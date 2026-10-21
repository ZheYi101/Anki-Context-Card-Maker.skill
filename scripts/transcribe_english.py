"""Create timed English SRT subtitles with an optional VideoCaptioner CLI.

This is deliberately a narrow adapter for the Faster-Whisper-XXL command-line
interface. Other ASR products should export SRT/VTT and be passed to the card
generator as ``--subtitles``. This adapter does not start or automate the GUI,
upload media, or download models.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

def resolve_engine(engine: str | Path | None = None) -> Path:
    """Resolve a user-selected ASR adapter without requiring VideoCaptioner."""
    configured = engine or os.environ.get("ENGLISH_VIDEO_ANKI_TRANSCRIBER")
    if configured:
        raw = str(configured).strip()
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(raw)
        if found:
            return Path(found).resolve()
        raise RuntimeError(
            f"The configured transcription executable was not found: {raw}. "
            "Provide another ASR implementation or pass a timed subtitle file instead."
        )
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        standard = Path(local_app_data) / "VideoCaptioner" / "resource" / "bin" / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe"
        if standard.is_file():
            return standard.resolve()
    found = shutil.which("faster-whisper-xxl") or shutil.which("faster-whisper-xxl.exe")
    if found:
        return Path(found).resolve()
    raise RuntimeError(
        "This job needs a transcription capability, but no ASR adapter is configured. "
        "If you have another transcription app or service, export a timed SRT/VTT and pass it as --subtitles; "
        "otherwise consider installing/configuring a local ASR such as VideoCaptioner."
    )


def default_model_dir(engine: Path) -> Path:
    # .../VideoCaptioner/resource/bin/Faster-Whisper-XXL/faster-whisper-xxl.exe
    return engine.parents[3] / "AppData" / "models"


def transcribe(
    video: Path,
    output_dir: Path,
    engine: str | Path | None = None,
    model_dir: str | Path | None = None,
    model: str = "large-v2",
) -> Path:
    """Transcribe ``video`` into ``output_dir/<video-stem>.srt``."""
    video = video.resolve()
    executable = resolve_engine(engine)
    models = Path(model_dir).expanduser().resolve() if model_dir else default_model_dir(executable).resolve()
    if not video.is_file():
        raise ValueError(f"Video file does not exist: {video}")
    if not executable.is_file():
        raise RuntimeError(
            "The selected transcription executable was not found. "
            "Pass --transcriber-engine or provide a timed subtitle file instead."
        )
    if not (models / f"faster-whisper-{model}").is_dir():
        raise RuntimeError(
            f"Local VideoCaptioner model is missing: {models / f'faster-whisper-{model}'}. "
            "Install/download the selected ASR model using its own tool, or pass --transcriber-model-dir."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "-m", model,
        "--model_dir", str(models),
        "-l", "en",
        "-o", str(output_dir),
        "-f", "srt",
        "--sentence",
        "--max_line_width", "1000",
        "--max_line_count", "1",
        "--beep_off",
        str(video),
    ]
    subprocess.run(command, check=True)
    subtitle = output_dir / f"{video.stem}.srt"
    if not subtitle.is_file() or not subtitle.read_text(encoding="utf-8-sig", errors="replace").strip():
        raise RuntimeError(f"VideoCaptioner completed without an SRT subtitle: {subtitle}")
    return subtitle


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="Directory for the generated .srt")
    p.add_argument("--transcriber-engine", "--captioner-engine", dest="engine", help="Optional ASR executable/command")
    p.add_argument("--transcriber-model-dir", "--captioner-model-dir", dest="model_dir", help="Directory containing faster-whisper-<model>")
    p.add_argument("--transcriber-model", "--captioner-model", dest="model", default="large-v2", help="Installed ASR model name")
    return p


if __name__ == "__main__":
    try:
        args = parser().parse_args()
        print(transcribe(args.video, args.output, args.engine, args.model_dir, args.model))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"error: {exc}")
