"""SEO routing invariants: localised slugs must not create duplicate content."""

from __future__ import annotations

from picglot.domain import tools as tool_catalog
from picglot.domain.enums import ToolType


def test_localized_slug_replaces_the_english_one():
    """A locale with its own slug publishes only under it, never both."""
    photo = tool_catalog.BY_TYPE[ToolType.TRANSLATE_PHOTO]
    assert tool_catalog.slug_for(photo, "ru") == "perevod-po-foto"
    assert tool_catalog.slug_for(photo, "en") == "translate-photo"
    # A locale without an override falls back to the English slug.
    assert tool_catalog.slug_for(photo, "de") == "translate-photo"


def test_every_brief_slug_is_wired():
    """The slugs the brand brief commits to, mapped to the tool that serves them."""
    expected = {
        "perevod-po-foto": ToolType.TRANSLATE_PHOTO,
        "tekst-s-kartinki": ToolType.IMAGE_TO_TEXT,
        "foto-v-word": ToolType.JPG_TO_WORD,
        "foto-v-excel": ToolType.IMAGE_TO_EXCEL,
        "perevod-pdf": ToolType.PDF_TRANSLATOR,
    }
    actual = {
        slug: tool_type
        for tool_type, by_locale in tool_catalog.LOCALIZED_SLUGS.items()
        for slug in by_locale.values()
    }
    assert actual == expected


def test_localized_slugs_never_collide():
    """Two pages must never claim one path in the same locale."""
    localized = [
        slug for by_locale in tool_catalog.LOCALIZED_SLUGS.values() for slug in by_locale.values()
    ]
    assert len(localized) == len(set(localized)), "two tools claim the same localised slug"
    assert not (set(localized) & set(tool_catalog.BY_SLUG)), "collides with a tool slug"
    assert not (set(localized) & {slug for slug, *_ in tool_catalog.FORMAT_PAGES}), (
        "collides with a format page slug"
    )


def test_localized_slugs_are_url_safe():
    for by_locale in tool_catalog.LOCALIZED_SLUGS.values():
        for slug in by_locale.values():
            assert slug == slug.lower()
            assert " " not in slug
            assert slug.strip("-") == slug
            assert all(char.isalnum() or char == "-" for char in slug)


def test_config_exposes_overrides_only_where_they_exist():
    """Tools without an override report an empty map, so the client can default."""
    photo = tool_catalog.BY_TYPE[ToolType.TRANSLATE_PHOTO]
    assert tool_catalog.localized_slugs(photo) == {"ru": "perevod-po-foto"}

    screenshot = tool_catalog.BY_TYPE[ToolType.SCREENSHOT_TRANSLATOR]
    assert tool_catalog.localized_slugs(screenshot) == {}


# --------------------------------------------------------------------------- #
# Deployment layout
# --------------------------------------------------------------------------- #
def test_config_roots_survive_a_flat_install_layout():
    """`config.py` must import from a deployed image, not just a checkout.

    In the image the package lives at /app/picglot/core/, which has no fourth
    ancestor. Indexing `parents[4]` blindly raised IndexError at import time and
    every container — api, workers, migrate — died before doing any work.
    """
    from pathlib import PurePosixPath

    def roots(path: str) -> tuple[str, str]:
        parents = PurePosixPath(path).parents
        api_root = parents[2] if len(parents) > 2 else parents[-1]
        repo_root = parents[4] if len(parents) > 4 else api_root
        return str(api_root), str(repo_root)

    # Deployed image: both roots collapse to the application directory.
    assert roots("/app/picglot/core/config.py") == ("/app", "/app")
    # Source checkout: the repository root is four levels above the package.
    assert roots("/src/apps/api/picglot/core/config.py") == ("/src/apps/api", "/src")


def test_config_module_exposes_usable_roots():
    from picglot.core.config import API_ROOT, REPO_ROOT

    assert API_ROOT.is_absolute()
    assert REPO_ROOT.is_absolute()
    # A relative storage path is resolved against REPO_ROOT, so it must never
    # collapse to the filesystem root.
    assert str(REPO_ROOT) != "/"


def test_loopback_host_is_accepted_in_production(monkeypatch):
    """The container health check addresses the app as 127.0.0.1.

    TrustedHostMiddleware answers 400 to any Host it does not know, so leaving
    the loopback address out meant the probe failed against a perfectly healthy
    process and the container sat 'unhealthy' forever.
    """
    from fastapi.testclient import TestClient

    from picglot.core.config import settings

    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    from picglot.main import create_app

    client = TestClient(create_app())
    for host in ("127.0.0.1", "localhost", settings.brand_domain):
        response = client.get("/health/live", headers={"Host": host})
        assert response.status_code == 200, f"{host} rejected: {response.status_code}"

    assert client.get("/health/live", headers={"Host": "evil.example"}).status_code == 400
