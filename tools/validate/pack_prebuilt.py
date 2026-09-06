#!/usr/bin/env python3
"""Pack freeze-identical prebuilt USDs into dist/prebuilt-assets-v0.1.0-candidate.tar.zst."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
BUNDLE = DIST / "prebuilt-assets-v0.1.0-candidate.tar.zst"
WORLDS = (
    "museum",
    "hospital",
    "office",
    "bookstore",
    "house_museum",
    "small_house",
    "small_warehouse",
)


def _require(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"missing {path.relative_to(ROOT)} — copy freeze USDs first")


def main() -> int:
    members: list[Path] = []
    for world in WORLDS:
        usd = ROOT / "worlds" / f"{world}.usd"
        assets = ROOT / "worlds" / "assets" / world
        _require(usd)
        _require(assets)
        members.append(usd)
        members.extend(p for p in assets.rglob("*") if p.is_file())
    for robot in ("stretch", "reachy"):
        usd = ROOT / "robots" / robot / f"{robot}.usd"
        payloads = ROOT / "robots" / robot / "payloads"
        _require(usd)
        _require(payloads)
        members.append(usd)
        members.extend(p for p in payloads.rglob("*") if p.is_file())

    DIST.mkdir(exist_ok=True)
    tmp = DIST / "prebuilt-assets-v0.1.0-candidate.tar"
    with tarfile.open(tmp, "w") as tar:
        for path in sorted(members, key=lambda p: str(p.relative_to(ROOT))):
            tar.add(path, arcname=str(path.relative_to(ROOT)))
    subprocess.run(["zstd", "-f", "-19", "-T0", str(tmp), "-o", str(BUNDLE)], check=True)
    tmp.unlink()
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    print(f"wrote {BUNDLE.relative_to(ROOT)} sha256={digest} files={len(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
