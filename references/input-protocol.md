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

When subtitles are unavailable for a video/audio card task, read [videocaptioner.md](videocaptioner.md). If the `videocaptioner` CLI is installed and a local ASR engine is already configured, run `videocaptioner transcribe` with that engine explicitly selected, save an SRT, then pass it to `anki_cards.py` with `--subtitles`. Do not let a default or remote ASR send the media online without the user's approval. The `--transcribe` option is for the legacy Faster-Whisper-XXL executable adapter; when needed, pass its path with `--transcriber <path>`. Generated subtitles are retained and marked AI-generated. If no local engine/model is available, do not install software or download models; ask for an exported SRT/VTT or local ASR configuration.

Use `--dictionary` for a local SQLite dictionary file or directory. Use `--capabilities` for non-secret implementation paths. Run dry-run first; remove it only after the preview is approved and Anki write is authorized.
