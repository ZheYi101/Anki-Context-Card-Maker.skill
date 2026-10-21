# Input Protocol

Create a UTF-8 `lookups.json` array. Each item needs `term`; include its complete original `sentence` whenever possible. `definition` must state the short meaning in this specific context before a card is ready to import. It may be omitted while drafting, but such records have `ready_for_import=false` and are skipped on import. `part_of_speech`, `note`, `word_audio`, and `timestamp` are optional. Use `note` only for a brief usage distinction that helps this target, not a full grammar lesson.

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

`term` is the canonical dictionary form, not necessarily its spelling in the source. For `She figured it out after reading the log.`, use `term: "figure out"`, `surface: "figured it out"`, and the unchanged original `sentence`. For `少しずつ自信を取り戻している。`, use `term: "取り戻す"`, `surface: "取り戻している"`. `surface` is an optional literal contiguous source span, including intervening pronouns when needed; verify it really expresses the target. Do not rewrite the source or supply unrelated text merely to force a match. The script verifies occurrence, not linguistic equivalence.

Supply a stable logical `--source` (article URL, media ID, or known document ID) when available. Otherwise local bytes identify the source; filename and modification time do not. A changed local file without a stable locator is a new source. Existing legacy notes are reused only by key or an unambiguous exact source/term/context match; unresolved identities need review, not silent recreation.

Run from the skill's `scripts` directory with absolute paths:

```powershell
python .\anki_cards.py --video "D:\media\episode.mp4" --subtitles "D:\media\episode.en.vtt" --lookups "D:\jobs\lookups.json" --target-language en-US --output "D:\jobs\output" --profile "..\profiles\context.json" --dry-run
```

For text, use `--text` and omit media. For audio, use `--audio` plus timed subtitles. For a URL, add `--url`, `--download-dir`, and optionally `--downloader` and `--cookies`. The downloader may be an executable path or the `yt-dlp` Python module.

When media is required but only a transcript is supplied, prepare the text with `--require-media context_audio` and, for a required frame, `--require-media visual`. These repeatable flags keep the candidate a draft until files are prepared. Video/audio inputs imply their sentence audio (and video frame) requirements automatically. A nonexistent supplied media path is an error; do not invent a path. Dry-run may report `source_available`; `--prepare-only` actually extracts media without importing. Optional term pronunciation can be required with `--require-media term_audio`.

When subtitles are unavailable for a video/audio card task, read [videocaptioner.md](videocaptioner.md). If the `videocaptioner` CLI is installed and a local ASR engine is already configured, run `videocaptioner transcribe` with that engine explicitly selected, save an SRT, then pass it to `anki_cards.py` with `--subtitles`. Do not let a default or remote ASR send the media online without the user's approval. The `--transcribe` option is for the legacy Faster-Whisper-XXL executable adapter; when needed, pass its path with `--transcriber <path>`. Generated subtitles are retained and marked AI-generated. If no local engine/model is available, do not install software or download models; ask for an exported SRT/VTT or local ASR configuration.

Use `--dictionary` for a local SQLite dictionary file or directory. Use `--capabilities` for non-secret implementation paths. Run dry-run first; remove it only after the preview is approved and Anki write is authorized.
