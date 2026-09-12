"""Build multilingual context Anki cards from text, subtitles, audio, or video.

The generator is intentionally deterministic: lookup records provide learner intent,
subtitles provide timing, and optional local capabilities provide media and speech.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_dictionary import lookup as dictionary_lookup_path
from local_dictionary import lookup_provider
from transcribe import transcribe as run_transcription


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str
    raw: str = ""


@dataclass(frozen=True)
class Lookup:
    term: str
    sentence: str | None = None
    definition: str = ""
    part_of_speech: str = ""
    note: str = ""
    timestamp: float | None = None
    word_audio: Path | None = None


def parse_timestamp(value: str) -> float:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Unsupported subtitle timestamp: {value!r}")


def parse_subtitles(path: Path) -> list[Cue]:
    lines = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
    cues: list[Cue] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.upper() == "WEBVTT" or line.startswith("NOTE"):
            i += 1
            continue
        if "-->" not in line and i + 1 < len(lines) and "-->" in lines[i + 1]:
            i += 1
            line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        left, right = line.split("-->", 1)
        start, end = parse_timestamp(left.split()[0]), parse_timestamp(right.split()[0])
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i].strip())
            i += 1
        raw = " ".join(body)
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if text and end > start:
            cues.append(Cue(len(cues) + 1, start, end, re.sub(r"\s+", " ", text), raw))
    return cues


def normalize(value: str) -> str:
    value = html.unescape(value).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'\u4e00-\u9fff\u3040-\u30ff-]", " ", value, flags=re.UNICODE)).strip()


def language_family(language: str) -> str:
    return language.casefold().split("-")[0]


def contains_term(text: str, term: str, language: str) -> bool:
    text_n, term_n = normalize(text), normalize(term)
    if not text_n or not term_n:
        return False
    if language_family(language) in {"zh", "ja", "ko"}:
        return term_n in text_n
    return re.search(rf"(?<![\w'-]){re.escape(term_n)}(?![\w'-])", text_n, re.I) is not None


def load_lookups(path: Path) -> list[Lookup]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Lookups JSON must be an array")
    result: list[Lookup] = []
    for item in raw:
        if isinstance(item, str):
            item = {"term": item}
        if not isinstance(item, dict) or not str(item.get("term", "")).strip():
            raise ValueError("Each lookup must contain a non-empty 'term'")
        audio = item.get("word_audio")
        audio_path = Path(str(audio)).expanduser() if audio else None
        if audio_path and not audio_path.is_absolute():
            audio_path = path.parent / audio_path
        if audio_path and not audio_path.is_file():
            raise ValueError(f"word_audio file does not exist: {audio_path}")
        sentence = item.get("sentence")
        result.append(Lookup(
            term=str(item["term"]).strip(),
            sentence=str(sentence).strip() if sentence is not None and str(sentence).strip() else None,
            definition=str(item.get("definition", "")).strip(),
            part_of_speech=str(item.get("part_of_speech", "")).strip(),
            note=str(item.get("note", "")).strip(),
            timestamp=float(item["timestamp"]) if item.get("timestamp") is not None else None,
            word_audio=audio_path,
        ))
    return result


def sentence_spans(cues: list[Cue], language: str, limit: float = 12.0) -> list[Cue]:
    """Join subtitle cues into sentence-sized ranges; CJK uses punctuation too."""
    spans: list[Cue] = []
    pending: list[Cue] = []
    for cue in cues:
        if not cue.text or normalize(cue.text) in {"music", "[music]"}:
            continue
        pending.append(cue)
        text = " ".join(c.text for c in pending)
        if re.search(r"[.!?。！？](?:[\"'’”）】]*)$", cue.text) or cue.end - pending[0].start >= limit:
            spans.append(Cue(len(spans) + 1, pending[0].start, cue.end, re.sub(r"\s+", " ", text)))
            pending = []
    if pending:
        spans.append(Cue(len(spans) + 1, pending[0].start, pending[-1].end, " ".join(c.text for c in pending)))
    return spans


def find_context(item: Lookup, cues: list[Cue], language: str) -> Cue | None:
    candidates = cues
    if item.sentence:
        expected = normalize(item.sentence)
        exact = [c for c in cues if expected in normalize(c.text)]
        if exact:
            candidates = exact
    matches = [c for c in candidates if contains_term(c.text, item.term, language)]
    if not matches:
        return None
    if item.timestamp is None:
        return matches[0]
    return min(matches, key=lambda c: abs((c.start + c.end) / 2 - item.timestamp))


def find_sentence(item: Lookup, spans: list[Cue], language: str) -> Cue | None:
    candidates = [s for s in spans if contains_term(s.text, item.term, language)]
    if item.sentence:
        expected = normalize(item.sentence)
        exact = [s for s in candidates if expected in normalize(s.text)]
        if exact:
            candidates = exact
    if not candidates:
        return None
    if item.timestamp is None:
        return candidates[0]
    return min(candidates, key=lambda s: abs((s.start + s.end) / 2 - item.timestamp))


def source_fingerprint(path: Path | None, source: str) -> str:
    if path and path.is_file():
        stat = path.stat()
        source = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def instance_key(language: str, term: str, source: str, start: float, sentence: str) -> str:
    raw = "|".join([language.casefold(), normalize(term), source, f"{start:.3f}", normalize(sentence)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def html_field(value: str) -> str:
    return html.escape(value, quote=False).replace("\n", "<br>")


def sentence_field(sentence: str, term: str, language: str) -> str:
    escaped = html_field(sentence)
    if language_family(language) in {"zh", "ja", "ko"}:
        pos = escaped.casefold().find(html.escape(term, quote=False).casefold())
        if pos >= 0:
            end = pos + len(html.escape(term, quote=False))
            return escaped[:pos] + "<b>" + escaped[pos:end] + "</b>" + escaped[end:]
        return escaped
    return re.sub(rf"(?<![\w'-])({re.escape(html.escape(term, quote=False))})(?![\w'-])", r"<b>\1</b>", escaped, count=1, flags=re.I)


def load_profile(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object")
    return data


def capability_value(config: dict[str, Any], section: str, *keys: str) -> str | None:
    value = config.get(section, {})
    if isinstance(value, dict):
        for key in keys:
            if value.get(key):
                return str(value[key])
    for key in keys:
        if config.get(key):
            return str(config[key])
    return None


def dictionary_entry(term: str, language: str, configured: str | None = None):
    """Resolve a local dictionary entry without ever making an online request."""
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return lookup_provider(term, language, path)
        return dictionary_lookup_path(term, path, language=language)
    return lookup_provider(term, language)


def downloader_command(configured: str | None) -> list[str]:
    """Return argv prefix for either a yt-dlp executable or Python module."""
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() or shutil.which(configured):
            return [str(candidate.resolve()) if candidate.is_file() else str(shutil.which(configured))]
        if configured in {"yt-dlp", "yt_dlp"}:
            return [sys.executable, "-m", "yt_dlp"]
        raise RuntimeError(f"Configured downloader was not found: {configured}")
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise RuntimeError("URL input needs a configured downloader or an installed yt-dlp module")


def resolve_executable(value: str | None, capability: str) -> str:
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
        found = shutil.which(value)
        if found:
            return found
        raise RuntimeError(f"Configured {capability} was not found: {value}")
    found = shutil.which("ffmpeg") if capability == "media_processor" else None
    if found:
        return found
    raise RuntimeError(f"No {capability} implementation is available")


def run_media(ffmpeg: str, source: Path, start: float, end: float, output: Path, kind: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.2, end - start)
    if kind == "gif":
        args = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}", "-an", "-vf", "fps=6,scale=360:-2:flags=lanczos", "-loop", "0", str(output)]
    else:
        args = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}", "-vn", "-acodec", "libmp3lame", "-q:a", "4", str(output)]
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def synthesize(term: str, output: Path, executable: str) -> None:
    escaped = term.replace("'", "''")
    target = str(output.resolve()).replace("'", "''")
    command = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.SetOutputToWaveFile('" + target + "'); $v.Speak('" + escaped + "'); $v.Dispose()"
    subprocess.run([executable, "-NoProfile", "-Command", command], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def stable_media_name(source: str, term: str, start: float, end: float, kind: str, suffix: str) -> str:
    digest = hashlib.sha256(f"{source}|{normalize(term)}|{start:.3f}|{end:.3f}|{kind}".encode()).hexdigest()[:24]
    return f"anki_{kind}_{digest}{suffix}"


class AnkiConnect:
    def __init__(self, url: str):
        self.url = url

    def call(self, action: str, **params: Any) -> Any:
        body = json.dumps({"action": action, "version": 6, "params": params}).encode()
        request = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach AnkiConnect at {self.url}: {exc}") from exc
        if payload.get("error"):
            raise RuntimeError(f"AnkiConnect {action} failed: {payload['error']}")
        return payload.get("result")

    def store(self, filename: str, path: Path) -> None:
        self.call("storeMediaFile", filename=filename, path=str(path.resolve()))

    def ensure_model(self, model_name: str, profile: dict[str, Any]) -> None:
        fields = profile.get("fields") or ["Term", "Pronunciation", "Context", "TermAudio", "ContextAudio", "Visual", "Definition", "PartOfSpeech", "Language", "TranslationLanguage", "Source", "Note", "InstanceKey", "ResourceStatus"]
        front = profile.get("front") or "<div class=\"term\">{{Term}}</div><div class=\"context\">{{Context}}</div>"
        back = profile.get("back") or "{{FrontSide}}<hr>{{#Visual}}<div>{{Visual}}</div>{{/Visual}}<div class=\"audio\">{{TermAudio}}{{ContextAudio}}</div>{{#Pronunciation}}<div>{{Pronunciation}}</div>{{/Pronunciation}}<div>{{Definition}}</div>{{#PartOfSpeech}}<div>{{PartOfSpeech}}</div>{{/PartOfSpeech}}<div>{{ResourceStatus}}</div>"
        css = profile.get("css") or ".card{text-align:center;font-family:Arial;color:#202124;background:#faf8f1;padding:24px}.term{font-size:42px;color:#159ac5}.context{font-size:24px;line-height:1.45;margin:22px auto;max-width:850px}.context b{color:#159ac5}.audio{min-height:28px;margin:12px}.card img{max-width:100%;max-height:400px}"
        if model_name not in (self.call("modelNames") or []):
            self.call("createModel", modelName=model_name, inOrderFields=fields, css=css, isCloze=False, cardTemplates=[{"Name": "Context", "Front": front, "Back": back}])
        else:
            actual = self.call("modelFieldNames", modelName=model_name) or []
            missing = [f for f in fields if f not in actual]
            if missing:
                raise RuntimeError(f"Note type {model_name!r} is missing fields: {', '.join(missing)}")
            self.call("updateModelTemplates", model={"name": model_name, "templates": {"Context": {"Front": front, "Back": back}}})
            self.call("updateModelStyling", model={"name": model_name, "css": css})


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    target_language = args.target_language
    translation_language = args.translation_language
    config = json.loads(args.capabilities.read_text(encoding="utf-8")) if args.capabilities else {}
    profile = load_profile(args.profile)
    material = args.video or args.audio or args.text or args.subtitles
    if not material and not args.url:
        raise ValueError("Provide --text, --subtitles, --audio, --video, or --url")
    video = args.video
    audio = args.audio
    transcript_source = "provided"
    source_label = args.source or (str(material) if material else args.url or "text")
    subtitle_path = args.subtitles
    if args.url:
        downloader = args.downloader or capability_value(config, "download", "executable", "downloader", "yt_dlp")
        args.download_dir.mkdir(parents=True, exist_ok=True)
        command = downloader_command(downloader) + ["--no-playlist", "--format", "bv*+ba/b", "--merge-output-format", "mp4", "--write-subs", "--write-auto-subs", "--sub-langs", f"{target_language}," + language_family(target_language), "--sub-format", "vtt", "--output", str(args.download_dir / "source.%(ext)s")]
        if args.cookies:
            if not args.cookies.is_file():
                raise ValueError(f"Cookie file does not exist: {args.cookies}")
            command.extend(["--cookies", str(args.cookies)])
        command.append(args.url)
        subprocess.run(command, check=True)
        videos = [p for p in args.download_dir.glob("source.*") if p.suffix.casefold() in {".mp4", ".mkv", ".webm", ".mov"}]
        video = max(videos, key=lambda p: p.stat().st_mtime_ns) if videos else None
        subs = list(args.download_dir.glob("source*.vtt"))
        subtitle_path = subs[0] if subs else None
    if args.transcribe and not subtitle_path:
        source_media = video or audio
        if not source_media:
            raise ValueError("--transcribe requires --video or --audio media")
        args.output.mkdir(parents=True, exist_ok=True)
        transcription = config.get("transcription", {}) if isinstance(config.get("transcription", {}), dict) else {}
        engine = args.transcriber or transcription.get("engine")
        model_dir = args.transcriber_model_dir or transcription.get("model_dir")
        model_name = args.transcriber_model or transcription.get("model", "large-v2")
        transcript_dir = args.output / "transcript"
        subtitle_path = run_transcription(source_media, transcript_dir, target_language, engine, model_dir, model_name)
        transcript_source = "ai-generated"
    if args.text:
        cues = [Cue(1, 0.0, 0.0, args.text.read_text(encoding="utf-8"))]
    elif subtitle_path:
        cues = parse_subtitles(subtitle_path)
    else:
        raise ValueError("Audio/video input needs a timed --subtitles file")
    material_type = args.material_type or ("Video" if video or args.url else "Audio" if audio else "Text")
    spans = sentence_spans(cues, target_language) if cues and cues[0].end > 0 else cues
    lookups = load_lookups(args.lookups)
    source_id = source_fingerprint(video or audio or args.text, source_label)
    args.output.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for item in lookups:
        context = find_sentence(item, spans, target_language) if spans and spans[0].end > 0 else (Cue(1, 0, 0, item.sentence or cues[0].text) if item.sentence or cues else None)
        record: dict[str, Any] = {"term": item.term, "matched": context is not None, "language": target_language, "translation_language": translation_language}
        if context is None:
            record.update({"error": "No matching context"})
            manifest.append(record)
            continue
        sentence = item.sentence or context.text
        key = instance_key(target_language, item.term, source_id, context.start, sentence)
        record.update({"sentence": sentence, "start": context.start, "end": context.end, "instance_key": key, "source": source_label, "lookup": {"term": item.term, "definition": item.definition, "part_of_speech": item.part_of_speech, "note": item.note}})
        dictionary = dictionary_entry(item.term, target_language, args.dictionary)
        status = {"subtitles": transcript_source, "dictionary": "provided" if item.definition else ("available" if dictionary else "missing"), "term_audio": "provided" if item.word_audio else "pending", "context_audio": "unavailable", "visual": "unavailable"}
        if video or audio:
            status["context_audio"] = "available" if context.end > context.start else "unavailable"
        if video:
            status["visual"] = "available"
        record["resource_status"] = status
        if not args.dry_run and (video or audio):
            ffmpeg_setting = args.media_processor or capability_value(config, "media", "ffmpeg", "executable", "path")
            ffmpeg = resolve_executable(ffmpeg_setting, "media_processor")
            stem = f"{len(manifest)+1:03d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', item.term).strip('_') or 'term'}"
            base = args.output / stem
            source_media = video or audio
            if video:
                visual = base.with_suffix(".gif"); run_media(ffmpeg, source_media, max(0, context.start-args.padding), context.end+args.padding, visual, "gif"); record["visual"] = str(visual)
            context_audio = base.with_name(base.name + "-context.mp3"); run_media(ffmpeg, source_media, max(0, context.start-args.padding), context.end+args.padding, context_audio, "audio"); record["context_audio"] = str(context_audio)
            word_audio = base.with_name(base.name + "-term.mp3")
            if item.word_audio:
                shutil.copyfile(item.word_audio, word_audio)
            else:
                tts = args.tts or capability_value(config, "tts", "executable", "path")
                if tts:
                    wav = base.with_name(base.name + "-term.wav"); synthesize(item.term, wav, resolve_executable(tts, "tts")); run_media(ffmpeg, wav, 0, 60, word_audio, "audio"); wav.unlink(missing_ok=True)
                else:
                    status["term_audio"] = "missing"
            if word_audio.is_file():
                record["term_audio"] = str(word_audio)
        manifest.append(record)
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run or args.prepare_only:
        return manifest
    deck = args.deck or f"Anki::{target_language}::{material_type}"
    model = args.model or "Anki Card Maker Context"
    anki = AnkiConnect(args.anki_url); anki.call("version"); anki.call("createDeck", deck=deck); anki.ensure_model(model, profile)
    existing_ids = anki.call("findNotes", query=f'deck:"{deck}"') or []
    existing = anki.call("notesInfo", notes=existing_ids) if existing_ids else []
    by_key = {n.get("fields", {}).get("InstanceKey", {}).get("value", ""): n for n in existing if n.get("modelName") == model}
    for record in manifest:
        if not record.get("matched"):
            continue
        dictionary = dictionary_entry(record["term"], target_language, args.dictionary)
        definition = record["lookup"].get("definition") or getattr(dictionary, "translation", "") or getattr(dictionary, "definition", "") or ""
        fields = {"Term": html_field(record["term"]), "Pronunciation": html_field(getattr(dictionary, "phonetic", "")), "Context": sentence_field(record["sentence"], record["term"], target_language), "TermAudio": f"[sound:{Path(record['term_audio']).name}]" if record.get("term_audio") else "", "ContextAudio": f"[sound:{Path(record['context_audio']).name}]" if record.get("context_audio") else "", "Visual": f'<img src="{Path(record["visual"]).name}">' if record.get("visual") else "", "Definition": html_field(definition), "PartOfSpeech": html_field(record["lookup"].get("part_of_speech", "")), "Language": target_language, "TranslationLanguage": translation_language, "Source": html_field(record["source"]), "Note": html_field(record["lookup"].get("note", "")), "InstanceKey": record["instance_key"], "ResourceStatus": html_field(json.dumps(record["resource_status"], ensure_ascii=False))}
        for field in ("visual", "term_audio", "context_audio"):
            if record.get(field):
                name = stable_media_name(source_id, record["term"], record["start"], record["end"], field, Path(record[field]).suffix); anki.store(name, Path(record[field])); fields[{"visual":"Visual","term_audio":"TermAudio","context_audio":"ContextAudio"}[field]] = f'<img src="{name}">' if field == "visual" else f"[sound:{name}]"
        note = by_key.get(record["instance_key"])
        if note:
            anki.call("updateNoteFields", note={"id": note["noteId"], "fields": fields}); record["note_id"] = note["noteId"]
        else:
            record["note_id"] = anki.call("addNote", note={"deckName": deck, "modelName": model, "fields": fields, "tags": args.tag})
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_argument_group("material")
    source.add_argument("--url"); source.add_argument("--video", type=Path); source.add_argument("--audio", type=Path); source.add_argument("--text", type=Path); source.add_argument("--subtitles", type=Path)
    p.add_argument("--lookups", type=Path, required=True); p.add_argument("--target-language", required=True); p.add_argument("--translation-language", default="zh-CN")
    p.add_argument("--material-type", choices=["Video", "Audio", "Text"]); p.add_argument("--deck"); p.add_argument("--model"); p.add_argument("--profile", type=Path); p.add_argument("--dictionary"); p.add_argument("--tts"); p.add_argument("--transcriber"); p.add_argument("--transcriber-model-dir"); p.add_argument("--transcriber-model", default="large-v2"); p.add_argument("--transcribe", action="store_true"); p.add_argument("--media-processor"); p.add_argument("--capabilities", type=Path); p.add_argument("--downloader"); p.add_argument("--cookies", type=Path); p.add_argument("--download-dir", type=Path, default=Path("downloads")); p.add_argument("--output", type=Path, default=Path("anki-output")); p.add_argument("--padding", type=float, default=0.35); p.add_argument("--dry-run", action="store_true"); p.add_argument("--prepare-only", action="store_true"); p.add_argument("--anki-url", default="http://127.0.0.1:8765"); p.add_argument("--source"); p.add_argument("--tag", action="append", default=["anki_context_card"])
    return p


if __name__ == "__main__":
    try:
        result = build(parser().parse_args())
        print(json.dumps({"count": len(result), "matched": sum(bool(r.get("matched")) for r in result)}, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr); raise SystemExit(2)
