"""Compatibility re-export shim for the legacy `src.pipeline` import path."""

from server.core.pipeline import VideoClipperEngine  # noqa: F401

__all__ = ["VideoClipperEngine"]