#!/usr/bin/env python3
"""Stage CUCR cucr_worlds_bookstore (AWS RoboMaker retail) for Isaac convert.

Parses bookstore.world model wrappers (pose lives on the parent <model>, not
inside <include>). Skips the ceiling slab. Keeps SDF mesh scale and Collada
unit (cm → m) so compose does not drop a 0.01 the way hospital #41 did.

Usage:
  python3 tools/convert/prepare_bookstore.py \\
    --cucr-root /tmp/cucr_bookstore_src/cucr_worlds \\
    --out /tmp/cucr_bookstore_src/obj
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
    "aws_robomaker_retail_RetailShopCeiling_01",
}

HUMANISH = re.compile(
    r"(casual_|elegant_|actor|person|human|turtlebot|servicebot)", re.I
)


def _parse_world_models(world_text: str) -> list[dict]:
    """Pose is a sibling of <include> on the wrapping <model> (not inside include)."""
    out = []
    for m in re.finditer(
        r"<model\s+name=['\"]([^'\"]+)['\"]>(.*?)</model>", world_text, re.S
    ):
        inst_name, body = m.group(1), m.group(2)
        uri_m = re.search(r"<uri>\s*model://(.*?)\s*</uri>", body)
        if not uri_m:
            continue
        model = uri_m.group(1).strip()
        pose_m = re.search(r"<pose[^>]*>\s*(.*?)\s*</pose>", body)
        if pose_m:
            nums = [float(x) for x in pose_m.group(1).split()]
            while len(nums) < 6:
                nums.append(0.0)
            pose = nums[:6]
            if abs(pose[2]) > 50.0:
                pose[2] = 0.0
        else:
            pose = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        out.append({"model": model, "name": inst_name, "pose": pose})
    return out


def _mesh_scale_from_sdf(model_dir: Path) -> list[float]:
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


def _dae_unit_meter(mesh: Path) -> float:
    if mesh.suffix.lower() not in {".dae"}:
        return 1.0
    head = mesh.read_text(errors="ignore")[:4000]
    m = re.search(r'<unit[^>]*meter="([\d.eE+-]+)"', head)
    return float(m.group(1)) if m else 1.0


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
    text = dae_path.read_text(errors="ignore")
    fixed = re.sub(r"(<color[^>]*>)\s+", r"\1", text)
    if fixed != text:
        tmp_dae.write_text(fixed)
        return tmp_dae
    return dae_path


def _flatten_mtl_texture_paths(dest_dir: Path) -> None:
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


def _stage_model(model: str, model_dir: Path, out_dir: Path) -> tuple[Path | None, float]:
    mesh = _pick_visual_mesh(model_dir)
    if mesh is None:
        print(f"[skip] no visual mesh: {model}", flush=True)
        return None, 1.0
    unit = _dae_unit_meter(mesh)
    dest_dir = out_dir / model
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_obj = dest_dir / f"{model}.obj"

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
    tex_root = model_dir / "materials" / "textures"
    if tex_root.is_dir():
        for src in tex_root.iterdir():
            if src.is_file():
                shutil.copy2(src, dest_dir / src.name)

    if mesh.suffix.lower() == ".obj":
        shutil.copy2(mesh, dest_obj)
        mtl = mesh.with_suffix(".mtl")
        if mtl.is_file():
            shutil.copy2(mtl, dest_dir / mtl.name)
        _flatten_mtl_texture_paths(dest_dir)
        return dest_obj, unit

    tmp_dae = dest_dir / f"_tmp_{mesh.name}"
    src_dae = _strip_assimp_color_ws(mesh, tmp_dae)
    print(f"[assimp] {model}: {mesh.name} unit={unit}", flush=True)
    r = subprocess.run(
        ["assimp", "export", str(src_dae), str(dest_obj)],
        capture_output=True,
        text=True,
    )
    if tmp_dae.is_file():
        tmp_dae.unlink()
    if r.returncode != 0 or not dest_obj.is_file():
        print(
            f"[fail] assimp {model}: {(r.stderr or r.stdout)[-500:]}",
            flush=True,
        )
        return None, unit
    _flatten_mtl_texture_paths(dest_dir)
    return dest_obj, unit


def _obj_xy_extent(obj: Path) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    with obj.open(errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                xs.append(float(p[1]))
                ys.append(float(p[2]))
                if len(xs) > 80000:
                    break
    if not xs:
        return 0.0, 0.0
    return max(xs) - min(xs), max(ys) - min(ys)


def _crop_map(src_pgm: Path, src_yaml: Path, out_png: Path, out_yaml: Path) -> None:
    """Crop the 4000² gmapping canvas to occupied cells + 2 m pad."""
    import numpy as np
    from PIL import Image

    with src_pgm.open("rb") as f:
        f.readline()  # P5
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = (int(x) for x in line.split())
        f.readline()  # maxval
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = data[: w * h].reshape((h, w))
    occupied = img < 50
    ys, xs = np.nonzero(occupied)
    if len(xs) == 0:
        shutil.copy2(src_pgm, out_png)
        shutil.copy2(src_yaml, out_yaml)
        print("[maps] no occupied cells; copied full PGM", flush=True)
        return
    pad_px = int(2.0 / 0.05)
    x0 = max(0, int(xs.min()) - pad_px)
    x1 = min(w, int(xs.max()) + pad_px + 1)
    y0 = max(0, int(ys.min()) - pad_px)
    y1 = min(h, int(ys.max()) + pad_px + 1)
    crop = img[y0:y1, x0:x1]
    Image.fromarray(crop).save(out_png)
    origin_x = -100.0 + x0 * 0.05
    # PGM row 0 is top; ROS map origin is bottom-left of full image.
    origin_y = -100.0 + (h - y1) * 0.05
    out_yaml.write_text(
        f"image: {out_png.name}\n"
        f"resolution: 0.050000\n"
        f"origin: [{origin_x:.6f}, {origin_y:.6f}, 0.000000]\n"
        f"negate: 0\n"
        f"occupied_thresh: 0.65\n"
        f"free_thresh: 0.196\n"
    )
    print(
        f"[maps] cropped {w}x{h} -> {crop.shape[1]}x{crop.shape[0]} "
        f"origin=[{origin_x:.2f}, {origin_y:.2f}]",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cucr-root",
        type=Path,
        default=Path("/tmp/cucr_bookstore_src/cucr_worlds"),
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/cucr_bookstore_src/obj"),
    )
    args = ap.parse_args()

    root = args.cucr_root / "cucr_worlds_bookstore"
    world_path = root / "worlds" / "bookstore.world"
    models_root = root / "models"
    if not world_path.is_file():
        print(f"missing world: {world_path}", file=sys.stderr)
        return 2

    includes = _parse_world_models(world_path.read_text())
    args.out.mkdir(parents=True, exist_ok=True)

    by_model: dict[str, list[dict]] = defaultdict(list)
    skipped = []
    for inc in includes:
        m = inc["model"]
        if m in SKIP_MODELS or HUMANISH.search(m):
            skipped.append(m)
            continue
        by_model[m].append(inc)

    staged = {}
    units = {}
    failed = []
    for model in sorted(by_model):
        model_dir = models_root / model
        if not model_dir.is_dir():
            print(f"[missing] model dir {model}", flush=True)
            failed.append(model)
            continue
        obj, unit = _stage_model(model, model_dir, args.out)
        if obj is None:
            failed.append(model)
        else:
            staged[model] = str(obj)
            units[model] = unit

    instances = []
    for model, incs in by_model.items():
        if model not in staged:
            continue
        sdf_scale = _mesh_scale_from_sdf(models_root / model)
        unit = units.get(model, 1.0)
        # If Assimp already baked Collada metres into OBJ, do not also apply 0.01.
        dx, dy = _obj_xy_extent(Path(staged[model]))
        baked = max(dx, dy) < 80.0 and unit < 0.5
        apply_unit = 1.0 if baked else unit
        scale = [sdf_scale[i] * apply_unit for i in range(3)]
        print(
            f"[scale] {model} sdf={sdf_scale} dae_unit={unit} "
            f"obj_xy=({dx:.1f},{dy:.1f}) apply={scale}",
            flush=True,
        )
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
                    "kind": "mesh",
                }
            )

    maps_src = root / "maps"
    if (maps_src / "map.pgm").is_file():
        try:
            _crop_map(
                maps_src / "map.pgm",
                maps_src / "map.yaml",
                args.out / "bookstore.png",
                args.out / "bookstore.yaml",
            )
        except Exception as exc:
            print(f"[maps] crop failed ({exc}); copy raw", flush=True)
            shutil.copy2(maps_src / "map.pgm", args.out / "bookstore.png")
            shutil.copy2(maps_src / "map.yaml", args.out / "bookstore.yaml")

    xs = [i["translate"][0] for i in instances]
    ys = [i["translate"][1] for i in instances]
    manifest = {
        "source_world": str(world_path),
        "models_staged": sorted(staged.keys()),
        "models_failed": failed,
        "models_skipped": sorted(set(skipped)),
        "instance_count": len(instances),
        "instances": instances,
        "pose_xy_span": [min(xs), min(ys), max(xs), max(ys)] if xs else [],
    }
    man_path = args.out / "instances.json"
    man_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[done] staged={len(staged)} failed={len(failed)} "
        f"instances={len(instances)} skip={sorted(set(skipped))} -> {man_path}",
        flush=True,
    )
    return 0 if staged else 1


if __name__ == "__main__":
    raise SystemExit(main())
