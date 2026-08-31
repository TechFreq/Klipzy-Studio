"""Compatibility re-export shim for the legacy `src.transcriber` import path."""

from server.core.transcriber import Transcriber  # noqa: F401

__all__ = ["Transcriber"]