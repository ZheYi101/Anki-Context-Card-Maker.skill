---
name: anki-context-card-maker
description: Turn real language input from text, subtitles, audio, or video into multilingual Anki context cards. Works with learning-lab and ai-learning-review; preserves exact sentence context with optional local dictionaries, TTS, media, and transcription.
---

# Anki Context Card Maker

Use this skill when the user wants real language input turned into Anki cards. One task uses one BCP-47 `target_language` such as `en-US`, `zh-CN`, or `ja-JP`; `translation_language` defaults to `zh-CN`.

The default Fushi/Lapis-style card keeps the front focused on the target term and one complete context sentence. The back contains a lightweight PNG video frame, a standard term/phrase pronunciation followed by the complete context sentence audio, and structured local dictionary data. English cards default to the top-level `English` deck. The neutral note type is `Anki Card Maker Context`; existing review scheduling is preserved when notes are upgraded.

## Learning integration

- Accept direct card-making requests or handoffs from `learning-lab` / `ai-learning-review`; reuse the supplied source, exact sentence, target meaning, timestamp, language, and chosen destination. Do not restart a learner interview or ask for information already present.
- Route by retrieval target: sentence-dependent vocabulary, expressions, pronunciation, and media belong here; durable concepts, rules, and decisions can use `ai-learning-review`. A paper or business document may supply both knowledge and language goals without duplicating the same target into both card types.
- Before adapting handoff data into lookups, check the actual source form and sentence. Keep a dictionary-form `term`; when inflection or a separated phrase differs, supply its literal `surface` from the sentence, verify the linguistic relationship, and preserve the original sentence. Preserve source locators; do not turn invented examples into source quotations.
- Keep `lookups.json`, `InstanceKey`, existing profiles, decks, and review history. A unified learning entrypoint does not authorize migrations or changes to card identity, templates, media boundaries, or import approval requirements.
- When a card exposes an understanding or usage problem, return to `learning-lab` for explanation or practice using the same context. Do not automatically add another card or implement a second scheduler.

## Workflow

Before selecting cards, read [references/card-quality.md](references/card-quality.md). Select a small set of expressions the learner wants to understand or use; do not convert every unknown word into a card. The default front tests meaning in the supplied context, not listening-only comprehension or productive recall; use `learning-lab` practice or an explicitly chosen card format for those goals.

1. Read [references/input-protocol.md](references/input-protocol.md) and create a UTF-8 lookup JSON. A supplied sentence or timestamp is preferred because it disambiguates repeated terms and preserves the learner's intended context.
2. Read [references/capabilities.md](references/capabilities.md) only for capabilities needed by the chosen input. Reuse the user's downloader, ASR, ffmpeg-compatible processor, TTS, dictionary, and AnkiConnect when available. If the user asks for video/audio cards and captions are missing, prefer the installed `videocaptioner` CLI with an explicitly selected, already-configured local ASR engine; read [references/videocaptioner.md](references/videocaptioner.md) first. The existing `--transcribe` adapter remains available for the legacy Faster-Whisper-XXL executable. External programs are optional implementations; never install, upload, download models, or silently send media to online ASR services.
3. Run `scripts/anki_cards.py` with `--dry-run` or `--prepare-only` first and inspect `manifest.json`, including `ready_for_import` and `quality_issues`. Source matching uses `surface` when supplied, otherwise `term`; absent source forms or sentences remain unmatched. A missing contextual definition remains a draft; dictionary senses alone do not select the meaning. For video cards, deduplicate rolling captions and derive one target-sentence window; do not silently include adjacent sentences. Required media must exist as prepared files before import: video requires PNG and sentence audio; audio requires sentence audio. If only subtitles/text are available but the user requires media, pass `--require-media visual` / `--require-media context_audio` as appropriate. `source_available` means extraction is possible, not completed. Keep missing resources visible in the manifest, not as raw diagnostic JSON on the card.
4. After the user approves the preview, run without `--dry-run` to write the selected deck. If no deck is supplied for English, use the top-level `English` deck; use language-scoped defaults only for non-English languages.
5. Verify the AnkiConnect result, note fields, media references, and stable `InstanceKey` values. Use a stable `--source` locator when available; without one the script identifies local content by bytes. Touching or moving unchanged files must not create new instances. Legacy keys and unique exact source/term/context matches reuse existing notes and keys; ambiguous legacy matches are reported for review. The same term in another sentence or logical source remains a separate instance.

## Inputs and routing

Use the current platform's commands and paths: on macOS/Linux run scripts with `python3` and forward slashes. The built-in automatic TTS is Windows-only; on macOS reuse supplied `word_audio` or report that optional capability as missing. Reuse exported SRT/VTT when the legacy transcription adapter is unavailable. See [README.md](README.md) for installation and platform details.

- Video: `--video` or `--url`, plus timed subtitles; use `--transcribe` only with an available local ASR adapter and label its output as AI-generated.
- Audio: `--audio` plus timed subtitles.
- Text: `--text`, or subtitles without video/audio. Text cards can still be generated when media capabilities are absent.
- URL downloads use a configured compatible downloader or an installed `yt-dlp` module. Cookie paths may be passed with `--cookies`; never print cookie contents.
- Local dictionaries are selected by language (`en` ECDICT, `zh` CC-CEDICT, `ja` JMdict) and are used offline. Keep headword, pronunciation, part of speech, definitions, translation, and source metadata where available. The supplied contextual meaning is the main answer; broader dictionary senses and optional examples stay in collapsed reference sections. Missing databases remain a diagnosed capability limitation; a verified supplied contextual meaning can still support a text card.

The declaration-driven profile at `profiles/context.json` controls fields, templates, and CSS. Language metadata lives in `languages/*.json`; add a language package rather than hard-coding language-specific matching in the card writer. CJK matching must not rely on English word-boundary regexes.

Keep the old historical scripts and Anki data untouched, including `English::Video`, `English Video Fushi`, and `Lapis`.

For detailed dictionary fields and phrase fallback behavior, read `references/dictionary-capability.md`. For sentence-boundary and media-window rules, read `references/media-segmentation.md`.
