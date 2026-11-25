"""Fetch a static ffmpeg binary into ``assets/ffmpeg`` for a release.

Only ``ffmpeg`` is needed: the source duration used to size the preview cut is
read with mutagen, so the ~78 MB static ``ffprobe`` that ships next to it is
never installed.

ffmpeg.org does not publish binaries itself; its download page links to
third-party static builds. This script pins one build per target:

- linux-x86_64 and windows-x64: BtbN's automated **LGPL** builds, verified
  against the release's own ``checksums.sha256``. ``latest`` moves with each
  autobuild, so the checksum is fetched at download time rather than pinned.
- macos-x86_64: evermeet.cx 9.1 (GPL build, x86_64), pinned by SHA-256.
- macos-arm64: OSXExperts 9.1 (GPL build, arm64), pinned by SHA-256.

Usage::

    .venv/bin/python scripts/fetch_ffmpeg.py             # native target
    .venv/bin/python scripts/fetch_ffmpeg.py --target macos-arm64 --dest assets/ffmpeg
    .venv/bin/python scripts/fetch_ffmpeg.py --force     # replace existing binaries

The binaries are git-ignored; only the release workflow and a local packaging
test need them. See ``assets/README.md`` for the license notes.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"

TARGETS: dict[str, dict] = {
    "linux-x86_64": {
        "archives": [
            {
                "url": f"{BTBN}/ffmpeg-master-latest-linux64-lgpl.tar.xz",
                "checksums": f"{BTBN}/checksums.sha256",
            }
        ],
        "members": {"ffmpeg": "ffmpeg"},
        "license": "LGPL build by BtbN (ffmpeg.org download link)",
    },
    "windows-x64": {
        "archives": [
            {
                "url": f"{BTBN}/ffmpeg-master-latest-win64-lgpl.zip",
                "checksums": f"{BTBN}/checksums.sha256",
            }
        ],
        "members": {"ffmpeg": "ffmpeg.exe"},
        "license": "LGPL build by BtbN (ffmpeg.org download link)",
    },
    "macos-x86_64": {
        "archives": [
            {
                "url": "https://evermeet.cx/ffmpeg/ffmpeg-9.1.zip",
                "sha256": "9962f7b3c01af9ac89673c91df9ef998343a280ac0857c295e2d2a301820598a",
                "member": "ffmpeg",
            },
        ],
        "members": {"ffmpeg": "ffmpeg"},
        "license": "GPL build by evermeet.cx (ffmpeg.org download link)",
    },
    "macos-arm64": {
        "archives": [
            {
                "url": "https://www.osxexperts.net/ffmpeg91arm.zip",
                "sha256": "2d645bb619ba294ae7a4f7a01aa251d549610bfdcd82c7ec03fc963afcb59d5a",
                "member": "ffmpeg",
            },
        ],
        "members": {"ffmpeg": "ffmpeg"},
        "license": "GPL build by OSXExperts.net (ffmpeg.org download link)",
    },
}


def native_target() -> str:
    """Map the current OS/arch to a key in TARGETS."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "windows" and machine in {"x86_64", "amd64"}:
        return "windows-x64"
    if system == "darwin":
        return "macos-arm64" if machine == "arm64" else "macos-x86_64"
    raise SystemExit(f"no pinned ffmpeg build for {system}/{machine}; pass --target")


def download(url: str, dest: Path) -> str:
    """Download ``url`` to ``dest`` and return its SHA-256 digest."""
    print(f"downloading {url}")
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=300) as response, dest.open("wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def upstream_checksum(archive_url: str, checksums_url: str) -> str:
    """Read the expected digest for ``archive_url`` from an upstream file."""
    with urllib.request.urlopen(checksums_url, timeout=60) as response:
        for line in response.read().decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == archive_url.rsplit("/", 1)[-1]:
                return parts[0]
    raise SystemExit(f"{archive_url} is missing from {checksums_url}")


def extract_member(archive: Path, wanted: str) -> bytes:
    """Return the bytes of the archive member whose basename is ``wanted``."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            names = [n for n in bundle.namelist() if "__MACOSX" not in n]
            match = next((n for n in names if n.rsplit("/", 1)[-1] == wanted), None)
            if match is None:
                raise SystemExit(f"{wanted} not found in {archive.name}")
            return bundle.read(match)
    with tarfile.open(archive) as bundle:
        names = [n for n in bundle.getnames() if n.rsplit("/", 1)[-1] == wanted]
        if not names:
            raise SystemExit(f"{wanted} not found in {archive.name}")
        extracted = bundle.extractfile(names[0])
        if extracted is None:
            raise SystemExit(f"{wanted} is not a regular file in {archive.name}")
        return extracted.read()


def fetch(target: str, dest: Path, force: bool) -> None:
    spec = TARGETS.get(target)
    if spec is None:
        raise SystemExit(f"unknown target {target!r}; choose from {', '.join(TARGETS)}")

    dest.mkdir(parents=True, exist_ok=True)
    existing = [dest / name for name in spec["members"].values() if (dest / name).exists()]
    if existing and not force:
        raise SystemExit(
            f"{dest} already contains {', '.join(p.name for p in existing)}; pass --force to replace"
        )

    sources: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ffmpeg-") as tmp:
        for archive_spec in spec["archives"]:
            url = archive_spec["url"]
            archive = Path(tmp) / url.rsplit("/", 1)[-1]
            actual = download(url, archive)
            expected = archive_spec.get("sha256") or upstream_checksum(
                url, archive_spec["checksums"]
            )
            if actual != expected:
                raise SystemExit(f"SHA-256 mismatch for {url}: {actual} != {expected}")
            print(f"verified SHA-256 {actual}")
            sources.append(f"{url}  sha256:{actual}")

        # Each per-archive spec names its own member; the LGPL bundle carries
        # more than we extract, so only the wanted members are written out.
        if "archives" in spec and all("member" in a for a in spec["archives"]):
            for archive_spec in spec["archives"]:
                url = archive_spec["url"]
                archive = Path(tmp) / url.rsplit("/", 1)[-1]
                name = archive_spec["member"]
                (dest / name).write_bytes(extract_member(archive, name))
        else:
            archive_spec = spec["archives"][0]
            archive = Path(tmp) / archive_spec["url"].rsplit("/", 1)[-1]
            for name, member in spec["members"].items():
                (dest / name).write_bytes(extract_member(archive, member))

    for name in spec["members"].values():
        path = dest / name
        path.chmod(path.stat().st_mode | 0o111)

    (dest / "SOURCE.txt").write_text(
        "\n".join(
            [
                f"target: {target}",
                f"license: {spec['license']}",
                *sources,
                "",
                "The binary is redistributed under its build's license; see",
                "assets/README.md for the source and license links.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"installed {', '.join(spec['members'].values())} into {dest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a static ffmpeg for a release")
    parser.add_argument("--target", choices=sorted(TARGETS), default=None)
    parser.add_argument("--dest", default="assets/ffmpeg", help="output directory")
    parser.add_argument("--force", action="store_true", help="replace existing binaries")
    args = parser.parse_args(argv)

    target = args.target or native_target()
    fetch(target, Path(args.dest), args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
