---
name: anki-context-card-maker
description: Turn text, subtitles, audio, or video into multilingual Anki context cards with optional local dictionaries, TTS, sentence audio, looping visual clips, and local VideoCaptioner CLI transcription.
---

# Anki Context Card Maker

Use this skill when the user wants real language input turned into Anki cards. One task uses one BCP-47 `target_language` such as `en-US`, `zh-CN`, or `ja-JP`; `translation_language` defaults to `zh-CN`.

The default Fushi-style card keeps the front focused on the target term and a complete context sentence. The back may contain a looping GIF (for video), local dictionary data, and audio in this order: term audio, then the complete context sentence audio. The neutral note type is `Anki Card Maker Context`; existing decks and note types are never migrated or changed.

## Workflow

1. Read [references/input-protocol.md](references/input-protocol.md) and create a UTF-8 lookup JSON. A supplied sentence or timestamp is preferred because it disambiguates repeated terms and preserves the learner's intended context.
2. Read [references/capabilities.md](references/capabilities.md) only for capabilities needed by the chosen input. Reuse the user's downloader, ASR, ffmpeg-compatible processor, TTS, dictionary, and AnkiConnect when available. If the user asks for video/audio cards and captions are missing, prefer the installed `videocaptioner` CLI with an explicitly selected, already-configured local ASR engine; read [references/videocaptioner.md](references/videocaptioner.md) first. The existing `--transcribe` adapter remains available for the legacy Faster-Whisper-XXL executable. External programs are optional implementations; never install, upload, download models, or silently send media to online ASR services.
3. Run `scripts/anki_cards.py` with `--dry-run` or `--prepare-only` first and inspect `manifest.json`. Unmatched terms remain unmatched; do not invent context. Missing media or dictionary entries are recorded in `ResourceStatus` and the manifest while usable text cards continue.
4. After the user approves the preview, run without `--dry-run` to write the selected deck. If no deck is supplied, use `Anki::<language>::Video`, `Anki::<language>::Audio`, or `Anki::<language>::Text` based on material type.
5. Verify the AnkiConnect result, note fields, media references, and stable `InstanceKey` values. Re-running the same source updates the same instance; the same term in another sentence or source remains a separate card.

## Inputs and routing

- Video: `--video` or `--url`, plus timed subtitles. If captions are missing, follow [references/videocaptioner.md](references/videocaptioner.md) and prefer the installed CLI with a selected local ASR engine; use `--transcribe` only for its legacy Faster-Whisper-XXL adapter and label output AI-generated.
- Audio: `--audio` plus timed subtitles.
- Text: `--text`, or subtitles without video/audio. Text cards can still be generated when media capabilities are absent.
- URL downloads use a configured compatible downloader or an installed `yt-dlp` module. Cookie paths may be passed with `--cookies`; never print cookie contents.
- Local dictionaries are selected by language (`en` ECDICT, `zh` CC-CEDICT, `ja` JMdict) and are used offline. A supplied `definition` always takes precedence. Missing databases do not block card generation.

The declaration-driven profile at `profiles/context.json` controls fields, templates, and CSS. Language metadata lives in `languages/*.json`; add a language package rather than hard-coding language-specific matching in the card writer. CJK matching must not rely on English word-boundary regexes.

Keep this skill separate from `ai-learning-review`: that skill extracts durable concepts and workflow review units, while this one creates language/media cards from source material.
