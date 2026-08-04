"""End-to-end pipeline: upload → OCR → layout → translate → render → export."""

from __future__ import annotations

import fitz
import pytest

from picglot.workers import dispatch


def _process(client, image_bytes: bytes, options: str) -> dict:
    response = client.post(
        "/api/v1/process",
        files={"file": ("sign.png", image_bytes, "image/png")},
        data={"options": options},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    dispatch.wait_for(job["id"], timeout=300)
    return client.get(f"/api/v1/jobs/{job['id']}").json()


def test_guest_can_process_an_image_without_registering(client, sample_image_bytes):
    job = _process(client, sample_image_bytes, '{"tool":"image-to-text","translate":false}')
    assert job["status"] == "completed", job.get("error")
    assert job["pages_completed"] == 1

    project = client.get(f"/api/v1/projects/{job['project_id']}").json()
    regions = project["pages"][0]["regions"]
    assert len(regions) >= 3

    recognised = " ".join(region["normalized_text"] for region in regions).lower()
    assert "emergency" in recognised
    assert "door" in recognised
    assert all(region["confidence"] > 0.5 for region in regions)


def test_translation_is_applied_and_rendered(client, sample_image_bytes):
    job = _process(
        client,
        sample_image_bytes,
        '{"tool":"image-translator","target_language":"ru","translate":true}',
    )
    assert job["status"] == "completed", job.get("error")

    page = client.get(f"/api/v1/projects/{job['project_id']}").json()["pages"][0]
    assert page["rendered_url"], "a rendered image should be produced"
    assert all(region["translated_text"] for region in page["regions"])
    # The echo provider prefixes the target language, proving the real path ran.
    assert all(region["translated_text"].startswith("[ru]") for region in page["regions"])


def test_quality_does_not_report_translated_blocks_as_untranslated(client, sample_image_bytes):
    """Every block was translated, so coverage must not be reported as a problem.

    The report used to re-read the translations through the ORM, which returned
    nothing — the session does not expire on commit — and every finished job
    told the user that none of its blocks had been translated.
    """
    job = _process(
        client,
        sample_image_bytes,
        '{"tool":"image-translator","target_language":"ru","translate":true}',
    )
    project = client.get(f"/api/v1/projects/{job['project_id']}").json()

    reasons = {reason["key"]: reason for reason in project["quality_reasons"]}
    assert "translation_coverage" not in reasons, reasons.get("translation_coverage")
    assert reasons.get("language_detection", {"score": 1.0})["score"] > 0.0


def test_signed_page_urls_are_actually_servable(client, sample_image_bytes):
    """Every asset URL handed to the browser must resolve to its bytes.

    The local backend signs URLs pointing at `/api/v1/files/local/...`, a route
    that did not exist: each one answered 404, so a self-hosted install with
    local storage rendered pages the editor could never display.
    """
    from urllib.parse import urlparse

    job = _process(client, sample_image_bytes, '{"tool":"image-to-text"}')
    page = client.get(f"/api/v1/projects/{job['project_id']}").json()["pages"][0]

    for field in ("original_url", "preview_url", "thumbnail_url"):
        url = page.get(field)
        assert url, f"{field} is missing"
        response = client.get(urlparse(url).path)
        assert response.status_code == 200, f"{field}: {response.text[:200]}"
        assert response.headers["content-type"].startswith("image/")
        assert len(response.content) > 0


def test_signed_file_token_is_required(client):
    assert client.get("/api/v1/files/not-a-real-token").status_code == 401


def test_progress_stream_delivers_events(client, sample_image_bytes):
    """The SSE endpoint used to raise inside the stream on its first read."""
    response = client.post(
        "/api/v1/process",
        files={"file": ("sign.png", sample_image_bytes, "image/png")},
        data={"options": '{"tool":"image-to-text","translate":false}'},
    )
    job_id = response.json()["id"]
    dispatch.wait_for(job_id, timeout=300)

    stream = client.get(f"/api/v1/jobs/{job_id}/events")
    assert stream.status_code == 200
    assert "event: done" in stream.text


def test_layout_analysis_identifies_a_heading(client, sample_image_bytes):
    job = _process(client, sample_image_bytes, '{"tool":"image-to-text"}')
    page = client.get(f"/api/v1/projects/{job['project_id']}").json()["pages"][0]
    types = {region["region_type"] for region in page["regions"]}
    assert "heading" in types, f"expected a heading among {types}"

    orders = [region["reading_order"] for region in page["regions"]]
    assert orders == sorted(orders), "regions must be returned in reading order"


def test_quality_score_is_explainable(client, sample_image_bytes):
    job = _process(client, sample_image_bytes, '{"tool":"image-to-text"}')
    project = client.get(f"/api/v1/projects/{job['project_id']}").json()

    assert project["quality_score"] is not None
    assert project["quality_band"] in {"high", "medium", "review_recommended", "low"}
    # Every reported reason must name a factor and carry a score.
    for reason in project["quality_reasons"]:
        assert reason["key"]
        assert 0.0 <= reason["score"] <= 1.0


@pytest.mark.parametrize(
    "fmt,sniff",
    [
        ("txt", b""),
        ("json", b"{"),
        ("png", b"\x89PNG"),
        ("pdf", b"%PDF-"),
        ("docx", b"PK"),
        ("xlsx", b"PK"),
        ("csv", b"\xef\xbb\xbf"),
        ("md", b"#"),
    ],
)
def test_every_export_format_produces_a_valid_file(
    pro_client, sample_image_bytes, fmt, sniff, session
):
    job = _process(pro_client, sample_image_bytes, '{"tool":"image-to-text"}')
    project_id = job["project_id"]

    response = pro_client.post(f"/api/v1/projects/{project_id}/exports", json={"format": fmt})
    assert response.status_code == 201, response.text
    export = response.json()
    assert export["byte_size"] > 0

    data = _export_bytes(export["id"])
    assert data.startswith(sniff) or not sniff


def test_searchable_pdf_contains_a_real_text_layer(pro_client, sample_image_bytes):
    job = _process(pro_client, sample_image_bytes, '{"tool":"pdf-ocr"}')
    response = pro_client.post(
        f"/api/v1/projects/{job['project_id']}/exports",
        json={"format": "pdf_searchable", "content": "source"},
    )
    assert response.status_code == 201, response.text

    document = fitz.open(stream=_export_bytes(response.json()["id"]), filetype="pdf")
    text = document[0].get_text("text")
    document.close()

    assert "Emergency" in text, f"invisible text layer missing: {text!r}"


def test_reexporting_identical_settings_reuses_the_first_export(pro_client, sample_image_bytes):
    job = _process(pro_client, sample_image_bytes, '{"tool":"image-to-text"}')
    project_id = job["project_id"]

    first = pro_client.post(f"/api/v1/projects/{project_id}/exports", json={"format": "txt"}).json()
    second = pro_client.post(
        f"/api/v1/projects/{project_id}/exports", json={"format": "txt"}
    ).json()
    assert first["id"] == second["id"], "a free re-export must not rebuild the file"


def test_table_extraction_produces_typed_cells(pro_client, sample_table_bytes):
    response = pro_client.post(
        "/api/v1/process",
        files={"file": ("table.png", sample_table_bytes, "image/png")},
        data={"options": '{"tool":"image-to-excel","translate":false}'},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    dispatch.wait_for(job_id, timeout=300)

    job = pro_client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed", job.get("error")

    page = pro_client.get(f"/api/v1/projects/{job['project_id']}").json()["pages"][0]
    assert page["tables"], "a ruled table should be detected"
    table = page["tables"][0]
    assert table["rows"] >= 3
    assert table["cols"] >= 2

    numeric = [cell for cell in table["cells"] if cell["value_type"] in {"number", "currency"}]
    assert numeric, "prices and quantities should be typed as numbers, not text"

    export = pro_client.post(
        f"/api/v1/projects/{job['project_id']}/exports", json={"format": "xlsx"}
    )
    assert export.status_code == 201, export.text


def test_unsupported_and_malicious_uploads_are_refused(client, sample_image_bytes):
    fake_mime = client.post(
        "/api/v1/process",
        files={"file": ("payload.png", b"MZ\x90\x00 not an image", "image/png")},
        data={"options": '{"tool":"image-to-text"}'},
    )
    assert fake_mime.status_code == 415
    assert fake_mime.json()["error"]["code"] == "unsupported_file_type"

    dangerous = client.post(
        "/api/v1/process",
        files={"file": ("../../etc/passwd.exe", sample_image_bytes, "image/png")},
        data={"options": '{"tool":"image-to-text"}'},
    )
    assert dangerous.status_code == 415


def _export_bytes(export_id: str) -> bytes:
    from picglot.db.models import Export
    from picglot.db.session import session_scope
    from picglot.services import projects as project_service
    from picglot.services import storage

    with session_scope() as db:
        export = db.get(Export, export_id)
        asset = project_service.get_asset(db, export.asset_id)
        return storage.get_storage().get(asset.storage_key)
