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
