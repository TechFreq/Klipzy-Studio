"""
Face detection and speaker-aware tracking for 9:16 vertical smart crop.
Uses YOLO (ultralytics) with OpenCV. Cross-platform.
Heavy deps (cv2, numpy) are imported lazily so the server can boot without them.
"""

from typing import Optional, List
import os


class FaceTracker:
    def __init__(self, model_name: str = "yolov8n.pt"):
        self.model = None
        self.model_name = model_name

    def _init_model(self):
        if self.model is not None:
            return
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.model_name)
        except Exception:
            self.model = None

    def get_speaker_center_x(self, video_path: str, start_time: float, end_time: float) -> Optional[float]:
        """
        Samples frames to find the average horizontal center of detected people,
        returning the crop X offset for vertical 9:16 framing.

        Instead of decoding and discarding every frame between start and end
        (expensive on long clips), we jump straight to the sampled frame
        positions with CAP_PROP_POS_FRAMES + read. This cuts a 10s clip from
        ~300 frame decodes to ~2-10, making face tracking dramatically faster.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            # No OpenCV available - fall back to center crop
            return None

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        target_crop_width = int(height * (9 / 16))

        if target_crop_width >= width:
            cap.release()
            return 0  # Already vertical or square

        start_frame = int(start_time * fps)
        end_frame = int(end_time * fps)

        self._init_model()
        centers_x: List[float] = []
        # Sample about 1 frame/sec but cap on huge/dense selections so very
        # long clips don't hammer the detector. Also compute in a bounded range.
        total_frames = max(1, end_frame - start_frame)
        sample_count = min(12, max(3, total_frames // max(1, int(fps))))
        step = max(1, total_frames // sample_count)

        for frame_idx in range(sample_count):
            target = start_frame + frame_idx * step
            if target >= end_frame:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            if not ret:
                break
            if not self.model:
                continue
            results = self.model(frame, verbose=False, classes=[0])  # person
            boxes = results[0].boxes
            if len(boxes) > 0:
                # Largest box = main speaker
                box = sorted(
                    boxes,
                    key=lambda b: (b.xyxy[0][2] - b.xyxy[0][0]) * (b.xyxy[0][3] - b.xyxy[0][1]),
                    reverse=True,
                )[0]
                xyxy = box.xyxy[0].cpu().numpy()
                centers_x.append((xyxy[0] + xyxy[2]) / 2.0)

        cap.release()

        if not centers_x:
            return max(0, (width - target_crop_width) // 2)

        avg_center_x = float(np.median(centers_x))
        crop_x = int(avg_center_x - (target_crop_width / 2))
        crop_x = max(0, min(crop_x, width - target_crop_width))
        return crop_x