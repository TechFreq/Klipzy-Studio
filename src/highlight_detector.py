"""Compatibility re-export shim for the legacy `src.highlight_detector` import path."""

from server.core.highlight_detector import HighlightDetector  # noqa: F401

__all__ = ["HighlightDetector"]