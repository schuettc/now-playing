"""Now Playing orchestrator package.

Split out of the historical ``nowplaying.main`` god file. The runnable
entry point ``python -m nowplaying.main`` still works — ``main.py``
remains a thin shim that re-exports this package's public surface and
delegates to :func:`bootstrap.main_async`.

This package's ``__init__`` runs two side-effects at import time:

  1. Inserts ``pi/scripts/`` on ``sys.path`` so ``recognize_proto``
     (the legacy script imported as a module) can be imported by every
     submodule that needs it.
  2. Re-exports the previously-importable names so ``from
     nowplaying.main import X`` continues to resolve for tests and any
     external consumer.

``Orchestrator`` is now composed from five mixin files: :mod:`._sonos_handlers`,
:mod:`._heartbeat_handlers`, :mod:`._prediction`, :mod:`._llm_hooks`, and
:mod:`._publish_enrichment`. :mod:`._class` retains only ``__init__`` and
``hygiene_loop``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/recognize_proto.py is imported as a module (it lives outside
# the package). Mirror the historical sys.path mutation that used to
# live at the top of main.py so submodules can ``import recognize_proto``.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PI_DIR = _REPO_ROOT / "pi"
_SCRIPTS_DIR = str(_PI_DIR / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Re-export the previously-importable public surface from main.py. Order
# matters where modules depend on each other (state before everything
# that types-against it; constants before helpers that reference them).
from nowplaying.orchestrator.state import State  # noqa: E402
from nowplaying.orchestrator.streaming_idle import (  # noqa: E402
    HEARTBEAT_INTERVAL_S,
    MAX_UNMATCHED_STREAK,
    MIN_FINGERPRINT_HITS_ANCHORED,
    MIN_FINGERPRINT_HITS_BLIND,
    MUSIC_DB,
    NEEDS_ID_STREAK,
    PREDICTED_ADVANCE_TOLERANCE_S,
    RECOGNITION_LEAD_S,
    STREAMING_IDLE_DELAY_S,
    STRONG_FINGERPRINT_ANCHOR_MULTIPLIER,
    VINYL_IDLE_DELAY_S,
    _evaluate_sticky_idle,
    _is_music_level,
    _should_arm_streaming_idle,
    _should_cancel_streaming_idle_on_resume,
    _should_pause_capture,
)
from nowplaying.orchestrator.prediction import (  # noqa: E402
    _advance_predicted_position,
    _build_predicted_payload,
)
from nowplaying.orchestrator.shazam_match import _shazam_disagrees_with_lock  # noqa: E402
from nowplaying.orchestrator.fingerprint import _build_fingerprint_payload  # noqa: E402
from nowplaying.orchestrator.advance import (  # noqa: E402
    _compute_advance_elapsed_s,
    _interpret_advance_verdict,
)
from nowplaying.orchestrator.pin import (  # noqa: E402
    MIN_PIN_TTL_S,
    PIN_DIFFERENT_TRACK_RELEASE_STREAK,
    PIN_SAFETY_MARGIN_S,
    PIN_TTL_BUFFER_S,
    _evaluate_user_pin,
    _fingerprint_anchor_ttl_expired,
    _pin_ttl_expired,
    compute_pin_duration,
)
from nowplaying.orchestrator.guess import (  # noqa: E402
    DISMISSED_GUESS_TTL_S,
    _guess_is_dismissed,
)
from nowplaying.orchestrator.payload import (  # noqa: E402
    SOURCE_MAP,
    _apply_sonos_anchor,
    _cached_art_url,
    sonos_to_payload,
)
from nowplaying.orchestrator.io_helpers import _read_bytes  # noqa: E402
from nowplaying.orchestrator._class import Orchestrator  # noqa: E402
from nowplaying.orchestrator.bootstrap import (  # noqa: E402
    PI_DIR,
    REPO_ROOT,
    _build_app,
    _init_optional_features,
    main_async,
)

# Catalog module re-exported because tests reach for it via
# ``patch.object(nowplaying.main.discogs_catalog, "get_release", ...)``.
from nowplaying.discogs import catalog as discogs_catalog  # noqa: E402

__all__ = [
    "DISMISSED_GUESS_TTL_S",
    "HEARTBEAT_INTERVAL_S",
    "MAX_UNMATCHED_STREAK",
    "MIN_FINGERPRINT_HITS_ANCHORED",
    "MIN_FINGERPRINT_HITS_BLIND",
    "NEEDS_ID_STREAK",
    "PREDICTED_ADVANCE_TOLERANCE_S",
    "Orchestrator",
    "MIN_PIN_TTL_S",
    "PIN_DIFFERENT_TRACK_RELEASE_STREAK",
    "PIN_SAFETY_MARGIN_S",
    "PIN_TTL_BUFFER_S",
    "PI_DIR",
    "RECOGNITION_LEAD_S",
    "REPO_ROOT",
    "MUSIC_DB",
    "STRONG_FINGERPRINT_ANCHOR_MULTIPLIER",
    "SOURCE_MAP",
    "STREAMING_IDLE_DELAY_S",
    "State",
    "VINYL_IDLE_DELAY_S",
    "_advance_predicted_position",
    "_apply_sonos_anchor",
    "_build_app",
    "_build_fingerprint_payload",
    "_build_predicted_payload",
    "_cached_art_url",
    "_compute_advance_elapsed_s",
    "_evaluate_sticky_idle",
    "_evaluate_user_pin",
    "_fingerprint_anchor_ttl_expired",
    "_guess_is_dismissed",
    "compute_pin_duration",
    "_init_optional_features",
    "_interpret_advance_verdict",
    "_is_music_level",
    "_pin_ttl_expired",
    "_read_bytes",
    "_shazam_disagrees_with_lock",
    "_should_arm_streaming_idle",
    "_should_cancel_streaming_idle_on_resume",
    "_should_pause_capture",
    "discogs_catalog",
    "main_async",
    "sonos_to_payload",
]
