"""Compatibility re-export shim for the legacy `src.face_tracker` import path."""

from server.core.face_tracker import FaceTracker  # noqa: F401

__all__ = ["FaceTracker"]