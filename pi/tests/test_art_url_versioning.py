"""Tests for `_art_url_for_release` — the cache-busting helper that
appends `?v=<override_epoch>` to /art/<rid> when a user override exists,
so browser caches refresh after picks instead of serving stale art for
24h."""
from __future__ import annotations

from unittest.mock import patch

from nowplaying.art_overrides import Override
from nowplaying.orchestrator._publish_enrichment import _art_url_for_release


def test_unversioned_url_when_no_release_in_catalog() -> None:
    with patch(
        "nowplaying.orchestrator._publish_enrichment.discogs_catalog.rid_to_album",
        return_value=None,
    ):
        assert _art_url_for_release(12345) == "/art/12345"


def test_unversioned_url_when_no_override() -> None:
    with patch(
        "nowplaying.orchestrator._publish_enrichment.discogs_catalog.rid_to_album",
        return_value=("Hum", "Electra 2000"),
    ), patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        return_value=None,
    ):
        assert _art_url_for_release(12345) == "/art/12345"


def test_versioned_url_when_override_exists() -> None:
    ov = Override(
        key="abc",
        url="https://example/test.jpg",
        source="discogs-master",
        picked_at="2026-05-22T19:39:34Z",
        local_path="/tmp/x.jpg",
        content_type="image/jpeg",
        picked_at_epoch=1779478774,
    )
    with patch(
        "nowplaying.orchestrator._publish_enrichment.discogs_catalog.rid_to_album",
        return_value=("Hum", "Electra 2000"),
    ), patch(
        "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
        return_value=ov,
    ):
        assert _art_url_for_release(12345) == "/art/12345?v=1779478774"


def test_version_changes_with_override_epoch() -> None:
    """Picking new art for the same album should produce a different URL
    so the browser doesn't keep serving the stale image. Validates the
    happy-path identity that drives the whole fix."""
    def ov_with(epoch: int) -> Override:
        return Override(
            key="abc",
            url="https://example/test.jpg",
            source="discogs-master",
            picked_at="2026-05-22T19:39:34Z",
            local_path="/tmp/x.jpg",
            content_type="image/jpeg",
            picked_at_epoch=epoch,
        )

    with patch(
        "nowplaying.orchestrator._publish_enrichment.discogs_catalog.rid_to_album",
        return_value=("Hum", "Electra 2000"),
    ):
        with patch(
            "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
            return_value=ov_with(1779478774),
        ):
            first = _art_url_for_release(12345)
        with patch(
            "nowplaying.orchestrator._publish_enrichment.art_overrides.get",
            return_value=ov_with(1779999999),
        ):
            second = _art_url_for_release(12345)
        assert first != second
        assert first.endswith("?v=1779478774")
        assert second.endswith("?v=1779999999")
