#!/usr/bin/env python3
"""Stage CUCR hospital.world props as OBJs + instances.json for Isaac convert.

No Isaac required. Reads a cucr_worlds_hospital checkout, exports DAE→OBJ via
Assimp when needed, copies native OBJs, writes a placement manifest.

Usage:
  python3 tools/convert/prepare_hospital_props.py \\
    --cucr-root /tmp/cucr_hospital_src/cucr_worlds \\
    --out /tmp/cucr_hospital_src/obj_props
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SKIP_MODELS = {
    "sun",
    "ground_plane",
    # Building already converted in v1.
    "aws_robomaker_hospital_floor_01_floor",
    "aws_robomaker_hospital_floor_01_walls",
    "aws_robomaker_hospital_nursesstation_01",
    # Opaque ceiling blocks top-down demos; not needed for Nav2/social-nav.
    "aws_robomaker_hospital_floor_01_ceiling",
    # Upper floor not used in CUCR map / v1 demos.
    "aws_robomaker_hospital_floor_02_floor",
    "aws_robomaker_hospital_floor_02_walls",
    "aws_robomaker_hospital_floor_02_ceiling",
}

# People meshes are not placed in hospital.world; HuNav supplies agents.
HUMANISH = re.compile(
    r"(patient|visitor|elder|female|male|person|scrub)", re.I
)


def _parse_includes(world_text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"<include>(.*?)</include>", world_text, re.S):
        block = m.group(1)
        uri_m = re.search(r"<uri>\s*(.*?)\s*</uri>", block)
        if not uri_m:
            continue
        uri = uri_m.group(1)
        if "model://" not in uri:
            continue
        model = uri.split("model://", 1)[1].strip()
        pose_m = re.search(r"<pose[^>]*>\s*(.*?)\s*</pose>", block)
        name_m = re.search(r"<name>\s*(.*?)\s*</name>", block)
        if pose_m:
            nums = [float(x) for x in pose_m.group(1).split()]
            while len(nums) < 6:
                nums.append(0.0)
            pose = nums[:6]
            # Garbage Z in a few AWS exports — pin to floor.
            if abs(pose[2]) > 50.0:
                pose[2] = 0.0
        else:
            pose = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        out.append(
            {
                "model": model,
                "name": name_m.group(1).strip() if name_m else None,
                "pose": pose,
            }
        )
    return out


def _mesh_scale_from_sdf(model_dir: Path) -> list[float]:
    """Gazebo <mesh><scale> — many AWS chairs/IV stands are authored oversized."""
    sdf = model_dir / "model.sdf"
    if not sdf.is_file():
        return [1.0, 1.0, 1.0]
    text = sdf.read_text(errors="ignore")
    m = re.search(
        r"<mesh>\s*.*?<scale>\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*</scale>",
        text,
        re.S,
    )
    if not m:
        m = re.search(
            r"<scale>\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*</scale>",
            text,
        )
    if not m:
        return [1.0, 1.0, 1.0]
    return [float(m.group(1)), float(m.group(2)), float(m.group(3))]


def _pick_visual_mesh(model_dir: Path) -> Path | None:
    meshes = model_dir / "meshes"
    if not meshes.is_dir():
        return None
    cands: list[Path] = []
    for p in meshes.iterdir():
        if not p.is_file():
            continue
        low = p.name.lower()
        if p.suffix.lower() not in {".obj", ".dae"}:
            continue
        if "collision" in low or low.endswith("_col.obj") or "_col." in low:
            continue
        cands.append(p)
    if not cands:
        return None
    # Prefer *visual* then plain .obj then anything.
    cands.sort(
        key=lambda p: (
            0 if "visual" in p.name.lower() else 1,
            0 if p.suffix.lower() == ".obj" else 1,
            len(p.name),
            p.name.lower(),
        )
    )
    return cands[0]


def _strip_assimp_color_ws(dae_path: Path, tmp_dae: Path) -> Path:
    """Assimp chokes on leading whitespace after some <color> tags."""
    text = dae_path.read_text(errors="ignore")
    fixed = re.sub(r"(<color[^>]*>)\s+", r"\1", text)
    if fixed != text:
        tmp_dae.write_text(fixed)
        return tmp_dae
    return dae_path


def _stage_model(model: str, model_dir: Path, out_dir: Path) -> Path | None:
    mesh = _pick_visual_mesh(model_dir)
    if mesh is None:
        print(f"[skip] no visual mesh: {model}", flush=True)
        return None
    dest_dir = out_dir / model
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_obj = dest_dir / f"{model}.obj"

    # Copy sibling textures / mtl beside the mesh into dest.
    for src in mesh.parent.iterdir():
        if src.is_file() and src.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".mtl",
            ".tif",
            ".tiff",
        }:
            shutil.copy2(src, dest_dir / src.name)
    # AWS residential packs textures under materials/textures/
    tex_root = model_dir / "materials" / "textures"
    if tex_root.is_dir():
        for src in tex_root.iterdir():
            if src.is_file():
                shutil.copy2(src, dest_dir / src.name)

    if mesh.suffix.lower() == ".obj":
        shutil.copy2(mesh, dest_obj)
        # Prefer matching mtl name
        mtl = mesh.with_suffix(".mtl")
        if mtl.is_file():
            shutil.copy2(mtl, dest_dir / mtl.name)
        _flatten_mtl_texture_paths(dest_dir)
        return dest_obj

    # DAE → OBJ
    tmp_dae = dest_dir / f"_tmp_{mesh.name}"
    src_dae = _strip_assimp_color_ws(mesh, tmp_dae)
    cmd = ["assimp", "export", str(src_dae), str(dest_obj)]
    print(f"[assimp] {model}: {mesh.name}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if tmp_dae.is_file():
        tmp_dae.unlink()
    if r.returncode != 0 or not dest_obj.is_file():
        print(
            f"[fail] assimp {model}: {r.stderr[-500:] if r.stderr else r.stdout[-500:]}",
            flush=True,
        )
        return None
    _flatten_mtl_texture_paths(dest_dir)
    return dest_obj


def _flatten_mtl_texture_paths(dest_dir: Path) -> None:
    """Rewrite map_* paths to basenames (textures already copied beside OBJ)."""
    for mtl in dest_dir.glob("*.mtl"):
        text = mtl.read_text(errors="ignore")
        fixed = re.sub(
            r"^(\s*map_\w+\s+)(.+)$",
            lambda m: m.group(1) + Path(m.group(2).strip()).name,
            text,
            flags=re.M,
        )
        if fixed != text:
            mtl.write_text(fixed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cucr-root",
        type=Path,
        default=Path("/tmp/cucr_hospital_src/cucr_worlds"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/cucr_hospital_src/obj_props"),
    )
    args = ap.parse_args()

    hosp = args.cucr_root / "cucr_worlds_hospital"
    world_path = hosp / "worlds" / "hospital.world"
    models_root = hosp / "models"
    if not world_path.is_file():
        print(f"missing world: {world_path}", file=sys.stderr)
        return 2

    includes = _parse_includes(world_path.read_text())
    args.out.mkdir(parents=True, exist_ok=True)

    by_model: dict[str, list[dict]] = defaultdict(list)
    for inc in includes:
        m = inc["model"]
        if m in SKIP_MODELS or HUMANISH.search(m):
            continue
        by_model[m].append(inc)

    staged = {}
    failed = []
    for model in sorted(by_model):
        model_dir = models_root / model
        if not model_dir.is_dir():
            print(f"[missing] model dir {model}", flush=True)
            failed.append(model)
            continue
        obj = _stage_model(model, model_dir, args.out)
        if obj is None:
            failed.append(model)
        else:
            staged[model] = str(obj)

    instances = []
    for model, incs in by_model.items():
        if model not in staged:
            continue
        scale = _mesh_scale_from_sdf(models_root / model)
        for i, inc in enumerate(incs):
            x, y, z, roll, pitch, yaw = inc["pose"]
            instances.append(
                {
                    "model": model,
                    "prim_suffix": f"{i}",
                    "translate": [x, y, z],
                    "rpy_rad": [roll, pitch, yaw],
                    "scale": scale,
                    "obj": staged[model],
                }
            )

    manifest = {
        "source_world": str(world_path),
        "models_staged": sorted(staged.keys()),
        "models_failed": failed,
        "instance_count": len(instances),
        "instances": instances,
    }
    man_path = args.out / "instances.json"
    man_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[done] staged={len(staged)} failed={len(failed)} "
        f"instances={len(instances)} -> {man_path}",
        flush=True,
    )
    return 0 if staged and not failed else (0 if staged else 1)


if __name__ == "__main__":
    raise SystemExit(main())
