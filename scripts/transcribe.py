"""Optional local ASR adapter producing timed SRT for any BCP-47 language."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def resolve_engine(engine: str | Path | None = None) -> Path:
    configured = engine or os.environ.get("ANKI_CONTEXT_TRANSCRIBER")
    if configured:
        candidate = Path(str(configured)).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(str(configured))
        if found:
            return Path(found).resolve()
        raise RuntimeError(f"Configured transcription executable was not found: {configured}")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "VideoCaptioner" / "resource" / "bin" / "Faster-Whisper-XXL" / "faster-whisper-xxl.exe"
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which("faster-whisper-xxl") or shutil.which("faster-whisper-xxl.exe")
    if found:
        return Path(found).resolve()
    raise RuntimeError("No ASR adapter is configured; provide a timed SRT/VTT from another tool or configure VideoCaptioner/Faster-Whisper.")


def transcribe(media: Path, output_dir: Path, language: str, engine: str | Path | None = None, model_dir: str | Path | None = None, model: str = "large-v2") -> Path:
    executable = resolve_engine(engine)
    if not media.is_file():
        raise ValueError(f"Media file does not exist: {media}")
    models = Path(model_dir).expanduser().resolve() if model_dir else executable.parents[3] / "AppData" / "models"
    if not (models / f"faster-whisper-{model}").is_dir():
        raise RuntimeError(f"Local ASR model is missing: {models / f'faster-whisper-{model}'}")
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(executable), "-m", model, "--model_dir", str(models), "-l", language, "-o", str(output_dir), "-f", "srt", "--sentence", "--max_line_width", "1000", "--max_line_count", "1", "--beep_off", str(media)], check=True)
    subtitle = output_dir / f"{media.stem}.srt"
    if not subtitle.is_file() or not subtitle.read_text(encoding="utf-8-sig", errors="replace").strip():
        raise RuntimeError(f"ASR completed without an SRT subtitle: {subtitle}")
    return subtitle


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--media", "--video", dest="media", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--language", default="en")
    p.add_argument("--transcriber-engine", dest="engine")
    p.add_argument("--transcriber-model-dir", dest="model_dir")
    p.add_argument("--transcriber-model", dest="model", default="large-v2")
    return p


if __name__ == "__main__":
    try:
        args = parser().parse_args()
        print(transcribe(args.media, args.output, args.language, args.engine, args.model_dir, args.model))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"error: {exc}")
