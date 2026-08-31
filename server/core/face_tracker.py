"""
Face detection and speaker-aware tracking for 9:16 vertical, 1:1 square, and 4:5 portrait smart crop.
Uses YOLO (ultralytics) with OpenCV. Cross-platform.
Heavy deps (cv2, numpy) are imported lazily so the server can boot without them.
"""

from typing import Optional, List, Dict, Tuple
import os


def _models_dir() -> str:
    """Repo-root ``models/`` directory for ML weights (YOLO etc.).

    Resolved relative to this file (server/core/face_tracker.py -> repo root)
    so the weight loads from the same place no matter what working directory
    the server was launched from. Created on demand.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "models")
    os.makedirs(path, exist_ok=True)
    return path


class FaceTracker:
    def __init__(self, model_name: str = "yolov8n.pt"):
        self.model = None
        self.model_name = model_name

    def _resolve_model_path(self) -> str:
        """Absolute path to the weight inside ``models/``.

        If the user passed a full path that already exists, keep it. Otherwise
        pin the weight to ``models/`` so ultralytics downloads/loads it there
        instead of dropping it in the current working directory.
        """
        if os.path.isabs(self.model_name) and os.path.exists(self.model_name):
            return self.model_name
        return os.path.join(_models_dir(), os.path.basename(self.model_name))

    def _init_model(self):
        if self.model is not None:
            return
        try:
            from ultralytics import YOLO
            self.model = YOLO(self._resolve_model_path())
        except Exception:
            self.model = None

    # ------------------------------------------------------------------
    # Gaming / reaction layout auto-detection
    # ------------------------------------------------------------------
    @staticmethod
    def _corner_of(cx: float, cy: float, w: int, h: int) -> str:
        vert = "top" if cy < h / 2.0 else "bottom"
        horiz = "left" if cx < w / 2.0 else "right"
        return f"{vert}-{horiz}"

    @staticmethod
    def _facecam_candidate(boxes, w: int, h: int):
        """
        Given person boxes (x1,y1,x2,y2) in one frame, return the most likely
        webcam-inset as (corner, scale, area), or None. A facecam is small
        (< ~22% of frame), pushed toward a horizontal edge (not centered), and
        typically near the top or bottom — i.e. a corner inset over gameplay.
        """
        frame_area = float(w * h) or 1.0
        best = None
        for (x1, y1, x2, y2) in boxes:
            bw, bh = (x2 - x1), (y2 - y1)
            area = bw * bh
            frac = area / frame_area
            if frac <= 0.0 or frac >= 0.22:
                continue  # too big to be a webcam inset
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            # Must sit toward a side, not centered (a centered talking head is
            # a normal interview, not gameplay+cam).
            if w * 0.45 <= cx <= w * 0.55:
                continue
            corner = FaceTracker._corner_of(cx, cy, w, h)
            if best is None or area < best[2]:
                best = (corner, bw / float(w), area)
        return best

    def detect_gaming_layout(
        self,
        video_path: str,
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        sample_count: int = 12,
    ) -> Dict:
        """
        Heuristic: is this gameplay footage with a webcam facecam in a corner?
        Samples frames, detects people, and looks for a small person box that
        stays in the SAME corner across most frames. Returns:
          {is_gaming, cam_position, cam_scale, confidence}
        Fully local (YOLO + OpenCV); degrades to is_gaming=False on any problem.
        """
        result = {"is_gaming": False, "cam_position": None, "cam_scale": 0.32, "confidence": 0.0}
        try:
            import cv2
            import numpy as np
        except ImportError:
            return result
        if not os.path.exists(video_path):
            return result

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return result
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if w <= 0 or h <= 0:
            cap.release()
            return result

        s_frame = int(start_time * fps)
        e_frame = int(end_time * fps) if end_time else (total or int(start_time * fps) + int(fps * 60))
        span = max(1, e_frame - s_frame)
        step = max(1, span // sample_count)

        self._init_model()
        corners: Dict[str, int] = {}
        scales: List[float] = []
        frames_seen = 0

        for i in range(sample_count):
            target = s_frame + i * step
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            if not ret or not self.model:
                continue
            frames_seen += 1
            try:
                res = self.model(frame, verbose=False, classes=[0])
                boxes = []
                for b in res[0].boxes:
                    xyxy = b.xyxy[0].cpu().numpy()
                    boxes.append((float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])))
                cand = self._facecam_candidate(boxes, w, h)
                if cand:
                    corner, scale, _ = cand
                    corners[corner] = corners.get(corner, 0) + 1
                    scales.append(scale)
            except Exception:
                continue

        cap.release()
        if not corners or frames_seen == 0:
            return result

        top_corner = max(corners, key=corners.get)
        hits = corners[top_corner]
        confidence = hits / float(frames_seen)
        # Gaming if a small corner cam is present in most sampled frames.
        if confidence >= 0.5:
            import statistics
            result.update({
                "is_gaming": True,
                "cam_position": top_corner,
                "cam_scale": round(min(0.45, max(0.18, statistics.median(scales))), 2) if scales else 0.32,
                "confidence": round(confidence, 2),
            })
        else:
            result["confidence"] = round(confidence, 2)
        return result

    def _calc_target_crop_width(self, width: int, height: int, aspect_ratio: str = "9:16") -> int:
        if aspect_ratio == "9:16":
            ratio = 9.0 / 16.0
        elif aspect_ratio == "4:5":
            ratio = 4.0 / 5.0
        elif aspect_ratio == "1:1":
            ratio = 1.0
        elif aspect_ratio == "16:9":
            ratio = 16.0 / 9.0
        else:
            ratio = 9.0 / 16.0
        return int(height * ratio)


    @staticmethod
    def _face_region_motion(gray_a, gray_b, xyxy) -> float:
        """
        Mean per-pixel motion in the head region of a person box, comparing two
        nearby frames. Talking mouths + head motion light this up, so it's a
        cheap, fully-local proxy for "who is the active speaker" — no extra model.
        Returns 0.0 on any problem.
        """
        try:
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            h = y2 - y1
            # Head/face band = top ~45% of the person box (where the mouth is).
            fy1 = max(0, y1)
            fy2 = max(fy1 + 1, y1 + int(h * 0.45))
            fx1 = max(0, x1)
            fx2 = max(fx1 + 1, x2)
            ra = gray_a[fy1:fy2, fx1:fx2]
            rb = gray_b[fy1:fy2, fx1:fx2]
            if ra.size == 0 or ra.shape != rb.shape:
                return 0.0
            import numpy as np
            return float(np.mean(np.abs(ra.astype("int16") - rb.astype("int16"))))
        except Exception:
            return 0.0

    def get_speaker_center_x(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        aspect_ratio: str = "9:16",
    ) -> Optional[int]:
        """
        Find the optimal horizontal crop offset, biased toward the ACTIVE SPEAKER.

        For each sampled point we grab two adjacent frames, detect people, and
        measure head-region motion for each. When several people are on screen,
        the one whose face is moving (talking) wins the frame; if nobody is
        clearly moving, we fall back to the most prominent (largest) person —
        the previous behaviour. Picks are aggregated with a motion-weighted
        median so the crop tracks whoever speaks most across the clip.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None

        if not os.path.exists(video_path):
            return None

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # OpenCV's bundled decoder can't read every codec (notably HEVC/iPhone
        # .MOV on many builds); it then reports 0x0. Returning None lets the
        # renderer center-crop instead of slicing the left edge (crop_x=0).
        if width <= 0 or height <= 0:
            cap.release()
            return None

        target_crop_width = self._calc_target_crop_width(width, height, aspect_ratio)

        if target_crop_width >= width:
            cap.release()
            return 0

        start_frame = int(start_time * fps)
        end_frame = int(end_time * fps)

        self._init_model()
        centers_x: List[float] = []
        weights: List[float] = []

        total_frames = max(1, end_frame - start_frame)
        sample_count = min(16, max(4, total_frames // max(1, int(fps))))
        step = max(1, total_frames // sample_count)

        for frame_idx in range(sample_count):
            target = start_frame + frame_idx * step
            if target >= end_frame:
                break
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ret, frame = cap.read()
            if not ret:
                break
            # Second frame a beat later for the motion diff (talking cadence).
            ret2, frame2 = cap.read()
            if not self.model:
                continue

            try:
                results = self.model(frame, verbose=False, classes=[0])
                boxes = results[0].boxes
                if len(boxes) == 0:
                    continue

                gray_a = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if ret2 else None
                gray_b = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY) if ret2 else None

                scored = []  # (area, center_x, motion)
                for b in boxes:
                    xyxy = b.xyxy[0].cpu().numpy()
                    area = float((xyxy[2] - xyxy[0]) * (xyxy[3] - xyxy[1]))
                    center_x = float((xyxy[0] + xyxy[2]) / 2.0)
                    motion = (
                        self._face_region_motion(gray_a, gray_b, xyxy)
                        if gray_a is not None else 0.0
                    )
                    scored.append((area, center_x, motion))

                scored.sort(key=lambda s: s[0], reverse=True)  # largest first
                top_area, top_center, _ = scored[0]
                pick_center, pick_weight = top_center, top_area

                if len(scored) >= 2:
                    # Consider only reasonably-sized people as speaker candidates.
                    big = [s for s in scored if s[0] >= 0.4 * top_area]
                    motions = [s[2] for s in big]
                    max_motion = max(motions) if motions else 0.0
                    second_motion = sorted(motions, reverse=True)[1] if len(motions) > 1 else 0.0
                    # A clear motion winner = the talker. "Clear" = meaningfully
                    # more head motion than the next person.
                    if max_motion >= 1.5 and max_motion >= 1.3 * max(second_motion, 0.01):
                        talker = max(big, key=lambda s: s[2])
                        pick_center = talker[1]
                        pick_weight = talker[0] * (1.0 + talker[2])
                    else:
                        # No clear talker: if two similar people are close together,
                        # frame both (previous behaviour); else keep the largest.
                        second_area, second_center, _ = scored[1]
                        if second_area >= 0.55 * top_area and abs(top_center - second_center) <= target_crop_width * 0.85:
                            pick_center = (top_center + second_center) / 2.0

                centers_x.append(pick_center)
                weights.append(pick_weight)
            except Exception:
                continue

        cap.release()

        if not centers_x:
            return max(0, (width - target_crop_width) // 2)

        try:
            arr_centers = np.array(centers_x)
            arr_weights = np.array(weights)
            sorted_indices = np.argsort(arr_centers)
            sorted_centers = arr_centers[sorted_indices]
            sorted_weights = arr_weights[sorted_indices]
            cum_weights = np.cumsum(sorted_weights)
            cutoff = cum_weights[-1] / 2.0
            median_idx = np.where(cum_weights >= cutoff)[0][0]
            avg_center_x = float(sorted_centers[median_idx])
        except Exception:
            avg_center_x = float(np.median(centers_x))

        crop_x = int(round(avg_center_x - (target_crop_width / 2.0)))
        crop_x = max(0, min(crop_x, width - target_crop_width))
        return crop_x
    def get_speaker_trajectory(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        aspect_ratio: str = "9:16",
        sample_fps: float = 2.0,
    ) -> List[Dict[str, float]]:
        """
        Calculates a smoothed time-series trajectory of crop X offsets.
        """
        try:
            import cv2
            import numpy as np
        except ImportError:
            return []

        if not os.path.exists(video_path):
            return []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        target_crop_width = self._calc_target_crop_width(width, height, aspect_ratio)

        if target_crop_width >= width:
            cap.release()
            return [{"timestamp": start_time, "crop_x": 0, "center_x": width / 2.0}]

        duration = max(0.1, end_time - start_time)
        samples = max(2, int(duration * sample_fps))
        time_step = duration / samples

        self._init_model()
        raw_trajectory: List[Tuple[float, float]] = []

        for i in range(samples):
            t = start_time + i * time_step
            frame_no = int(t * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ret, frame = cap.read()
            if not ret or not self.model:
                raw_trajectory.append((t, width / 2.0))
                continue

            try:
                results = self.model(frame, verbose=False, classes=[0])
                boxes = results[0].boxes
                if len(boxes) > 0:
                    best_box = max(
                        boxes,
                        key=lambda b: (b.xyxy[0][2] - b.xyxy[0][0]) * (b.xyxy[0][3] - b.xyxy[0][1]),
                    )
                    xyxy = best_box.xyxy[0].cpu().numpy()
                    cx = float((xyxy[0] + xyxy[2]) / 2.0)
                    raw_trajectory.append((t, cx))
                else:
                    prev = raw_trajectory[-1][1] if raw_trajectory else (width / 2.0)
                    raw_trajectory.append((t, prev))
            except Exception:
                prev = raw_trajectory[-1][1] if raw_trajectory else (width / 2.0)
                raw_trajectory.append((t, prev))

        cap.release()

        smoothed: List[Dict[str, float]] = []
        alpha = 0.35
        current_cx = raw_trajectory[0][1] if raw_trajectory else (width / 2.0)

        for t, raw_cx in raw_trajectory:
            current_cx = alpha * raw_cx + (1.0 - alpha) * current_cx
            crop_x = int(round(current_cx - (target_crop_width / 2.0)))
            crop_x = max(0, min(crop_x, width - target_crop_width))
            smoothed.append({
                "timestamp": round(t, 2),
                "crop_x": crop_x,
                "center_x": round(current_cx, 1),
            })

        return smoothed

