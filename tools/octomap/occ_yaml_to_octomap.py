#!/usr/bin/env python3
"""Convert a ROS occupancy YAML+PNG into an octomap .bt for ESC.

ESC's mapper loads OcTree from offline_octomap_path and, when that path is
set, does not subscribe to live /scan. Occupied cells become voxels at a few
z heights so GridMapOctomapConverter can project a 2D 'full' layer.

Usage:
  python3 tools/esc_isaac/occ_yaml_to_octomap.py \\
    /path/to/museum.yaml tools/esc_isaac/maps/museum.bt
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

import yaml
from PIL import Image


def _occupied_xy(yaml_path: str) -> tuple[float, list[tuple[float, float]]]:
    yaml_path = os.path.abspath(yaml_path)
    with open(yaml_path, encoding="utf-8") as fh:
        meta = yaml.safe_load(fh)
    image_name = meta["image"]
    image_path = (
        image_name
        if os.path.isabs(image_name)
        else os.path.join(os.path.dirname(yaml_path), image_name)
    )
    res = float(meta["resolution"])
    origin = meta.get("origin", [0.0, 0.0, 0.0])
    origin_x, origin_y = float(origin[0]), float(origin[1])
    occ_thresh = float(meta.get("occupied_thresh", 0.65))
    negate = int(meta.get("negate", 0))

    img = Image.open(image_path).convert("L")
    w, h = img.size
    pix = img.tobytes()
    pts: list[tuple[float, float]] = []
    for row in range(h):
        off = row * w
        for col in range(w):
            v = pix[off + col]
            occ_prob = (v / 255.0) if negate else ((255.0 - v) / 255.0)
            if occ_prob <= occ_thresh:
                continue
            x = origin_x + (col + 0.5) * res
            y = origin_y + ((h - 1 - row) + 0.5) * res
            pts.append((x, y))
    return res, pts


def _ensure_helper(src: str, bin_path: str) -> str:
    if os.path.isfile(bin_path) and os.path.getmtime(bin_path) >= os.path.getmtime(src):
        return bin_path
    cmd = ["g++", "-O2", "-o", bin_path, src, "-loctomap", "-loctomath"]
    subprocess.run(cmd, check=True)
    return bin_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("yaml_path")
    ap.add_argument("out_bt")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, "occ_xy_to_bt.cpp")
    helper = os.path.join(here, ".occ_xy_to_bt")
    _ensure_helper(src, helper)

    res, pts = _occupied_xy(args.yaml_path)
    if not pts:
        print("FAIL: no occupied cells", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.out_bt)) or ".", exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        for x, y in pts:
            tmp.write(f"{x:.5f} {y:.5f}\n")
        xyz = tmp.name
    try:
        with open(xyz, encoding="utf-8") as stdin:
            subprocess.run(
                [helper, f"{res:.6f}", os.path.abspath(args.out_bt)],
                stdin=stdin,
                check=True,
            )
    finally:
        os.unlink(xyz)
    size = os.path.getsize(args.out_bt)
    print(f"wrote {args.out_bt} occupied_xy={len(pts)} bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
