"""
Export integrations for NLE editors:
- Adobe Premiere Pro / DaVinci Resolve (Final Cut Pro XML / FCPXML)
- Edit Decision List (EDL)
- CapCut Draft Project format
"""

import os
import json
from pathlib import Path
from typing import List, Dict, Any


def export_fcpxml(
    video_path: str,
    clips: List[Dict[str, Any]],
    output_xml_path: str,
    fps: float = 30.0
) -> str:
    """
    Exports clip cuts into an Apple FCPXML / Premiere Pro XML format.
    Compatible with Adobe Premiere Pro, DaVinci Resolve, and Final Cut Pro.
    """
    video_name = Path(video_path).name
    video_abs = Path(video_path).resolve().as_uri()

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE xmeml>',
        '<xmeml version="4">',
        '  <sequence>',
        f'    <name>{Path(video_path).stem}_Shorts_Timeline</name>',
        '    <rate>',
        f'      <timebase>{int(fps)}</timebase>',
        '      <ntsc>FALSE</ntsc>',
        '    </rate>',
        '    <media>',
        '      <video>',
        '        <format>',
        '          <samplecharacteristics>',
        '            <width>1080</width>',
        '            <height>1920</height>',
        '          </samplecharacteristics>',
        '        </format>',
        '        <track>'
    ]

    current_timeline_frame = 0
    for idx, clip in enumerate(clips, start=1):
        in_frame = int(clip["start_time"] * fps)
        out_frame = int(clip["end_time"] * fps)
        duration_frames = out_frame - in_frame

        xml_lines.extend([
            f'          <clipitem id="clipitem_{idx}">',
            f'            <name>{clip.get("title", f"Clip {idx}")}</name>',
            f'            <duration>{duration_frames}</duration>',
            '            <rate>',
            f'              <timebase>{int(fps)}</timebase>',
            '            </rate>',
            f'            <start>{current_timeline_frame}</start>',
            f'            <end>{current_timeline_frame + duration_frames}</end>',
            f'            <in>{in_frame}</in>',
            f'            <out>{out_frame}</out>',
            '            <file id="file_1">',
            f'              <name>{video_name}</name>',
            f'              <pathurl>{video_abs}</pathurl>',
            '              <rate>',
            f'                <timebase>{int(fps)}</timebase>',
            '              </rate>',
            '            </file>',
            '          </clipitem>'
        ])
        current_timeline_frame += duration_frames

    xml_lines.extend([
        '        </track>',
        '      </video>',
        '    </media>',
        '  </sequence>',
        '</xmeml>'
    ])

    Path(output_xml_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_lines))

    return output_xml_path


def export_edl(
    video_path: str,
    clips: List[Dict[str, Any]],
    output_edl_path: str,
    fps: float = 30.0
) -> str:
    """
    Exports an EDL (Edit Decision List) for DaVinci Resolve and Adobe Premiere.
    """
    def frames_to_tc(frames: float, fps_rate: float) -> str:
        fps_int = int(round(fps_rate))
        h = int(frames // (fps_int * 3600))
        m = int((frames % (fps_int * 3600)) // (fps_int * 60))
        s = int((frames % (fps_int * 60)) // fps_int)
        f = int(frames % fps_int)
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    reel = "AX"
    edl_lines = [
        f"TITLE: {Path(video_path).stem}_Shorts",
        "FCM: NON-DROP FRAME",
        ""
    ]

    record_frame = 0
    for idx, clip in enumerate(clips, start=1):
        src_in = int(clip["start_time"] * fps)
        src_out = int(clip["end_time"] * fps)
        dur = src_out - src_in
        rec_in = record_frame
        rec_out = record_frame + dur

        line = (
            f"{idx:03d}  {reel:<8} V     C        "
            f"{frames_to_tc(src_in, fps)} {frames_to_tc(src_out, fps)} "
            f"{frames_to_tc(rec_in, fps)} {frames_to_tc(rec_out, fps)}"
        )
        edl_lines.append(line)
        edl_lines.append(f"* FROM CLIP NAME: {Path(video_path).name}")
        edl_lines.append("")
        record_frame = rec_out

    Path(output_edl_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_edl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(edl_lines))

    return output_edl_path


def export_capcut_draft(
    video_path: str,
    clips: List[Dict[str, Any]],
    output_json_path: str
) -> str:
    """
    Generates a CapCut-compatible timeline structure JSON for easy import.
    """
    draft = {
        "platform": "CapCut",
        "video_source": os.path.abspath(video_path),
        "canvas_config": {
            "width": 1080,
            "height": 1920,
            "ratio": "9:16"
        },
        "tracks": [
            {
                "type": "video",
                "segments": [
                    {
                        "id": clip.get("clip_id", f"clip_{i}"),
                        "name": clip.get("title", f"Clip {i}"),
                        "source_start_us": int(clip["start_time"] * 1_000_000),
                        "source_duration_us": int((clip["end_time"] - clip["start_time"]) * 1_000_000),
                        "hook_text": clip.get("hook_text", "")
                    }
                    for i, clip in enumerate(clips, start=1)
                ]
            }
        ]
    }

    Path(output_json_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2)

    return output_json_path
