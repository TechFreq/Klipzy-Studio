"""
Transcription worker — runs Whisper in a SEPARATE process so a job cancel can
hard-kill it.

Background: everything else in the pipeline routes ffmpeg through proc.run, so a
cancel kills the active subprocess. Transcription, however, ran IN-process
(PyTorch / CTranslate2 / MLX), so once Whisper started, "Cancel" couldn't
interrupt it — the job ran to completion first (handoff §5.2 known limitation).

This module is a tiny CLI that transcribes one audio file with the normal
``Transcriber`` (so all backend selection + fallbacks are preserved) and writes
the segments as JSON. The parent launches it via proc.run and, on cancel,
kills this process. Invoked as:

    python -m server.core.transcribe_worker <audio> --model base --out out.json [--language en]

Exit code 0 on success; non-zero (with a message on stderr) on failure, which
tells the parent to fall back to in-process transcription.
"""
import argparse
import json
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Klipzy transcription worker")
    parser.add_argument("audio")
    parser.add_argument("--model", default="base")
    parser.add_argument("--language", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        from server.core.transcriber import Transcriber
        segments = Transcriber(model_size=args.model).transcribe(args.audio, language=args.language)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump([s.model_dump() for s in segments], fh, ensure_ascii=False)
        return 0
    except Exception as e:  # noqa: BLE001 — surface any failure to the parent
        sys.stderr.write(f"transcribe_worker failed: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
