# Anki Context Card Maker

A multilingual Codex skill for turning real language input into Fushi-style Anki context cards. It accepts text, timed subtitles, audio, video, or a video URL and keeps the target language, translation language, deck, dictionary, TTS, ASR, and media processor configurable.

## What it produces

- A front containing the target term or phrase and a complete context sentence.
- An answer containing available visual media, an offline dictionary definition, term audio, and context-sentence audio.
- Audio references in the order **term audio → context audio**.
- A deterministic `InstanceKey`, so repeated runs update the same context while the same term in another sentence or source remains a separate card.
- A manifest that explicitly records missing subtitles, dictionaries, TTS, media processors, or other resources instead of fabricating them.

The default deck is isolated by language and material type: `Anki::<language>::Video`, `Anki::<language>::Audio`, or `Anki::<language>::Text`. The default neutral note type is `Anki Card Maker Context`. Existing decks and note types are not migrated or modified.

## Install

Copy or clone this repository into the Codex skills directory. The skill folder itself keeps the standard name `anki-context-card-maker`; the `.skill` suffix belongs to this distributable repository name.

```powershell
Copy-Item -Recurse .\anki-context-card-maker.skill "$env:USERPROFILE\.agents\skills\anki-context-card-maker"
```

Run `scripts/anki_cards.py --dry-run` first, inspect `manifest.json`, and only then run without `--dry-run` after approving the candidates and ensuring AnkiConnect is available. See [references/input-protocol.md](references/input-protocol.md) for lookup JSON and command examples.

## Optional capabilities

yt-dlp, VideoCaptioner/Faster-Whisper, ffmpeg, TTS, and AnkiConnect are optional implementations, not hard dependencies. Reuse an existing equivalent tool or exported SRT/VTT when possible. The skill never silently installs software, uploads source media, or performs online dictionary queries while generating cards.

## Offline dictionaries

The repository intentionally does not commit generated dictionary databases: ECDICT is larger than GitHub's regular file limit, and CC-CEDICT/JMdict carry their own distribution terms. Use the explicit installer when local dictionaries are needed:

```powershell
python .\scripts\install_dictionaries.py --provider ecdict
python .\scripts\install_dictionaries.py --provider cc-cedict
python .\scripts\install_dictionaries.py --provider jmdict
```

You can also provide a locally downloaded source file with `--source`. The installer records provider, source URL, SHA-256, installation time, and license notes in `scripts/data/dictionary-manifest.json`. Card generation itself remains offline.

## Scope boundary

This skill handles language/media cards. It is intentionally separate from `ai-learning-review`, which extracts durable theory, project decisions, and workflow review units.
