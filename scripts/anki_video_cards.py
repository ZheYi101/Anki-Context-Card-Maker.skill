"""Build video-context Anki notes from subtitles and a lookup list.

The CLI deliberately keeps semantic decisions in the input/LLM layer and makes
media processing plus Anki writes deterministic and inspectable.
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

from local_dictionary import LocalDictionaryEntry, lookup as local_dictionary_lookup


class MissingCapabilityError(RuntimeError):
    """An optional implementation is absent for a capability the job needs."""


@dataclass(frozen=True)
class Cue:
    index: int
    start: float
    end: float
    text: str
    raw: str = ""


@dataclass(frozen=True)
class TimedToken:
    time: float
    text: str
    anchored: bool


@dataclass(frozen=True)
class SentenceSpan:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Lookup:
    term: str
    sentence: str | None = None
    definition: str = ""
    part_of_speech: str = ""
    note: str = ""
    timestamp: float | None = None
    word_audio: Path | None = None


@dataclass(frozen=True)
class DictionaryEntry:
    pronunciation: str = ""
    definitions_html: str = ""
    synonyms: str = ""
    translation: str = ""


def validate_cookies(cookies: Path | None, url: str) -> None:
    """Fail early for Bilibili cookie exports without a login session."""
    if cookies is None:
        return
    if not cookies.is_file():
        raise ValueError(f"Cookie file does not exist: {cookies}")
    if "bilibili.com" not in url.casefold():
        return
    names: set[str] = set()
    for line in cookies.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            names.add(fields[5])
    # Never log values. SESSDATA is the indispensable authenticated Bilibili cookie.
    if "SESSDATA" not in names:
        raise ValueError(
            "This Bilibili cookie export has no SESSDATA login session. "
            "Export cookies again from a currently signed-in bilibili.com browser session."
        )


def capability_status() -> dict[str, bool]:
    """Report optional implementations without assuming a particular vendor."""
    return {
        "python": sys.executable is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "yt_dlp_module": importlib.util.find_spec("yt_dlp") is not None,
        "anki_connect": True,  # checked by the API call immediately before write
    }


def resolve_executable(value: str | Path, capability: str) -> str:
    """Resolve either an executable path or a command available on PATH."""
    raw = str(value).strip()
    if not raw:
        raise MissingCapabilityError(f"A configured {capability} executable is empty.")
    path = Path(raw).expanduser()
    if path.is_file():
        return str(path.resolve())
    found = shutil.which(raw)
    if found:
        return found
    raise MissingCapabilityError(
        f"The configured {capability} executable was not found: {raw}. "
        "Provide another implementation or update the capability configuration."
    )


def resolve_media_processor(explicit: str | Path | None = None, adjacent_to: str | Path | None = None) -> str:
    """Find the media-processing implementation only when media is needed."""
    configured = explicit or os.environ.get("ENGLISH_VIDEO_ANKI_FFMPEG")
    if configured:
        return resolve_executable(configured, "media processor")
    found = shutil.which("ffmpeg")
    if found:
        return found
    if adjacent_to:
        sibling = Path(adjacent_to).expanduser().resolve().parent / "ffmpeg.exe"
        if sibling.is_file():
            return str(sibling)
    raise MissingCapabilityError(
        "This job needs a media processor capable of extracting audio and making a looping visual. "
        "No implementation was found. If you already have another ffmpeg-compatible tool, provide its executable; "
        "otherwise consider installing ffmpeg."
    )


def resolve_word_tts(explicit: str | Path | None = None) -> str:
    """Find the optional PowerShell-compatible local TTS implementation."""
    configured = explicit or os.environ.get("ENGLISH_VIDEO_ANKI_TTS")
    if configured:
        return resolve_executable(configured, "PowerShell-compatible word text-to-speech")
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    raise MissingCapabilityError(
        "This job needs a word-audio text-to-speech capability. "
        "No local PowerShell-compatible TTS implementation was found. If you already have a TTS service or app, "
        "provide its generated word audio in the lookup input; otherwise consider enabling a local TTS tool."
    )


def download_source(
    url: str,
    download_dir: Path,
    yt_dlp_path: Path | None = None,
    cookies: Path | None = None,
    allow_missing_subtitles: bool = False,
) -> tuple[Path, Path | None]:
    """Download one video plus English VTT subtitles through yt-dlp."""
    validate_cookies(cookies, url)
    download_dir.mkdir(parents=True, exist_ok=True)
    configured_downloader = yt_dlp_path or os.environ.get("ENGLISH_VIDEO_ANKI_DOWNLOADER")
    if configured_downloader:
        downloader = [resolve_executable(configured_downloader, "video downloader")]
    elif importlib.util.find_spec("yt_dlp") is not None:
        downloader = [sys.executable, "-m", "yt_dlp"]
    else:
        raise MissingCapabilityError(
            "This URL job needs a video downloader (yt-dlp-compatible). "
            "No configured downloader was found. If you already have another downloader, provide its executable; "
            "otherwise consider installing yt-dlp."
        )
    cookie_args = ["--cookies", str(cookies)] if cookies else []
    command = [
        *downloader, *cookie_args, "--no-playlist",
        "--format", "bv*+ba/b", "--merge-output-format", "mp4",
        "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
        "--sub-format", "vtt", "--convert-subs", "vtt",
        "--output", str(download_dir / "source.%(ext)s"), url,
    ]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Python is unavailable for yt-dlp. Install Python 3 and run: py -m pip install -U yt-dlp") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Download failed. Install or update yt-dlp with: py -m pip install -U yt-dlp"
        ) from exc

    videos = [
        candidate for candidate in download_dir.glob("source.*")
        if candidate.suffix.lower() in {".mp4", ".mkv", ".webm", ".m4v", ".mov"}
    ]
    # yt-dlp may still write a provider's default track when the requested
    # English language is unavailable. Do not silently parse zh/ai-zh as if it
    # were English: that must leave room for the explicit local ASR fallback.
    subtitles = []
    for candidate in download_dir.glob("source*.vtt"):
        suffix = candidate.stem.removeprefix("source.").casefold()
        if suffix == "en" or suffix.startswith(("en-", "en_")):
            subtitles.append(candidate)
    if not videos:
        raise RuntimeError(f"yt-dlp finished without a supported video in {download_dir}")
    if not subtitles and not allow_missing_subtitles:
        raise RuntimeError(
            "yt-dlp downloaded the video but no English VTT subtitle track. "
            "Provide --subtitles manually, use a source with English captions, or explicitly pass --transcribe."
        )
    video = max(videos, key=lambda candidate: candidate.stat().st_mtime_ns)
    # Prefer original English captions over automatic/country variants when both exist.
    subtitles.sort(key=lambda candidate: ("en-orig" not in candidate.name, "en." not in candidate.name, candidate.name))
    return video, subtitles[0] if subtitles else None


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"Unsupported subtitle timestamp: {value!r}")


def parse_subtitles(path: Path) -> list[Cue]:
    """Parse the common SRT/VTT cue shape without depending on a package."""
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
        start_raw, end_raw = [part.strip().split()[0] for part in line.split("-->", 1)]
        start, end = parse_timestamp(start_raw), parse_timestamp(end_raw)
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        raw = " ".join(text_lines)
        text = re.sub(r"<[^>]+>", "", raw).strip()
        # YouTube emits a 10 ms duplicate "settled" cue after every live cue.
        # Keeping it would create false sentence starts and duplicate word times.
        if text and end - start >= 0.1:
            cues.append(Cue(len(cues) + 1, start, end, html.unescape(text), raw))
    return cues


def parse_timed_tokens(path: Path) -> list[TimedToken]:
    """Read YouTube-style inline VTT timestamps into a de-duplicated word stream."""
    lines = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
    tokens: list[TimedToken] = []
    timestamp = re.compile(r"<(\d\d:\d\d:\d\d\.\d\d\d)>")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" not in line and i + 1 < len(lines) and "-->" in lines[i + 1]:
            i += 1
            line = lines[i].strip()
        if "-->" not in line:
            i += 1
            continue
        start_raw = line.split("-->", 1)[0].strip().split()[0]
        cue_start = parse_timestamp(start_raw)
        i += 1
        raw: list[str] = []
        while i < len(lines) and lines[i].strip():
            raw.append(lines[i].strip())
            i += 1
        current_time = cue_start
        anchored = False
        for part in timestamp.split(" ".join(raw)):
            match = timestamp.fullmatch(part)
            if match:
                current_time = parse_timestamp(match.group(1))
                anchored = True
                continue
            clean = html.unescape(re.sub(r"<[^>]+>", "", part)).strip()
            for token in clean.split():
                if token and token not in {">>", "[music]"}:
                    tokens.append(TimedToken(current_time, token, anchored))

    # The rolling captions repeat each timestamped token in overlapping cues.
    # Prefer the real timestamped occurrence over a cue-start approximation.
    deduped: dict[tuple[int, str], TimedToken] = {}
    for token in tokens:
        key = (round(token.time * 1000), normalize(token.text))
        existing = deduped.get(key)
        if existing is None or (token.anchored and not existing.anchored):
            deduped[key] = token
    return sorted(deduped.values(), key=lambda token: (token.time, not token.anchored))


def normalize(value: str) -> str:
    value = html.unescape(value).lower()
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def load_lookups(path: Path) -> list[Lookup]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Lookups JSON must be an array")
    out: list[Lookup] = []
    for item in raw:
        if isinstance(item, str):
            item = {"term": item}
        if not isinstance(item, dict) or not str(item.get("term", "")).strip():
            raise ValueError("Each lookup must contain a non-empty 'term'")
        audio_value = item.get("word_audio")
        word_audio = Path(str(audio_value)).expanduser() if audio_value else None
        if word_audio is not None and not word_audio.is_absolute():
            word_audio = path.parent / word_audio
        if word_audio is not None and not word_audio.is_file():
            raise ValueError(f"word_audio file does not exist: {word_audio}")
        out.append(
            Lookup(
                term=str(item["term"]).strip(),
                sentence=str(item["sentence"]).strip() or None
                if item.get("sentence") is not None
                else None,
                definition=str(item.get("definition", "")).strip(),
                part_of_speech=str(item.get("part_of_speech", "")).strip(),
                note=str(item.get("note", "")).strip(),
                timestamp=float(item["timestamp"])
                if item.get("timestamp") is not None
                else None,
                word_audio=word_audio,
            )
        )
    return out


def find_cue(lookup: Lookup, cues: list[Cue]) -> Cue | None:
    target_sentence = normalize(lookup.sentence or "")
    target_term = normalize(lookup.term)
    candidates = cues
    if target_sentence:
        sentence_matches = [c for c in cues if target_sentence in normalize(c.text)]
        if sentence_matches:
            candidates = sentence_matches
    term_matches = [c for c in candidates if target_term in normalize(c.text)]
    if term_matches:
        candidates = term_matches
    elif target_sentence:
        return candidates[0] if candidates else None
    else:
        return None
    if lookup.timestamp is None:
        return candidates[0]
    return min(candidates, key=lambda cue: abs(((cue.start + cue.end) / 2) - lookup.timestamp))


def sentence_spans(cues: list[Cue], timed_tokens: list[TimedToken]) -> list[SentenceSpan]:
    """Join rolling caption cues into complete spoken sentences.

    The VTT's presentation cues are not semantic units: one sentence commonly
    crosses two or three cues.  Fushi lets a learner explicitly compose
    neighboring cues; for a batch tool we derive that same complete-sentence
    range from punctuation, with a conservative 12-second guardrail.
    """
    spans: list[SentenceSpan] = []
    words: list[str] = []
    start: float | None = None
    end: float | None = None
    for cue in cues:
        text = re.sub(r"\s+", " ", cue.text).strip()
        if not text or normalize(text) in {"music", ""}:
            continue
        if start is None:
            start = cue.start
        words.append(text)
        end = cue.end
        joined = " ".join(words)
        # A full stop/question/exclamation closes a semantic sentence.  If a
        # transcript misses punctuation, avoid creating an oversized card.
        if re.search(r"[.!?](?:[\"')\]]*)$", text) or (end - start) >= 12:
            clean = re.sub(r"\s+", " ", joined).strip()
            spans.append(SentenceSpan(clean, start, end))
            words, start, end = [], None, None
    if words and start is not None and end is not None:
        spans.append(SentenceSpan(re.sub(r"\s+", " ", " ".join(words)).strip(), start, end))

    # VTT's visible rolling cue text is an imperfect semantic transcription,
    # but word-level timestamps make the media range exact.  Map each span's
    # first/last normalized word to the nearest actual spoken timestamp.
    resolved: list[SentenceSpan] = []
    for span in spans:
        span_words = [normalize(word) for word in re.findall(r"[\w']+", span.text, flags=re.UNICODE)]
        in_range = [t for t in timed_tokens if span.start - 0.05 <= t.time <= span.end + 0.05]
        if span_words and in_range:
            first = next((t for t in in_range if normalize(t.text) == span_words[0]), None)
            last = next((t for t in reversed(in_range) if normalize(t.text).rstrip(".!?") == span_words[-1].rstrip(".!?")), None)
            resolved.append(SentenceSpan(span.text, first.time if first else span.start, (last.time + 0.42) if last else span.end))
        else:
            resolved.append(span)
    return resolved


def find_sentence(lookup: Lookup, spans: list[SentenceSpan]) -> SentenceSpan | None:
    target_term = normalize(lookup.term)
    candidates = [span for span in spans if re.search(rf"\b{re.escape(target_term)}\b", normalize(span.text))]
    if lookup.sentence:
        expected = normalize(lookup.sentence)
        exact = [span for span in candidates if expected in normalize(span.text)]
        if exact:
            candidates = exact
    if not candidates:
        return None
    if lookup.timestamp is None:
        return candidates[0]
    return min(candidates, key=lambda span: abs(((span.start + span.end) / 2) - lookup.timestamp))


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = re.findall(r"[\w']+|[.!?]", phrase, flags=re.UNICODE)
    # Lookup sentences are intentionally cleaned (they omit filler such as
    # "uh"), while the source VTT retains it.  Let punctuation, VTT tags and
    # common filler bridge adjacent meaningful words without crossing a final
    # sentence mark.
    separator = r"(?:<[^>]+>|[^\w.!?]+|\b(?:uh|um)\b)*"
    return re.compile(separator.join(re.escape(word) for word in words), re.IGNORECASE)


def _last_timestamp_before(text: str, position: int) -> float | None:
    marks = list(re.finditer(r"<(\d\d:\d\d:\d\d\.\d\d\d)>", text[:position]))
    return parse_timestamp(marks[-1].group(1)) if marks else None


def resolve_sentence_span(lookup: Lookup, cue: Cue, cues: list[Cue]) -> SentenceSpan:
    """Resolve a complete input sentence to the exact VTT media window.

    A VTT cue is merely a moving screen caption.  The supplied lookup sentence
    is therefore the semantic authority; timestamp markers are used only to
    locate its first word and the marker immediately after its final word.
    """
    sentence = lookup.sentence or cue.text
    first_word = re.search(r"[\w']+", sentence, re.UNICODE)
    start = cue.start
    if first_word:
        first = re.compile(rf"\b{re.escape(first_word.group(0))}\b", re.IGNORECASE)
        nearby = [c for c in cues if cue.start - 6 <= c.start <= cue.end + 2]
        for candidate in nearby:
            match = first.search(candidate.raw)
            if match:
                marked = _last_timestamp_before(candidate.raw, match.start())
                start = marked if marked is not None else candidate.start
                break
    end = cue.end
    phrase = _phrase_pattern(sentence)
    nearby = [c for c in cues if cue.start - 2 <= c.start <= cue.end + 4]
    for candidate in nearby:
        match = phrase.search(candidate.raw)
        if match:
            following = re.search(r"<(\d\d:\d\d:\d\d\.\d\d\d)>", candidate.raw[match.end():])
            end = parse_timestamp(following.group(1)) if following else candidate.end
            break
    return SentenceSpan(sentence, max(0.0, start), max(start + 0.2, end))


def stable_clip_media_name(
    source_video: Path,
    term: str,
    start: float,
    end: float,
    kind: str,
    suffix: str,
) -> str:
    """Name generated media by source identity, not nondeterministic codec bytes."""
    stat = source_video.stat()
    identity = "\0".join(
        [
            str(source_video.resolve()), str(stat.st_size), str(stat.st_mtime_ns),
            term.casefold(), f"{start:.3f}", f"{end:.3f}", kind,
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"codex_{kind}_{digest}{suffix}"


def write_clip_subtitle(path: Path, text: str, duration: float) -> None:
    def stamp(seconds: float) -> str:
        millis = round(seconds * 1000)
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        whole, millis = divmod(millis, 1000)
        return f"{hours:02}:{minutes:02}:{whole:02},{millis:03}"

    path.write_text(
        f"1\n{stamp(0.05)} --> {stamp(max(0.1, duration - 0.05))}\n{text}\n",
        encoding="utf-8",
    )


def run_ffmpeg(
    video: Path,
    start: float,
    end: float,
    output: Path,
    *,
    subtitle: Path | None = None,
    audio_only: bool = False,
    executable: str | None = None,
) -> None:
    ffmpeg = executable or resolve_media_processor()
    duration = max(0.2, end - start)
    if audio_only:
        args = [
            ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-vn", "-acodec", "libmp3lame", "-q:a", "4", str(output),
        ]
    elif output.suffix.lower() == ".webm":
        args = [
            ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-c:v", "libvpx-vp9", "-crf", "33", "-b:v", "0", "-row-mt", "1",
            "-c:a", "libopus", "-b:a", "80k",
        ]
        if subtitle is not None:
            args.extend([
                "-vf",
                f"subtitles='{subtitle.as_posix()}':force_style='FontSize=18,Outline=2,Shadow=1,Alignment=2'",
            ])
        args.append(str(output))
    else:
        args = [
            ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", str(output),
        ]
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run_visual_gif(video: Path, start: float, end: float, output: Path, *, executable: str | None = None) -> None:
    """Produce Fushi-style silent loopable visual media, never a video player."""
    duration = max(0.2, end - start)
    # Fushi's GIF extractor uses fps + width + palettegen/paletteuse.  These
    # standard-tier values keep sentence context visible without making Anki's
    # media collection impractically large.
    # Fushi's lower mobile-safe tier: enough motion for context while avoiding
    # oversized GIFs and long Anki syncs on source video with high resolution.
    filter_graph = "fps=6,scale=360:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a"
    args = [
        executable or resolve_media_processor(), "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}",
        "-an", "-filter_complex", filter_graph, "-loop", "0", str(output),
    ]
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def synthesize_word_audio(text: str, output: Path, *, tts_executable: str | None = None) -> None:
    """Use the installed offline English voice for a durable local word prompt."""
    escaped_text = text.replace("'", "''")
    escaped_output = str(output.resolve()).replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.SelectVoice('Microsoft Zira Desktop'); "
        "$voice.SetOutputToWaveFile('" + escaped_output + "'); "
        "$voice.Speak('" + escaped_text + "'); $voice.Dispose()"
    )
    subprocess.run([tts_executable or resolve_word_tts(), "-NoProfile", "-Command", command], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def dictionary_lookup(term: str) -> DictionaryEntry:
    """Read ECDICT from local SQLite. Card creation never needs the network."""
    entry = local_dictionary_lookup(term)
    return format_local_dictionary_entry(entry) if entry else DictionaryEntry()


def format_local_dictionary_entry(entry: LocalDictionaryEntry) -> DictionaryEntry:
    english = html.escape(entry.definition.replace("\\n", "\n")).replace("\n", "<br>")
    metadata: list[str] = []
    if entry.collins:
        metadata.append(f"Collins {entry.collins} star")
    if entry.oxford:
        metadata.append("Oxford 3000")
    if entry.tag:
        metadata.append(entry.tag)
    if entry.frq:
        metadata.append(f"COCA #{entry.frq}")
    if entry.bnc:
        metadata.append(f"BNC #{entry.bnc}")
    details = f'<div class="dict-meta">{" · ".join(html.escape(item) for item in metadata)}</div>' if metadata else ""
    definitions = ""
    if english:
        definitions += f'<section><div class="dict-pos">English</div><div>{english}</div></section>'
    return DictionaryEntry(
        pronunciation=f"/{entry.phonetic}/" if entry.phonetic else "",
        definitions_html=definitions + details,
        synonyms="",
        translation=entry.translation.replace("\\n", "\n"),
    )


class AnkiConnect:
    def __init__(self, url: str) -> None:
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
        # Loopback AnkiConnect can read a local path, avoiding base64 inflation.
        self.call("storeMediaFile", filename=filename, path=str(path.resolve()))

    def ensure_english_model(self, model_name: str) -> None:
        fields = [
            "Word", "Pronunciation", "Sentence", "WordAudio", "SentenceAudio", "Picture",
            "Definition", "Part of Speech", "Source", "Note",
        ]
        front = """<div class=\"word\">{{Word}}</div>
