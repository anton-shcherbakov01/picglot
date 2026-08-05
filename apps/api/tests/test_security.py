"""Security behaviour: authorisation, uploads, webhooks, tokens, SSRF."""

from __future__ import annotations

import time

import pytest

from picglot.core.errors import AppError, ErrorCode
from picglot.core.security import (
    hash_password,
    hash_token,
    sign_webhook,
    validate_password_strength,
    verify_password,
    verify_webhook_signature,
)
from picglot.services import files as file_service
from picglot.workers import dispatch


# --------------------------------------------------------------------------- #
# Authorisation
# --------------------------------------------------------------------------- #
def test_a_user_cannot_read_another_users_project(client, sample_image_bytes):
    from fastapi.testclient import TestClient

    from picglot.main import app

    owner = client
    response = owner.post(
        "/api/v1/auth/register",
        json={
            "email": "owner@idor.example.com",
            "password": "Str0ng!Passw0rd",
            "accept_terms": True,
        },
    )
    owner.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
    created = owner.post(
        "/api/v1/process",
        files={"file": ("a.png", sample_image_bytes, "image/png")},
        data={"options": '{"tool":"image-to-text"}'},
    ).json()
    dispatch.wait_for(created["id"], timeout=300)
    project_id = created["project_id"]

    attacker = TestClient(app)
    try:
        response = attacker.post(
            "/api/v1/auth/register",
            json={
                "email": "attacker@idor.example.com",
                "password": "Str0ng!Passw0rd",
                "accept_terms": True,
            },
        )
        attacker.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})

        # 404, not 403 — existence of another account's project is not disclosed.
        assert attacker.get(f"/api/v1/projects/{project_id}").status_code == 404
        assert attacker.delete(f"/api/v1/projects/{project_id}").status_code == 404
        assert (
            attacker.post(
                f"/api/v1/projects/{project_id}/exports", json={"format": "txt"}
            ).status_code
            == 404
        )
    finally:
        attacker.close()


