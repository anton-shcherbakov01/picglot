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
