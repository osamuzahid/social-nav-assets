#!/usr/bin/env python3
"""Shared helpers for CUCR Gazebo → Isaac USD ports (bookstore-class worlds)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

HUMANISH = re.compile(
    r"(casual_|elegant_|actor|person|human|turtlebot|servicebot)", re.I
)

# Ceiling / roof slabs hide top-down views (hospital floor_01_ceiling, office cube).
SKIP_NAME_RE = re.compile(r"(ceiling|roof)", re.I)

WORLD_SPECS = {
    "house_museum": {
        "pkg": "cucr_worlds_house_museum",
        "kind": "single_mesh",
        "world_file": "worlds/house_museum.world",
        "mesh": "models/museum/meshes/house_museum.dae",
        "texture_dirs": [
            "models/museum/meshes",
            "models/museum/materials/textures",
        ],
        "map_pgm": "maps/map.pgm",
        "map_yaml": "maps/map.yaml",
        "crop_map": True,
        "z_offset": 0.1,
        "skip": set(),
    },
    "small_house": {
        "pkg": "cucr_worlds_small_house",
        "kind": "wrapped_models",
        "world_file": "worlds/small_house.world",
        "map_pgm": "maps/small_house.pgm",
        "map_yaml": "maps/small_house.yaml",
        "crop_map": False,
        "z_offset": 0.0,
        "skip": {"aws_robomaker_residential_RoomCeiling_01"},
    },
    "small_warehouse": {
        "pkg": "cucr_worlds_small_warehouse",
        "kind": "wrapped_models",
        "world_file": "worlds/small_warehouse.world",
        "map_pgm": "maps/small_warehouse.pgm",
        "map_yaml": "maps/small_warehouse.yaml",
        "crop_map": False,
        "z_offset": 0.0,
        "skip": {"aws_robomaker_warehouse_RoofB_01"},
    },
}


def parse_world_models(world_text: str) -> list[dict]:
    """Pose is a sibling of <include> on the wrapping <model> (not inside include)."""
    world_text = re.sub(r"<!--.*?-->", "", world_text, flags=re.S)
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


def mesh_scale_from_sdf(model_dir: Path) -> list[float]:
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


def dae_unit_meter(mesh: Path) -> float:
    if mesh.suffix.lower() not in {".dae"}:
        return 1.0
    head = mesh.read_text(errors="ignore")[:4000]
    m = re.search(r'<unit[^>]*meter="([\d.eE+-]+)"', head)
    return float(m.group(1)) if m else 1.0


def pick_visual_mesh(model_dir: Path) -> Path | None:
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


def strip_assimp_color_ws(dae_path: Path, tmp_dae: Path) -> Path:
    text = dae_path.read_text(errors="ignore")
    fixed = re.sub(r"(<color[^>]*>)\s+", r"\1", text)
    if fixed != text:
        tmp_dae.write_text(fixed)
        return tmp_dae
    return dae_path


def flatten_mtl_texture_paths(dest_dir: Path) -> None:
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


def _canon_mtl_token(name: str) -> str:
    """MTL treats '#' as a comment. Assimp emits `Material #112`, which Isaac then
    parses as a nameless/colliding `Material` and drops UsdUVTexture graphs."""
    token = name.replace("#", "_")
    token = re.sub(r"[^A-Za-z0-9_.]+", "_", token).strip("._")
    return token or "Material"


def sanitize_mtl_obj_material_names(dest_dir: Path) -> None:
    renamed = 0
    for mtl in dest_dir.glob("*.mtl"):
        lines = mtl.read_text(errors="ignore").splitlines(True)
        out = []
        for line in lines:
            if line.lower().startswith("newmtl "):
                raw = line.split(None, 1)[1].strip()
                canon = _canon_mtl_token(raw)
                if canon != raw:
                    renamed += 1
                out.append(f"newmtl {canon}\n")
            else:
                out.append(line)
        mtl.write_text("".join(out))
    for obj in dest_dir.glob("*.obj"):
        lines = obj.read_text(errors="ignore").splitlines(True)
        out = []
        for line in lines:
            if line.lower().startswith("usemtl "):
                raw = line.split(None, 1)[1].strip()
                out.append(f"usemtl {_canon_mtl_token(raw)}\n")
            else:
                out.append(line)
        obj.write_text("".join(out))
    if renamed:
        print(f"[mtl] sanitized {renamed} newmtl names with '#' in {dest_dir.name}", flush=True)


def fix_assimp_sampler_maps(dest_dir: Path) -> None:
    """Assimp writes `foo_jpeg--sampler.jpg` stubs; the real file is `foo.jpeg`."""
    files = {p.name.lower(): p.name for p in dest_dir.iterdir() if p.is_file()}
    for mtl in dest_dir.glob("*.mtl"):
        text = mtl.read_text(errors="ignore")

        def _fix(match: re.Match) -> str:
            prefix, name = match.group(1), match.group(2).strip()
            if name.lower() in files:
                return match.group(0)
            stem = re.sub(r"_jpeg--sampler\.jpg$", "", name, flags=re.I)
            for ext in (".jpeg", ".jpg", ".png"):
                cand = stem + ext
                if cand.lower() in files:
                    print(f"[mtl] sampler stub {name} -> {files[cand.lower()]}", flush=True)
                    return prefix + files[cand.lower()]
            return match.group(0)

        fixed = re.sub(r"^(\s*map_\w+\s+)(.+)$", _fix, text, flags=re.M)
        if fixed != text:
            mtl.write_text(fixed)


def strip_yup_roof_keep_sky(
    obj_path: Path,
    *,
    sky_mtls: set[str] | None = None,
    roof_y_min: float = 2.08,
    ny_abs: float = 0.40,
) -> None:
    """Drop high roof/ceiling slabs from a Y-up OBJ. Keep CUCR sunset sky (`material.001`).

    House-wing roof sits at y≈2.1–2.4 m (the old 2.5 m cut missed it). Gallery
    roof is higher. Sky faces are at y≈32–40 m and must not be removed.
    """
    sky_mtls = {
        s.lower() for s in (sky_mtls or {"material.001", "material_001", "material"})
    }
    text = obj_path.read_text(errors="ignore")
    verts: list[tuple[float, float, float]] = []
    cur_mtl = ""
    kept: list[str] = []
    n_roof = n_keep = n_sky = 0
    for line in text.splitlines(True):
        if line.startswith("v "):
            p = line.split()
            verts.append((float(p[1]), float(p[2]), float(p[3])))
            kept.append(line)
            continue
        if line.lower().startswith("usemtl "):
            cur_mtl = line.split(None, 1)[1].strip()
            kept.append(line)
            continue
        if not line.startswith("f "):
            kept.append(line)
            continue
        if cur_mtl.lower() in sky_mtls:
            n_sky += 1
            kept.append(line)
            continue
        ids = [int(tok.split("/")[0]) - 1 for tok in line.split()[1:]]
        if len(ids) >= 3 and verts:
            a, b, c = verts[ids[0]], verts[ids[1]], verts[ids[2]]
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            length = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            ny /= length
            ymid = sum(verts[i][1] for i in ids) / len(ids)
            if ymid >= roof_y_min and abs(ny) >= ny_abs:
                n_roof += 1
                continue
        kept.append(line)
        n_keep += 1
    obj_path.write_text("".join(kept))
    print(
        f"[strip] {obj_path.name}: keep_sky={n_sky} drop_roof={n_roof} keep_faces={n_keep}",
        flush=True,
    )


def copy_sidecar_textures(src_dirs: list[Path], dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src_dir in src_dirs:
        if not src_dir.is_dir():
            continue
        for src in src_dir.iterdir():
            if src.is_file() and src.suffix.lower() in {
                ".png",
                ".jpg",
                ".jpeg",
                ".mtl",
                ".tif",
                ".tiff",
            }:
                shutil.copy2(src, dest_dir / src.name)


def stage_mesh_file(mesh: Path, dest_dir: Path, model: str) -> tuple[Path | None, float]:
    unit = dae_unit_meter(mesh)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_obj = dest_dir / f"{model}.obj"
    copy_sidecar_textures([mesh.parent], dest_dir)

    if mesh.suffix.lower() == ".obj":
        shutil.copy2(mesh, dest_obj)
        mtl = mesh.with_suffix(".mtl")
        if mtl.is_file():
            shutil.copy2(mtl, dest_dir / mtl.name)
        flatten_mtl_texture_paths(dest_dir)
        sanitize_mtl_obj_material_names(dest_dir)
        fix_assimp_sampler_maps(dest_dir)
        return dest_obj, unit

    tmp_dae = dest_dir / f"_tmp_{mesh.name}"
    src_dae = strip_assimp_color_ws(mesh, tmp_dae)
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
    flatten_mtl_texture_paths(dest_dir)
    sanitize_mtl_obj_material_names(dest_dir)
    fix_assimp_sampler_maps(dest_dir)
    return dest_obj, unit


def stage_model(model: str, model_dir: Path, out_dir: Path) -> tuple[Path | None, float]:
    mesh = pick_visual_mesh(model_dir)
    if mesh is None:
        print(f"[skip] no visual mesh: {model}", flush=True)
        return None, 1.0
    dest_dir = out_dir / model
    tex_root = model_dir / "materials" / "textures"
    obj, unit = stage_mesh_file(mesh, dest_dir, model)
    if tex_root.is_dir():
        copy_sidecar_textures([tex_root], dest_dir)
    return obj, unit


def obj_xy_extent(obj: Path) -> tuple[float, float]:
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


def compose_scale(sdf_scale: list[float], unit: float, obj: Path) -> list[float]:
    """If Assimp already baked Collada metres into OBJ, do not also apply 0.01."""
    dx, dy = obj_xy_extent(obj)
    baked = max(dx, dy) < 80.0 and unit < 0.5
    apply_unit = 1.0 if baked else unit
    scale = [sdf_scale[i] * apply_unit for i in range(3)]
    print(
        f"[scale] {obj.parent.name} sdf={sdf_scale} dae_unit={unit} "
        f"obj_xy=({dx:.1f},{dy:.1f}) apply={scale}",
        flush=True,
    )
    return scale


def crop_or_copy_map(
    src_pgm: Path,
    src_yaml: Path,
    out_png: Path,
    out_yaml: Path,
    *,
    crop: bool,
) -> None:
    """Install occupancy as PNG + YAML. Crop 4000² gmapping canvases to occupied+pad."""
    import numpy as np
    from PIL import Image

    def _read_pgm(path: Path) -> tuple[int, int, "np.ndarray"]:
        with path.open("rb") as f:
            magic = f.readline()
            if not magic.startswith(b"P5"):
                raise ValueError(f"not P5 PGM: {path}")
            line = f.readline()
            while line.startswith(b"#"):
                line = f.readline()
            w, h = (int(x) for x in line.split())
            f.readline()  # maxval
            data = np.frombuffer(f.read(), dtype=np.uint8)
        return w, h, data[: w * h].reshape((h, w))

    origin_x, origin_y = -100.0, -100.0
    res = 0.05
    if src_yaml.is_file():
        text = src_yaml.read_text(errors="ignore")
        om = re.search(r"origin:\s*\[([^]]+)\]", text)
        if om:
            nums = [float(x.strip()) for x in om.group(1).split(",")]
            origin_x, origin_y = nums[0], nums[1]
        rm = re.search(r"resolution:\s*([-\d.eE]+)", text)
        if rm:
            res = float(rm.group(1))

    w, h, img = _read_pgm(src_pgm)
    if crop:
        occupied = img < 50
        ys, xs = np.nonzero(occupied)
        if len(xs) == 0:
            crop_img = img
            x0 = y0 = 0
            x1, y1 = w, h
            print("[maps] no occupied cells; using full PGM", flush=True)
        else:
            pad_px = int(2.0 / res)
            x0 = max(0, int(xs.min()) - pad_px)
            x1 = min(w, int(xs.max()) + pad_px + 1)
            y0 = max(0, int(ys.min()) - pad_px)
            y1 = min(h, int(ys.max()) + pad_px + 1)
            crop_img = img[y0:y1, x0:x1]
            origin_x = origin_x + x0 * res
            origin_y = origin_y + (h - y1) * res
        Image.fromarray(crop_img).save(out_png)
        print(
            f"[maps] cropped {w}x{h} -> {crop_img.shape[1]}x{crop_img.shape[0]} "
            f"origin=[{origin_x:.2f}, {origin_y:.2f}]",
            flush=True,
        )
    else:
        Image.fromarray(img).save(out_png)
        print(f"[maps] copied {w}x{h} PGM -> PNG origin=[{origin_x:.2f}, {origin_y:.2f}]", flush=True)

    out_yaml.write_text(
        f"image: {out_png.name}\n"
        f"resolution: {res:.6f}\n"
        f"origin: [{origin_x:.6f}, {origin_y:.6f}, 0.000000]\n"
        f"negate: 0\n"
        f"occupied_thresh: 0.65\n"
        f"free_thresh: 0.196\n"
    )


def should_skip(model: str, extra_skip: set[str]) -> bool:
    if model in extra_skip:
        return True
    if HUMANISH.search(model):
        return True
    if SKIP_NAME_RE.search(model):
        return True
    return False


def prepare_world(world: str, cucr_root: Path, out_dir: Path) -> int:
    spec = WORLD_SPECS[world]
    root = cucr_root / spec["pkg"]
    out_dir.mkdir(parents=True, exist_ok=True)

    if spec["kind"] == "single_mesh":
        mesh = root / spec["mesh"]
        if not mesh.is_file():
            print(f"missing mesh: {mesh}", flush=True)
            return 2
        dest_dir = out_dir / world
        extra_tex = [root / p for p in spec.get("texture_dirs", [])]
        obj, unit = stage_mesh_file(mesh, dest_dir, world)
        copy_sidecar_textures(extra_tex, dest_dir)
        if obj is None:
            return 1
        strip_yup_roof_keep_sky(obj)
        scale = compose_scale([1.0, 1.0, 1.0], unit, obj)
        z = float(spec.get("z_offset") or 0.0)
        instances = [
            {
                "model": world,
                "prim_suffix": "0",
                "translate": [0.0, 0.0, z],
                "rpy_rad": [0.0, 0.0, 0.0],
                "scale": scale,
                "obj": str(obj),
                "kind": "mesh",
            }
        ]
        staged = {world: str(obj)}
        failed: list[str] = []
        skipped: list[str] = []
        source_world = str(root / spec["world_file"])
    else:
        world_path = root / spec["world_file"]
        models_root = root / "models"
        if not world_path.is_file():
            print(f"missing world: {world_path}", flush=True)
            return 2
        includes = parse_world_models(world_path.read_text())
        extra_skip = set(spec.get("skip") or ())
        by_model: dict[str, list[dict]] = defaultdict(list)
        skipped = []
        for inc in includes:
            m = inc["model"]
            if should_skip(m, extra_skip):
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
            obj, unit = stage_model(model, model_dir, out_dir)
            if obj is None:
                failed.append(model)
            else:
                staged[model] = str(obj)
                units[model] = unit

        instances = []
        for model, incs in by_model.items():
            if model not in staged:
                continue
            sdf_scale = mesh_scale_from_sdf(models_root / model)
            unit = units.get(model, 1.0)
            scale = compose_scale(sdf_scale, unit, Path(staged[model]))
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
        source_world = str(world_path)

    map_pgm = root / spec["map_pgm"]
    map_yaml = root / spec["map_yaml"]
    if map_pgm.is_file():
        try:
            crop_or_copy_map(
                map_pgm,
                map_yaml,
                out_dir / f"{world}.png",
                out_dir / f"{world}.yaml",
                crop=bool(spec.get("crop_map")),
            )
        except Exception as exc:
            print(f"[maps] install failed ({exc})", flush=True)

    xs = [i["translate"][0] for i in instances]
    ys = [i["translate"][1] for i in instances]
    manifest = {
        "world": world,
        "source_world": source_world,
        "models_staged": sorted(staged.keys()),
        "models_failed": failed,
        "models_skipped": sorted(set(skipped)),
        "instance_count": len(instances),
        "instances": instances,
        "pose_xy_span": [min(xs), min(ys), max(xs), max(ys)] if xs else [],
    }
    man_path = out_dir / "instances.json"
    man_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[done] {world} staged={len(staged)} failed={len(failed)} "
        f"instances={len(instances)} skip={sorted(set(skipped))} -> {man_path}",
        flush=True,
    )
    return 0 if staged else 1
