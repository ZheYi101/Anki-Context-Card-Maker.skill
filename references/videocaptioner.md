# VideoCaptioner CLI and local transcription

Use this when a video or audio card task has no usable timed subtitles. Prefer the installed `videocaptioner` command over opening or automating the GUI. Check `videocaptioner --help` and `videocaptioner transcribe --help` before choosing flags; CLI options can vary by release.

For the CLI, select a local ASR engine explicitly and use only an engine/model already installed and configured. For example, when `whisper-cpp` is listed by the installed CLI and its local binary/model are ready:

```powershell
videocaptioner transcribe "D:\media\lecture.mp4" --asr whisper-cpp --language en --format srt --output "D:\jobs\lecture.srt"
```

Windows routing note for this machine: the user observed that VideoCaptioner does not follow Clash Verge rule-mode routing, while Global mode does. For a user-approved network-backed VideoCaptioner call that needs the proxy, set `HTTP_PROXY` and `HTTPS_PROXY` for that CLI process to Clash Verge's current HTTP/mixed listener; verify its current port instead of assuming one. Do not change system-wide or Global proxy mode for a skill run. This routing note does not authorize sending media to an online ASR service.

Pass the resulting file to `anki_cards.py` with `--subtitles`; do not also add `--transcribe`. Do not rely on the CLI's default ASR selection: services such as `bijian`, `jianying`, or `whisper-api` can send media to an online provider and require the user's explicit approval.

The legacy `--transcribe` adapter below runs VideoCaptioner's bundled `faster-whisper-xxl` command-line executable. It does not accept `videocaptioner` itself as `--transcriber`, because that wrapper uses Faster-Whisper-XXL-specific flags. Neither path opens or automates the GUI, uploads media, installs software, or downloads models.

## Resolve the existing install

The transcription adapter looks for an executable in this order:

1. `--transcriber <path>` on the `anki_cards.py` command.
2. `transcription.engine` in the JSON passed with `--capabilities`.
3. The `ANKI_CONTEXT_TRANSCRIBER` environment variable.
4. `%LOCALAPPDATA%\VideoCaptioner\resource\bin\Faster-Whisper-XXL\faster-whisper-xxl.exe`.
5. `faster-whisper-xxl` or `faster-whisper-xxl.exe` on `PATH`.

The default model is `large-v2`; it must already exist as `faster-whisper-large-v2` under VideoCaptioner's `AppData\models` directory. Use `--transcriber-model` to select another installed model or `--transcriber-model-dir` for a nonstandard model directory. The adapter checks both paths before starting. Do not install or fetch a missing model as part of card generation.

If a first run cannot find the executable, inspect the local installation and use `--transcriber` with its actual path. If a nonstandard executable rejects the adapter's command, inspect that executable's `--help` output and adjust only after confirming the supported flags; VideoCaptioner's current adapter expects Faster-Whisper-XXL-style arguments.

## Run it

From the skill's `scripts` directory, `--transcribe` uses the discovered local executable when no subtitle file was supplied:

```powershell
python .\anki_cards.py --video "D:\media\episode.mp4" --transcribe --lookups "D:\jobs\lookups.json" --target-language en-US --output "D:\jobs\output" --prepare-only
```

For a nonstandard executable or model location, add the corresponding options:

```powershell
--transcriber "D:\Apps\VideoCaptioner\resource\bin\Faster-Whisper-XXL\faster-whisper-xxl.exe" --transcriber-model-dir "D:\Apps\VideoCaptioner\AppData\models" --transcriber-model large-v2
```

The generated SRT is kept at `output\transcript\<media-stem>.srt`; the manifest identifies its source as AI-generated. Inspect the transcript and card preview before writing to Anki. To revise cards without paying the transcription cost again, pass the retained SRT with `--subtitles` on the next run and omit `--transcribe`.

When using `--capabilities` for a repeated setup, store only stable local paths and model settings under a `transcription` object. For example:

```json
{
  "transcription": {
    "engine": "D:\\Apps\\VideoCaptioner\\resource\\bin\\Faster-Whisper-XXL\\faster-whisper-xxl.exe",
    "model_dir": "D:\\Apps\\VideoCaptioner\\AppData\\models",
    "model": "large-v2"
  }
}
```

Keep the user's paths out of shared skill instructions. The general skill format here uses Codex's `name`/`description` frontmatter and linked references; Claude Code-only fields or shell-injection syntax such as `!command` are not portable and should not be copied into `SKILL.md`.
