# Capability Routing

The skill requires capabilities, not particular vendors.

| Capability | Reuse first | Optional implementation | Missing capability |
| --- | --- | --- | --- |
| Timed subtitles | User SRT/VTT or provider captions | Installed `videocaptioner` CLI with an explicitly selected local ASR engine, or the legacy Faster-Whisper-XXL adapter via `--transcribe`; see [videocaptioner.md](videocaptioner.md) | Ask for an exported SRT/VTT or local ASR configuration; do not install tools or download models |
| Download | Existing downloader or local file | yt-dlp executable/module | Ask for an equivalent downloader; do not install silently |
| Media extraction | Existing ffmpeg-compatible tool | ffmpeg on PATH or `--media-processor` | Generate text-only cards and mark media missing, or ask for a processor |
| Term audio | User `word_audio` | PowerShell System.Speech via `--tts` | Keep the card and mark term audio missing |
| Definitions | User `definition` | Local ECDICT/CC-CEDICT/JMdict SQLite | Keep the card and mark dictionary missing |
| Anki write | Desktop Anki + AnkiConnect | Built-in AnkiConnect client | Ask the user to start/configure it |

Never expose cookies or API keys, query an online dictionary during generation, upload user media, or auto-install external tools/models. Record implementation and missing-resource status in the manifest when known.