<div class=\"sentence prompt\">{{Sentence}}</div>"""
        # Anki queues [sound:] references in document order.  Keeping these
        # adjacent, word first, makes the answer reveal play word -> sentence.
        back = """{{FrontSide}}
<div class=\"answer\">
{{#Picture}}<div class=\"media\">{{Picture}}</div>{{/Picture}}
<div class=\"audio-sequence\">{{WordAudio}}{{SentenceAudio}}</div>
{{#Pronunciation}}<div class=\"pronunciation\">{{Pronunciation}}</div>{{/Pronunciation}}
<div class=\"definition\">{{Definition}}</div>
{{#Part of Speech}}<div class=\"pos\">{{Part of Speech}}</div>{{/Part of Speech}}
{{#Note}}<div class=\"note\">{{Note}}</div>{{/Note}}
<div class=\"source\">{{Source}}</div></div>"""
        css = """.card { font-family: Arial, sans-serif; text-align: center; color: #202124; background: #faf8f1; padding: 24px; }
.word { font-size: 42px; color: #159ac5; margin: 8px 0; }.pronunciation { color: #666; margin-top: 8px; }
.sentence { font-size: 25px; line-height: 1.45; margin: 22px auto; max-width: 850px; }.sentence b { color: #159ac5; }.sentence.prompt { font-size: 23px; }
.answer { margin-top: 26px; border-top: 1px solid #d8d3c8; }.media { margin: 20px auto 8px; }.clip-gif { width: min(100%, 640px); max-height: 400px; object-fit: contain; border-radius: 6px; background: #111; }
.audio-sequence { min-height: 28px; margin: 12px 0; }.definition { text-align: left; font-size: 18px; color: #333; max-width: 720px; margin: 22px auto; padding: 16px; background: #fff; border-left: 4px solid #159ac5; }
.definition h3 { margin: 0 0 8px; color: #159ac5; font-size: 16px; }.definition section { margin-top: 12px; }.dict-pos, .pos { color: #e66a25; font-weight: bold; }.note { margin-top: 14px; }.source { color: #777; font-size: 13px; margin-top: 16px; }"""
        try:
            existing = self.call("modelNames") or []
            if model_name not in existing:
                self.call(
                    "createModel",
                    modelName=model_name,
                    inOrderFields=fields,
                    css=css,
                    isCloze=False,
                    cardTemplates=[{"Name": "English video", "Front": front, "Back": back}],
                )
            else:
                actual = self.call("modelFieldNames", modelName=model_name) or []
                missing = [field for field in fields if field not in actual]
                if missing:
                    raise RuntimeError(
                        f"Note type {model_name!r} exists but is missing fields: {', '.join(missing)}"
                    )
                self.call(
                    "updateModelTemplates",
                    model={"name": model_name, "templates": {"English video": {"Front": front, "Back": back}}},
                )
                self.call("updateModelStyling", model={"name": model_name, "css": css})
        except RuntimeError as exc:
            if "already exists" not in str(exc).lower():
                raise


