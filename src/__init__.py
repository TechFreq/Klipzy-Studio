"""
Compatibility package: `src.*` mirrors the real `server.core.*` modules.

Clippy Studio's canonical layout lives under `server/`. Early-era code (and some
external tooling) referenced the modules via `src.*`; these thin re-export shims
keep every import path resolvable.
"""

from server import (  # noqa: F401
    ClipCandidate,
    ClipResult,
    ProcessRequest,
    ProcessResponse,
    TranscriptSegment,
    WordTimestamp,
    ChatRequest,
    ChatResponse,
)

__all__ = [
    "ClipCandidate",
    "ClipResult",
    "ProcessRequest",
    "ProcessResponse",
    "TranscriptSegment",
    "WordTimestamp",
    "ChatRequest",
    "ChatResponse",
]