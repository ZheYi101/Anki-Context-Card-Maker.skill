"""Explicitly download/import an offline dictionary provider.

This helper is never called by card generation. Run it only when the user
chooses to install a local dictionary. A local source file can be supplied to
avoid network access entirely.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from local_dictionary import PROVIDER_SOURCES, install


DOWNLOADS = {
    "ecdict": {
        "url": "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv",
        "filename": "ecdict.csv",
        "compressed": False,
        "license": "ECDICT is distributed under its upstream MIT license; verify terms before redistribution.",
    },
    "cc-cedict": {
        "url": "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
        "filename": "cedict.txt",
        "compressed": True,
        "license": "CC-CEDICT data is distributed under CC BY-SA 4.0; verify terms before redistribution.",
    },
    "jmdict": {
        "url": "https://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz",
        "filename": "JMdict_e.xml",
        "compressed": True,
        "license": "JMdict data is subject to the EDRDG licence; verify terms before redistribution.",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, output: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "anki-context-card-maker/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, output.open("wb") as target:
        shutil.copyfileobj(response, target)


def prepare_source(provider: str, source: Path | None, data_dir: Path) -> tuple[Path, str]:
    spec = DOWNLOADS[provider]
    data_dir.mkdir(parents=True, exist_ok=True)
    if source:
        if not source.is_file():
            raise FileNotFoundError(f"Source file does not exist: {source}")
        return source, "local file supplied by user"
    with tempfile.TemporaryDirectory(prefix="anki-dictionary-") as temporary:
        archive = Path(temporary) / f"{provider}.download"
        download(spec["url"], archive)
        target = data_dir / spec["filename"]
        if spec["compressed"]:
            with gzip.open(archive, "rb") as compressed, target.open("wb") as plain:
                shutil.copyfileobj(compressed, plain)
        else:
            shutil.copyfile(archive, target)
    return target, spec["url"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(DOWNLOADS), required=True)
    parser.add_argument("--source", type=Path, help="Use an already downloaded source file")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    args = parser.parse_args()

    source, origin = prepare_source(args.provider, args.source, args.data_dir)
    database = args.data_dir / {"ecdict": "ecdict.sqlite", "cc-cedict": "cc-cedict.sqlite", "jmdict": "jmdict.sqlite"}[args.provider]
    entries = install(source, database, args.provider)
    manifest_path = args.data_dir / "dictionary-manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[args.provider] = {
        "source": origin,
        "source_sha256": sha256(source),
        "database": str(database),
        "entries": entries,
        "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "license": DOWNLOADS[args.provider]["license"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest[args.provider], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
