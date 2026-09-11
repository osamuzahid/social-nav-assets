"""Layout and freeze-identity checks (no Isaac Sim, no GPU)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORLDS = (
    "museum",
    "hospital",
    "office",
    "bookstore",
    "house_museum",
    "small_house",
    "small_warehouse",
)

BT_SHA256 = {
    "bookstore.bt": "8fdc78eb0cb1be37b63c4305813f2888a60b32427ce0d86cf67a8851ba322aff",
    "hospital.bt": "3ab424aced976aa040f35ee81c191a0fb0a7b698ede4fbdeb106f957c4893396",
    "house_museum.bt": "0aaecba7e0c878823406f77497efb0fbb3a3b7750b47c04b6b01b2baa2404070",
    "museum.bt": "c6961741f8e6731b613876e78bf5efd3ba767a678e0a1de98c67b290d62de9a8",
    "office.bt": "91c290b582ba30a7ff4f534fe407e895a876538e61878938f0621b7506c50a35",
    "small_house.bt": "3c5267e08dee09a755ba85e7fd6cb622fe0d955c6ff46f824bbc73f33e28ea9a",
    "small_warehouse.bt": "8a047874102dd7d69a057293af2551be11945586ee0bf165e3baf8f74c6aac63",
}

MAP_PNG_SHA256 = {
    "museum.png": "dc047a54ab8a8297184cdecd602615c9dd2ef19a77d5ef15c8f377fa0a7d6469",
    "hospital.png": "5a5a6846770ad42b73013c6f12f74e5d74dbb50a0c38e7b9b1b5848ae932433d",
    "office.png": "e69e5dcf398b205de80cb21f55172af1c93971737c866204a2cd113f74b9dfeb",
    "bookstore.png": "ce00fdd8b5c00472fbd1dc1b8234c53270e87a1b291b4b44e442c0e6427a7f81",
    "house_museum.png": "807ef043219d3df8a2d256d844ded3a6217178ebde2fcc4fd22b4969c46359cb",
    "small_house.png": "ca0d26ddbd44dfe8df16a1c4de9ccdd77b088fba577995d30c3c759235dddf12",
    "small_warehouse.png": "0a8b29b4e6a91c6dc473e49645357a09aa2191b117030676b2fa87d3fc53b839",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def test_seven_occupancy_maps_parse() -> None:
    for world in WORLDS:
        yaml_path = ROOT / "maps" / f"{world}.yaml"
        png_path = ROOT / "maps" / f"{world}.png"
        meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert meta["image"] == f"{world}.png"
        assert png_path.is_file()
        assert float(meta["resolution"]) > 0
        assert len(meta["origin"]) >= 2
        assert "occupied_thresh" in meta


@pytest.mark.parametrize("name,digest", MAP_PNG_SHA256.items())
def test_occupancy_png_matches_freeze(name: str, digest: str) -> None:
    assert _sha256(ROOT / "maps" / name) == digest


@pytest.mark.parametrize("name,digest", BT_SHA256.items())
def test_octomap_matches_freeze(name: str, digest: str) -> None:
    assert _sha256(ROOT / "maps" / name) == digest


def test_no_platform_files() -> None:
    assert not list(ROOT.rglob("robot.yaml"))
    assert not list(ROOT.rglob("*_crowd.yaml"))
    assert not list(ROOT.rglob("*_hop.sh"))


def test_licences_present() -> None:
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "LICENSES" / "Apache-2.0.txt").is_file()
    assert (ROOT / "LICENSES" / "CLEAR_BSD_Hello_Robot.md").is_file()
    assert (ROOT / "robots" / "reachy" / "LICENSE").is_file()
    assert (ROOT / "robots" / "stretch" / "LICENSE.md").is_file()
    apache = (ROOT / "robots" / "reachy" / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in apache
    assert "Version 2.0" in apache


def test_tracked_text_has_no_lab_home() -> None:
    skip_suffix = {".usd", ".usda", ".png", ".bt", ".stl", ".STL", ".dae"}
    hits = []
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    for rel in tracked:
        if rel.startswith("tests/") or "/tests/" in rel:
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix in skip_suffix:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "/home/" in text:
            hits.append(rel)
    assert hits == []


def test_convert_scripts_use_repo_worlds() -> None:
    museum = (ROOT / "tools" / "convert" / "isaac_convert_museum.py").read_text(
        encoding="utf-8"
    )
    assert "SOCIAL_NAV_WORLDS" in museum
    assert '"src", "worlds"' not in museum
    assert "src/worlds" not in museum.split("OUT_DIR", 1)[1][:400]


def test_sources_lock_records_unknown_cucr_sha() -> None:
    lock = yaml.safe_load((ROOT / "sources.lock.yaml").read_text(encoding="utf-8"))
    assert lock["cucr_worlds"]["commit"] == "unknown"
    assert lock["reachy"]["commit"] == "unknown"
    assert lock["freeze"]["wrapper_isaac_6_0_jazzy"].startswith("64e8bb3")