def test_protected_endpoints_reject_anonymous_callers(client):
    for method, path in [
        ("get", "/api/v1/account/wallet"),
        ("get", "/api/v1/account/api-keys"),
        ("get", "/api/v1/auth/me"),
        ("get", "/api/v1/admin/dashboard"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code in {401, 403}, f"{path} -> {response.status_code}"


def test_non_admin_cannot_reach_the_admin_api(registered_client):
    assert registered_client.get("/api/v1/admin/dashboard").status_code == 403
    assert registered_client.get("/api/v1/admin/users").status_code == 403


def test_invalid_api_key_is_rejected(client):
    response = client.get(
        "/api/v1/account/wallet", headers={"Authorization": "Bearer lik_live_totally_made_up"}
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #
def test_magic_bytes_beat_the_declared_extension():
    with pytest.raises(AppError) as excinfo:
        file_service.identify(b"GIF89a" + b"\x00" * 32, filename="photo.png")
    # A real GIF is fine; the point is the type comes from content...
    assert excinfo.value.code in {ErrorCode.CORRUPTED_FILE, ErrorCode.UNSUPPORTED_FILE_TYPE}

    with pytest.raises(AppError) as excinfo:
        file_service.identify(b"#!/bin/sh\nrm -rf /", filename="image.png")
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_FILE_TYPE


def test_executable_extensions_are_refused_outright():
    with pytest.raises(AppError) as excinfo:
        file_service.identify(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, filename="evil.exe")
    assert excinfo.value.code is ErrorCode.UNSUPPORTED_FILE_TYPE


@pytest.mark.parametrize(
    "raw,expected_absent",
    [
        ("../../../etc/passwd", ".."),
        ("C:\\Windows\\System32\\cmd.exe", "\\"),
        ("file:stream.png", ":"),
        ("photo\u202egnp.exe", "\u202e"),
    ],
)
def test_filenames_are_sanitised(raw, expected_absent):
    safe = file_service.safe_filename(raw)
    assert expected_absent not in safe
    assert "/" not in safe


def test_oversized_images_are_rejected_before_decoding():
    import io

    from PIL import Image

    from picglot.core.config import settings

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (255, 255, 255)).save(buffer, "PNG")

    original = settings.max_image_pixels
    try:
        settings.max_image_pixels = 1000  # 200×200 = 40 000 pixels
        with pytest.raises(AppError) as excinfo:
            file_service.identify(buffer.getvalue(), filename="big.png")
        assert excinfo.value.code is ErrorCode.IMAGE_TOO_LARGE
    finally:
        settings.max_image_pixels = original


def test_pdf_javascript_is_stripped_on_upload():
    import fitz

    from picglot.vision import pdf as pdf_tools

    document = fitz.open()
    document.new_page()
    document.set_metadata({"title": "with js"})
    raw = document.tobytes()
    document.close()

    # Splice an OpenAction into the trailer the crude way a real attacker would.
    hostile = raw.replace(
        b"/Type/Catalog", b"/Type/Catalog/OpenAction<</S/JavaScript/JS(app.alert\\(1\\))>>"
    )
    cleaned = pdf_tools.sanitize(hostile)
    assert b"/JavaScript" not in cleaned
    assert b"/OpenAction" not in cleaned


# --------------------------------------------------------------------------- #
# Passwords and tokens
# --------------------------------------------------------------------------- #
def test_passwords_are_hashed_not_stored():
    hashed = hash_password("Str0ng!Passw0rd")
    assert "Str0ng!Passw0rd" not in hashed
    assert hashed.startswith("$argon2")
    assert verify_password("Str0ng!Passw0rd", hashed)
    assert not verify_password("wrong", hashed)


def test_verifying_against_a_missing_hash_is_safe():
    assert verify_password("anything", None) is False


@pytest.mark.parametrize(
    "password,ok",
    [
        ("short1!", False),
        ("password", False),
        ("alllowercaseletters", False),
        ("Str0ng!Passw0rd", True),
        ("C0rrect-Horse-Battery", True),
    ],
)
def test_password_strength_rules(password, ok):
    assert validate_password_strength(password).ok is ok


def test_tokens_are_stored_only_as_hashes():
    token = "lik_live_example_secret_value"
    hashed = hash_token(token)
    assert token not in hashed
    assert len(hashed) == 64
    assert hash_token(token) == hashed, "hashing must be deterministic for lookups"


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #
def test_webhook_signature_round_trip():
    body = b'{"event":"job.completed"}'
    now = int(time.time())
    header = sign_webhook("whsec_test", now, body)
    assert verify_webhook_signature("whsec_test", header, body, now=now)


def test_forged_and_replayed_webhooks_are_rejected():
    body = b'{"event":"job.completed"}'
    now = int(time.time())
    header = sign_webhook("whsec_test", now, body)

    assert not verify_webhook_signature("whsec_other", header, body, now=now)
    assert not verify_webhook_signature("whsec_test", header, b'{"event":"tampered"}', now=now)
    # Same signature, far in the future -> outside the tolerance window.
    assert not verify_webhook_signature("whsec_test", header, body, now=now + 4000)
    assert not verify_webhook_signature("whsec_test", None, body, now=now)


def test_webhook_urls_pointing_at_internal_hosts_are_refused():
    from picglot.services import webhooks

    for url in [
        "http://localhost:8000/hook",
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/hook",
    ]:
        with pytest.raises(AppError):
            webhooks.validate_url(url)


# --------------------------------------------------------------------------- #
# Error hygiene
# --------------------------------------------------------------------------- #
def test_errors_never_leak_internal_detail_to_clients(client):
    response = client.get("/api/v1/projects/prj_does_not_exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) <= {"code", "message", "retryable", "details", "request_id"}
    assert "Traceback" not in response.text
    assert "sqlalchemy" not in response.text.lower()


def test_every_response_carries_a_request_id(client):
    response = client.get("/health/live")
    assert response.headers.get("X-Request-ID", "").startswith("req_")


def test_security_headers_are_present(client):
    headers = client.get("/health/live").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in headers
    assert "Content-Security-Policy" in headers


@pytest.mark.parametrize(
    "endpoint,web_url,reachable",
    [
        # The Compose default: resolves inside the network and nowhere else.
        ("http://minio:9000", "https://picglot.ru", False),
        # Loopback is fine when the page came from the same machine — that is
        # the whole of local development — and useless otherwise.
        ("http://localhost:9000", "http://localhost:3000", True),
        ("http://localhost:9000", "https://picglot.ru", False),
        # Reachable host, but plain http under an https page — blocked as mixed
        # content before the request is even made.
        ("http://s3.picglot.ru", "https://picglot.ru", False),
        ("https://s3.picglot.ru", "https://picglot.ru", True),
    ],
)
def test_storage_urls_are_only_handed_out_when_a_browser_can_open_them(
    endpoint, web_url, reachable
):
    """A signed URL a visitor cannot fetch is worse than no URL at all.

    It fails silently: the upload works, the text is recognised, and only the
    picture is missing — with nothing in the server logs to say why.
    """
    from picglot.core.config import settings

    original = (settings.s3_public_endpoint_url, settings.public_web_url, settings.storage_backend)
    try:
        settings.s3_public_endpoint_url = endpoint
        settings.public_web_url = web_url
        settings.storage_backend = "s3"
        assert settings.storage_endpoint_reachable_by_browser is reachable
        assert settings.serve_files_through_api is not reachable
    finally:
        (
            settings.s3_public_endpoint_url,
            settings.public_web_url,
            settings.storage_backend,
        ) = original


def test_api_served_file_links_never_point_at_localhost():
    """The fallback must not swap one unopenable link for another.

    `PUBLIC_API_URL` defaults to localhost. Prefixing that onto the URLs we fall
    back to when storage is unreachable would reproduce the original failure
    one layer down, so the link is emitted as a path instead and resolves
    against the origin the page came from.
    """
    from picglot.core.config import settings
    from picglot.services.storage import api_download_url

    original = (settings.public_api_url, settings.public_web_url)
    try:
        settings.public_web_url = "https://picglot.ru"

        settings.public_api_url = "http://localhost:8000"
        assert api_download_url("guest/page.png").startswith("/api/v1/files/")

        settings.public_api_url = "https://picglot.ru"
        assert api_download_url("guest/page.png").startswith("https://picglot.ru/api/v1/files/")
    finally:
        settings.public_api_url, settings.public_web_url = original


def test_a_storage_endpoint_that_does_not_answer_falls_back_to_the_api():
    """A well-formed address proves nothing about whether it resolves.

    `https://s3.example.com` passes every shape check whether or not it has a
    DNS record, a certificate covering that name, or a vhost behind it — and
    all three failures look the same to a visitor: no picture, nothing logged.
    """
    from picglot.core.config import settings
    from picglot.services import storage as storage_service

    original = (
        settings.storage_backend,
        settings.s3_public_endpoint_url,
        settings.public_web_url,
        settings.public_api_url,
    )
    try:
        settings.storage_backend = "s3"
        settings.s3_public_endpoint_url = "https://s3.example.com"
        settings.public_web_url = "https://example.com"
        settings.public_api_url = "https://example.com"
        # The address itself is unimpeachable; only asking reveals the problem.
        assert settings.storage_endpoint_reachable_by_browser is True

        backend = storage_service.S3Storage()
        storage_service._endpoint_probe = None
        backend._probe_public_endpoint = lambda: (False, "no answer")  # type: ignore[method-assign]

        url = backend.signed_download_url("guest/page.png")
        assert url.startswith("https://example.com/api/v1/files/")

        # And once it does answer, links go straight to the store again.
        storage_service._endpoint_probe = None
        backend._probe_public_endpoint = lambda: (True, "")  # type: ignore[method-assign]
        assert backend.signed_download_url("guest/page.png").startswith("https://s3.example.com/")
    finally:
        storage_service._endpoint_probe = None
        (
            settings.storage_backend,
            settings.s3_public_endpoint_url,
            settings.public_web_url,
            settings.public_api_url,
        ) = original


@pytest.mark.parametrize(
    "status,usable",
    [
        # The object was just written, so this is the only answer that proves a
        # visitor can fetch it.
        (200, True),
        # Reached, and refusing our signature. A browser gets the same 403 and
        # shows an empty frame — the exact failure the probe exists to catch,
        # and the one the old "any status under 500" rule called healthy.
        (403, False),
        (401, False),
        # Something answers, but not from the bucket the API writes to.
        (404, False),
        (503, False),
    ],
)
def test_the_probe_only_trusts_an_endpoint_that_returns_the_object(status, usable, monkeypatch):
    """Reaching the store is not the same as being able to fetch from it.

    A reverse proxy that does not pass the original `Host` through reaches the
    store perfectly and rejects every presigned URL, because SigV4 signs the
    host. DNS, TLS and routing are all fine; every picture is still blank.
    """
    import httpx

    from picglot.core.config import settings
    from picglot.services import storage as storage_service

    original = (settings.storage_backend, settings.s3_public_endpoint_url)
    try:
        settings.storage_backend = "s3"
        settings.s3_public_endpoint_url = "https://s3.example.com"
        storage_service._endpoint_probe = None

        written: list[str] = []

        class FakeClient:
            def put_object(self, **kwargs):
                written.append(kwargs["Key"])

            def generate_presigned_url(self, *args, **kwargs):
                return "https://s3.example.com/picglot/.reachability-probe?X-Amz-Signature=x"

        backend = storage_service.S3Storage()
        backend._client = FakeClient()
        backend._public_client = FakeClient()
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *args, **kwargs: httpx.Response(status, text="denied"),
        )

        assert backend.public_endpoint_verdict()[0] is usable
        # The probe reads back an object it wrote, so a 404 is a real fault
        # rather than the expected answer it used to be.
        assert written == [storage_service.PROBE_KEY]
        if not usable:
            assert backend.public_endpoint_verdict()[1]
    finally:
        storage_service._endpoint_probe = None
        settings.storage_backend, settings.s3_public_endpoint_url = original
