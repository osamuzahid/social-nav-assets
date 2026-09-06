#!/usr/bin/env python3
"""Stage a remaining CUCR world (house_museum / small_house / small_warehouse).

Usage:
  python3 tools/convert/prepare_cucr_world.py --world house_museum \\
    --cucr-root /tmp/cucr_remaining_src/cucr_worlds \\
    --out /tmp/cucr_remaining_src/obj/house_museum
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cucr_port_common import WORLD_SPECS, prepare_world  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world", required=True, choices=sorted(WORLD_SPECS))
    ap.add_argument(
        "--cucr-root",
        type=Path,
        default=Path("/tmp/cucr_remaining_src/cucr_worlds"),
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or Path(f"/tmp/cucr_remaining_src/obj/{args.world}")
    return prepare_world(args.world, args.cucr_root, out)


if __name__ == "__main__":
    raise SystemExit(main())