def html_field(value: str) -> str:
    return html.escape(value, quote=False).replace("\n", "<br>")


def sentence_field(sentence: str, term: str) -> str:
    escaped = html_field(sentence)
    pattern = re.compile(rf"\b({re.escape(term)})\b", re.IGNORECASE)
    return pattern.sub(r"<b>\1</b>", escaped, count=1)


def load_capability_config(path: Path | None) -> dict[str, Any]:
    """Load optional implementation paths without making any tool mandatory."""
    if path is None:
        return {}
    if not path.is_file():
        raise ValueError(f"Capability configuration does not exist: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Capability configuration is not valid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Capability configuration must be a JSON object")
    return raw


def configured_value(config: dict[str, Any], section: str, *keys: str) -> str | None:
    """Read a scalar implementation setting from a capability config."""
    value: Any = config.get(section, {})
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, (str, Path)) and str(candidate).strip():
                return str(candidate)
    for key in keys:
        candidate = config.get(key)
        if isinstance(candidate, (str, Path)) and str(candidate).strip():
            return str(candidate)
    return None


def build(args: argparse.Namespace) -> list[dict[str, Any]]:
    capability_config = load_capability_config(getattr(args, "capabilities", None))
    downloader = getattr(args, "yt_dlp", None) or configured_value(
        capability_config, "download", "downloader", "yt_dlp", "executable", "path"
    )
    ffmpeg_setting = getattr(args, "ffmpeg", None) or configured_value(
        capability_config, "media", "ffmpeg", "media_processor", "executable", "path"
    )
    tts_setting = getattr(args, "tts", None) or configured_value(
        capability_config, "word_audio", "tts", "text_to_speech", "executable", "path"
    )
    transcription_config = capability_config.get("transcription", capability_config.get("transcribe", {}))
    if not isinstance(transcription_config, dict):
        raise ValueError("The transcription capability configuration must be a JSON object")
    captioner_engine = getattr(args, "captioner_engine", None) or transcription_config.get("engine") or transcription_config.get("executable") or transcription_config.get("path")
    captioner_model_dir = getattr(args, "captioner_model_dir", None) or transcription_config.get("model_dir")
    captioner_model = getattr(args, "captioner_model", None) or transcription_config.get("model") or "large-v2"
    if args.url:
        if args.video or args.subtitles:
            raise ValueError("Use either --url or both --video and --subtitles, not both inputs")
        video, subtitles = download_source(
            args.url, args.download_dir, downloader, args.cookies, args.transcribe,
        )
    else:
        if not args.video or (not args.subtitles and not args.transcribe):
            raise ValueError("Provide --url, or provide --video with --subtitles (or explicitly pass --transcribe)")
        video, subtitles = args.video, args.subtitles
    video = video.resolve()
    subtitle_source = "provided"
    if subtitles is None:
        from transcribe_english import transcribe

        subtitles = transcribe(
            video,
            args.output / "transcript",
            captioner_engine,
            captioner_model_dir,
            captioner_model,
        )
        subtitle_source = "ai-transcribed"
    subtitles = subtitles.resolve()
    cues = parse_subtitles(subtitles)
    timed_tokens = parse_timed_tokens(subtitles)
    lookups = load_lookups(args.lookups)
    if not cues:
        raise ValueError("No subtitle cues found")
    # Resolve optional implementations once per job, after subtitle matching
    # prerequisites are known. A dry run still reports missing media/TTS
    # capabilities only when at least one lookup can be matched.
    matched_items = [item for item in lookups if find_cue(item, cues) is not None]
    ffmpeg = resolve_media_processor(ffmpeg_setting, captioner_engine) if not args.dry_run and matched_items else None
    tts = (
        resolve_word_tts(tts_setting)
        if not args.dry_run and any(item.word_audio is None for item in matched_items)
        else None
    )
    args.output.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for item in lookups:
        cue = find_cue(item, cues)
        record: dict[str, Any] = {"term": item.term, "matched": cue is not None}
        if cue is None:
            record["error"] = "No matching subtitle cue"
            manifest.append(record)
            continue
        span = resolve_sentence_span(item, cue, cues)
        start = max(0.0, span.start - args.padding)
        end = span.end + args.padding
        stem = f"{len(manifest) + 1:03d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', item.term).strip('_') or 'term'}"
        visual = args.output / f"{stem}.gif"
        sentence_audio = args.output / f"{stem}-sentence.mp3"
        word_wav = args.output / f"{stem}-word.wav"
        word_audio = args.output / f"{stem}-word.mp3"
        if not args.dry_run:
            run_visual_gif(video, start, end, visual, executable=ffmpeg)
            run_ffmpeg(video, start, end, sentence_audio, audio_only=True, executable=ffmpeg)
            if item.word_audio is not None:
                if item.word_audio.suffix.casefold() == ".mp3":
                    shutil.copyfile(item.word_audio, word_audio)
                else:
                    run_ffmpeg(item.word_audio, 0.0, 99.0, word_audio, audio_only=True, executable=ffmpeg)
            else:
                synthesize_word_audio(item.term, word_wav, tts_executable=tts)
                run_ffmpeg(word_wav, 0.0, 99.0, word_audio, audio_only=True, executable=ffmpeg)
            word_wav.unlink(missing_ok=True)
        record.update({
            "cue": cue.__dict__, "sentence": span.text, "start": start, "end": end,
            "visual": str(visual), "word_audio": str(word_audio), "sentence_audio": str(sentence_audio),
            "subtitle_source": subtitle_source,
            "word_audio_source": "provided" if item.word_audio is not None else "local-tts",
        })
        record["lookup"] = {
            **item.__dict__,
            "word_audio": str(item.word_audio) if item.word_audio is not None else None,
        }
        manifest.append(record)
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        return manifest
    if args.prepare_only:
        return manifest
    if not args.deck or not args.model:
        raise ValueError(
            "--deck and --model are required "
            "unless --dry-run is used"
        )
    anki = AnkiConnect(args.anki_url)
    anki.call("version")
    anki.call("createDeck", deck=args.deck)
    anki.ensure_english_model(args.model)
    existing_ids = anki.call("findNotes", query=f'deck:"{args.deck}"') or []
    existing_notes = anki.call("notesInfo", notes=existing_ids) if existing_ids else []
    note_for_word = {
        str(note["fields"]["Word"]["value"]).strip().lower(): note
        for note in existing_notes
        if note.get("modelName") in {args.model, "English Video"} and "Word" in note.get("fields", {})
    }
    for record in manifest:
        if not record.get("matched"):
            continue
        visual_path = Path(record["visual"])
        word_audio_path = Path(record["word_audio"])
        sentence_audio_path = Path(record["sentence_audio"])
        lookup = record["lookup"]
        visual_name = stable_clip_media_name(
            video, lookup["term"], record["start"], record["end"], "picture", ".gif"
        )
        word_audio_name = stable_clip_media_name(video, lookup["term"], 0.0, 0.0, "word_audio", ".mp3")
        sentence_audio_name = stable_clip_media_name(
            video, lookup["term"], record["start"], record["end"], "sentence_audio", ".mp3"
        )
        anki.store(visual_name, visual_path)
        anki.store(word_audio_name, word_audio_path)
        anki.store(sentence_audio_name, sentence_audio_path)
        sentence = record["sentence"]
        dictionary = dictionary_lookup(lookup["term"])
        chinese = html_field(lookup.get("definition") or dictionary.translation or "(add Chinese definition)")
        dictionary_html = dictionary.definitions_html or "<p>Dictionary lookup unavailable.</p>"
        definition = f"<h3>中文释义</h3><div>{chinese}</div><h3>English dictionary</h3>{dictionary_html}"
        note_parts = [html_field(lookup.get("note", ""))]
        if dictionary.synonyms:
            note_parts.append(f"<b>Synonyms:</b> {html_field(dictionary.synonyms)}")
        fields = {
            "Word": html_field(lookup["term"]),
            "Pronunciation": html_field(dictionary.pronunciation),
            "Picture": f'<img class="clip-gif" src="{visual_name}" alt="Context visual">',
            "WordAudio": f"[sound:{word_audio_name}]",
            "SentenceAudio": f"[sound:{sentence_audio_name}]",
            "Sentence": sentence_field(sentence, lookup["term"]),
            "Definition": definition,
            "Part of Speech": html_field(lookup.get("part_of_speech", "")),
            "Source": html_field(f"{args.source_title} ({record['start']:.1f}s)"),
            "Note": "<br>".join(part for part in note_parts if part),
        }
        existing_note = note_for_word.get(lookup["term"].lower())
        if existing_note:
            existing_note_id = existing_note["noteId"]
            if existing_note.get("modelName") != args.model:
                # This AnkiConnect build exposes model migration as
                # updateNoteModel(note=...), not the newer changeModel API.
                anki.call("updateNoteModel", note={
                    "id": existing_note_id, "modelName": args.model, "fields": fields,
                    "tags": existing_note.get("tags", []),
                })
            else:
                anki.call("updateNoteFields", note={"id": existing_note_id, "fields": fields})
            record["note_id"] = existing_note_id
        else:
            record["note_id"] = anki.call(
                "addNote",
                note={"deckName": args.deck, "modelName": args.model, "fields": fields, "tags": args.tag},
            )
        record["media"] = {"picture": visual_name, "word_audio": word_audio_name, "sentence_audio": sentence_audio_name}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", help="Video URL; downloads video and English VTT subtitles through yt-dlp")
    p.add_argument("--download-dir", type=Path, default=Path("downloads"), help="Directory used with --url")
    p.add_argument(
        "--downloader", "--yt-dlp", dest="yt_dlp",
        help="Optional downloader executable/command (yt-dlp is one adapter; defaults to capability config/PATH)",
    )
    p.add_argument("--cookies", type=Path, help="Optional Netscape cookies.txt for authenticated sources such as Bilibili")
    p.add_argument("--video", type=Path, help="Local video; requires --subtitles unless --transcribe is set")
    p.add_argument("--subtitles", type=Path, help="Local timed SRT/VTT; requires --video")
    p.add_argument("--transcribe", action="store_true", help="Use the configured local ASR adapter when no English subtitles are available")
    p.add_argument(
        "--transcriber-engine", "--captioner-engine", dest="captioner_engine",
        help="Optional ASR executable; VideoCaptioner's Faster-Whisper adapter is one implementation",
    )
    p.add_argument("--transcriber-model-dir", "--captioner-model-dir", dest="captioner_model_dir", help="Directory containing the selected ASR model")
    p.add_argument("--transcriber-model", "--captioner-model", dest="captioner_model", help="Installed ASR model name (default: large-v2 for VideoCaptioner)")
    p.add_argument("--media-processor", "--ffmpeg", dest="ffmpeg", help="Optional ffmpeg-compatible media processor path or command; defaults to capability config/PATH")
    p.add_argument("--word-tts", "--tts", dest="tts", help="Optional word-audio TTS executable; defaults to capability config or local PowerShell")
    p.add_argument("--capabilities", type=Path, help="Optional JSON mapping capability names to user-provided implementations")
    p.add_argument("--lookups", type=Path, required=True, help="JSON array of terms/definitions")
    p.add_argument("--output", type=Path, default=Path("anki-output"))
    p.add_argument("--padding", type=float, default=0.35)
    p.add_argument("--dry-run", action="store_true", help="Match cues and write manifest without media/Anki writes")
    p.add_argument("--prepare-only", action="store_true", help="Create media and manifest without contacting Anki")
    p.add_argument("--anki-url", default="http://127.0.0.1:8765")
    p.add_argument("--deck", default="English::Video")
    p.add_argument("--model", default="English Video Fushi", help="Dedicated English video note type")
    p.add_argument("--front-field", help=argparse.SUPPRESS)
    p.add_argument("--back-field", help=argparse.SUPPRESS)
    p.add_argument("--source-title", default="Video source")
    p.add_argument("--tag", action="append", default=["codex_video"])
    return p


if __name__ == "__main__":
    try:
        result = build(parser().parse_args())
        print(json.dumps({"count": len(result), "matched": sum(1 for r in result if r.get("matched"))}, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
