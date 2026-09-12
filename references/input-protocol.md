# Input Protocol

Create a UTF-8 `lookups.json` array. Each item needs `term`; include the complete `sentence` whenever possible. `definition`, `part_of_speech`, `note`, `word_audio`, and `timestamp` are optional.

```json
[
  {
    "term": "figure out",
    "sentence": "I need to figure out what happened.",
    "definition": "弄明白；解决",
    "part_of_speech": "phrasal verb",
    "timestamp": 123.4
  }
]
```

Run from the skill's `scripts` directory with absolute paths:

```powershell
python .\anki_cards.py --video "D:\media\episode.mp4" --subtitles "D:\media\episode.en.vtt" --lookups "D:\jobs\lookups.json" --target-language en-US --output "D:\jobs\output" --profile "..\profiles\context.json" --dry-run
```

For text, use `--text` and omit media. For audio, use `--audio` plus timed subtitles. For a URL, add `--url`, `--download-dir`, and optionally `--downloader` and `--cookies`. The downloader may be an executable path or the `yt-dlp` Python module.

When subtitles are unavailable, ask whether the user has an ASR/transcription app or service that can export SRT/VTT. With approval and a compatible local VideoCaptioner/Faster-Whisper adapter, add `--transcribe --transcriber <path>`; generated subtitles are retained under `output\transcript`.

Use `--dictionary` for a local SQLite dictionary file or directory. Use `--capabilities` for non-secret implementation paths. Run dry-run first; remove it only after the preview is approved and Anki write is authorized.
