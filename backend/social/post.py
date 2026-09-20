"""The composed-post value object, in its own module so composer.py and
franchises.py can both import it without a cycle."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Post:
    """One composed post ready to render + publish.

    `card` is a renderer SPEC (see backend/social/card.py::render) — a dict, not
    pixels — so a franchise carries only data. `text` is the tweet body (no
    link; the link, if any, lives in `reply`). `dedup_key` (date + kind) makes a
    re-fire idempotent."""
    kind: str
    text: str
    alt_text: str
    card: dict
    reply: str | None = None
    dedup_key: str = ""
    meta: dict = field(default_factory=dict)  # free-form (for tests/telemetry)
