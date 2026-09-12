"""Local dictionary providers for multilingual Anki card generation.

ECDICT is the default provider. CC-CEDICT and JMdict can be imported into the
same small SQLite schema with ``install --provider``; card generation never
needs an online dictionary request.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import sys
import re
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


ECDICT_SOURCE_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
PROVIDER_SOURCES = {
    "ecdict": ECDICT_SOURCE_URL,
    "cc-cedict": "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
    "jmdict": "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz",
}
DEFAULT_DATABASE = Path(__file__).resolve().parent / "data" / "ecdict.sqlite"


@dataclass(frozen=True)
class LocalDictionaryEntry:
    word: str
    phonetic: str
    definition: str
    translation: str
    pos: str
    collins: int
    oxford: int
    tag: str
    bnc: int
    frq: int
    exchange: str


def normalize(word: str) -> str:
    return " ".join(word.casefold().strip().split())


def install(csv_path: Path, database_path: Path = DEFAULT_DATABASE, provider: str = "ecdict") -> int:
    """Convert ECDICT CSV, CC-CEDICT text, or JMdict XML into local SQLite."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE entries (
                word_norm TEXT PRIMARY KEY,
                word TEXT NOT NULL,
                phonetic TEXT NOT NULL,
                definition TEXT NOT NULL,
                translation TEXT NOT NULL,
                pos TEXT NOT NULL,
                collins INTEGER NOT NULL,
                oxford INTEGER NOT NULL,
                tag TEXT NOT NULL,
                bnc INTEGER NOT NULL,
                frq INTEGER NOT NULL,
                exchange_text TEXT NOT NULL
            );
            CREATE INDEX entries_word_index ON entries(word);
            """
        )
        rows: list[tuple[object, ...]] = []

        def add(word: str, phonetic: str = "", definition: str = "", translation: str = "", pos: str = "") -> None:
            word = word.strip()
            if not word:
                return
            rows.append((normalize(word), word, phonetic, definition, translation, pos, 0, 0, provider, 0, 0, ""))
            if len(rows) >= 5_000:
                connection.executemany("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                rows.clear()

        if provider == "ecdict":
            with csv_path.open("r", encoding="utf-8", newline="") as source:
                for row in csv.DictReader(source):
                    add(row.get("word", ""), row.get("phonetic", ""), row.get("definition", ""), row.get("translation", ""), row.get("pos", ""))
        elif provider == "cc-cedict":
            pattern = re.compile(r"^(\S+)\s+(\S+)\s+\[(.*?)\]\s+/(.*)/$")
            for raw in csv_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                match = pattern.match(line)
                if match:
                    _traditional, simplified, pinyin, definitions = match.groups()
                    add(simplified, pinyin, translation=definitions.replace("/", "; "))
        elif provider == "jmdict":
            for _, element in ET.iterparse(csv_path, events=("end",)):
                if element.tag != "entry":
                    continue
                words = [node.text or "" for node in element.findall("k_ele/keb")]
                if not words:
                    words = [node.text or "" for node in element.findall("r_ele/reb")]
                readings = [node.text or "" for node in element.findall("r_ele/reb")]
                glosses = [node.text or "" for node in element.findall("sense/gloss")]
                for word in words[:1]:
                    add(word, readings[0] if readings else "", translation="; ".join(glosses))
                element.clear()
        else:
            raise ValueError(f"Unsupported dictionary provider: {provider}")
        if rows:
            connection.executemany(
                "INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
            )
        metadata = {
            "name": provider.upper(),
            "provider": provider,
            "source_url": PROVIDER_SOURCES.get(provider, ""),
            "source_file": csv_path.name,
            "source_bytes": str(csv_path.stat().st_size),
            "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
            "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "license_note": "ECDICT: MIT; CC-CEDICT: CC BY-SA 4.0; JMdict: EDRDG licence. Verify terms before redistribution.",
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
        return connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    finally:
        connection.close()


def lookup(word: str, database_path: Path = DEFAULT_DATABASE, language: str = "en-US") -> LocalDictionaryEntry | None:
    if not database_path.exists():
        return None
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            """SELECT word, phonetic, definition, translation, pos, collins, oxford,
                      tag, bnc, frq, exchange_text
               FROM entries WHERE word_norm = ?""",
            (normalize(word),),
        ).fetchone()
    finally:
        connection.close()
    return LocalDictionaryEntry(*row) if row else None


def provider_for_language(language: str, dictionaries_dir: Path | None = None) -> Path:
    """Resolve a local database by language without making it a hard dependency."""
    root = dictionaries_dir or DEFAULT_DATABASE.parent
    family = language.casefold().split("-")[0]
    names = {"en": "ecdict.sqlite", "zh": "cc-cedict.sqlite", "ja": "jmdict.sqlite"}
    return root / names.get(family, "ecdict.sqlite")


def lookup_provider(word: str, language: str, dictionaries_dir: Path | None = None) -> LocalDictionaryEntry | None:
    """Look up a term using the configured local provider for its language."""
    return lookup(word, provider_for_language(language, dictionaries_dir), language=language)


def metadata(database_path: Path = DEFAULT_DATABASE) -> dict[str, str]:
    connection = sqlite3.connect(database_path)
    try:
        return dict(connection.execute("SELECT key, value FROM metadata"))
    finally:
        connection.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--csv", type=Path, required=True)
    install_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    install_parser.add_argument("--provider", choices=sorted(PROVIDER_SOURCES), default="ecdict")
    query_parser = commands.add_parser("query")
    query_parser.add_argument("word")
    query_parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    if args.command == "install":
        count = install(args.csv, args.database, args.provider)
        print(json.dumps({"entries": count, "database": str(args.database), "provider": args.provider, "source_url": PROVIDER_SOURCES[args.provider]}))
    else:
        entry = lookup(args.word, args.database)
        print(json.dumps(entry.__dict__ if entry else None, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
