"""Test fixtures.

Every test runs against SQLite + local storage + the inline queue, which
executes the *same* pipeline code as production — no mocks in the middle.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

WORKDIR = Path(tempfile.mkdtemp(prefix="lingoimage-tests-"))

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DEBUG": "true",
        "LOG_LEVEL": "WARNING",
        "SECRET_KEY": "test-secret-key-not-used-anywhere-else-0123456789abcdef",
        "DATABASE_URL": f"sqlite+pysqlite:///{WORKDIR}/test.sqlite3",
        "QUEUE_BACKEND": "inline",
        "STORAGE_BACKEND": "local",
        "LOCAL_STORAGE_PATH": str(WORKDIR / "storage"),
        "EMAIL_PROVIDER": "console",
        "TRANSLATION_PROVIDER_PRIORITY": "echo",
        "OCR_PROVIDER_PRIORITY": "rapidocr,tesseract",
        "RATE_LIMIT_ENABLED": "false",
        "BILLING_ENABLED": "false",
        "SESSION_COOKIE_SECURE": "false",
        "SEED_ENABLED": "true",
        "ANTIVIRUS_ENABLED": "false",
    }
)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    from lingoimage.db.base import Base
    from lingoimage.db.session import dispose_engine, get_engine

    Base.metadata.create_all(get_engine())
    yield
    dispose_engine()


@pytest.fixture
def session() -> Iterator[Session]:  # noqa: F821
    from lingoimage.db.session import session_scope

    with session_scope() as db:
        yield db


@pytest.fixture
def client() -> Iterator[TestClient]:  # noqa: F821
    """A fresh client (fresh cookie jar) per test.

    Deliberately constructed without the context manager: running the app
    lifespan per test would tear down the inline worker pool between the
    request that starts a job and the assertion that waits for it.
    """
    from fastapi.testclient import TestClient

    from lingoimage.main import app

    test_client = TestClient(app)
    yield test_client
    test_client.close()


@pytest.fixture
def sample_image_bytes() -> bytes:
    """A synthetic sign with three lines of text at different sizes."""
    from PIL import Image, ImageDraw

    from lingoimage.vision import fonts

    image = Image.new("RGB", (1000, 460), (250, 250, 246))
    draw = ImageDraw.Draw(image)
    draw.text(
        (60, 50), "Emergency Exit", font=fonts.load_font(size=48, bold=True), fill=(15, 15, 25)
    )
    draw.text(
        (60, 140),
        "Keep this door closed at all times",
        font=fonts.load_font(size=28),
        fill=(45, 45, 55),
    )
    draw.text(
        (60, 210),
        "Fire assembly point: Car park B",
        font=fonts.load_font(size=24),
        fill=(60, 60, 70),
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def sample_table_bytes() -> bytes:
    """A ruled table so table extraction has real grid lines to find."""
    from PIL import Image, ImageDraw

    from lingoimage.vision import fonts

    image = Image.new("RGB", (760, 320), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = fonts.load_font(size=22)

    rows = [
        ["Item", "Qty", "Price"],
        ["Coffee beans", "2", "14.50"],
        ["Paper filters", "10", "3.20"],
        ["Grinder", "1", "89.00"],
    ]
    left, top, row_height, col_widths = 40, 40, 60, [380, 120, 180]
    for index in range(len(rows) + 1):
        y = top + index * row_height
        draw.line([(left, y), (left + sum(col_widths), y)], fill=(30, 30, 30), width=2)
    x = left
    for width in [0, *col_widths]:
        x += width
        draw.line([(x, top), (x, top + len(rows) * row_height)], fill=(30, 30, 30), width=2)

    for row_index, row in enumerate(rows):
        x = left + 12
        for col_index, value in enumerate(row):
            draw.text((x, top + row_index * row_height + 18), value, font=font, fill=(10, 10, 10))
            x += col_widths[col_index]

    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def registered_client(client) -> TestClient:  # noqa: F821
    from lingoimage.core.ids import ulid

    email = f"user-{ulid()[:10].lower()}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ng!Passw0rd",
            "name": "Test User",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
    client.user_email = email  # type: ignore[attr-defined]
    return client


@pytest.fixture
def pro_client(registered_client) -> TestClient:  # noqa: F821
    """A client whose account is on the Pro plan (full export matrix)."""
    from lingoimage.db.session import session_scope
    from lingoimage.services import auth as auth_service
    from lingoimage.services import credits as credit_service

    with session_scope() as db:
        user = auth_service.find_user(db, registered_client.user_email)
        user.plan_code = "pro"
        credit_service.grant_monthly(db, user=user, amount=500, period="test")
    return registered_client
