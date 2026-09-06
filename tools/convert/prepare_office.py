#!/usr/bin/env python3
"""Stage CUCR cucr_worlds_office 1:1 for Isaac convert.

Parses office.world (building boxes + room meshes + nested furniture includes).
Skips only Gazebo people and the ServiceSim robot (HuNav + Stretch replace those).

Usage:
  python3 tools/convert/prepare_office.py \\
    --cucr-root /tmp/cucr_office_src/cucr_worlds \\
    --out /tmp/cucr_office_src/obj
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

SKIP_INCLUDE_MODELS = {
    "actor",
    "casual_female",
    "casual_male",
    "casual_male2",
    "elegant_female",
    "elegant_female2",
    "elegant_male",
    "turtlebot",
    "servicebot",
}

# Overlays: draw them, do not collide (carpet/tile would brick HuNav).
# Ceiling skipped entirely (opaque slab hid the world from above).
VISUAL_ONLY_MESH_STEMS = {
    "carpet",
    "tile",
    "hallway",
}
SKIP_MESH_BASENAMES = {
    "ceiling.obj",
}

RES_M = 0.05
FLOOR_CENTER = (-2.97, 11.25)
FLOOR_SIZE = (49.5, 22.5)

SDF_MAT_TO_PNG = {
    "ServiceSim/PlainWall": "plain.png",
    "ServiceSim/Elevator": "elevator.png",
    "ServiceSim/Door": "door_wall.png",
    "ServiceSim/Window": "window.png",
    "ServiceSim/Ceiling": "ceiling.png",
    "ServiceSim/Hallway": "hallway.png",
}


def _pose6(text: str | None) -> list[float]:
    nums = [float(x) for x in (text or "").split()]
    while len(nums) < 6:
        nums.append(0.0)
    return nums[:6]


def _rpy_to_mat(r: float, p: float, y: float) -> np.ndarray:
    cr, sr = math.cos(r), math.sin(r)
    cp, sp = math.cos(p), math.sin(p)
    cy, sy = math.cos(y), math.sin(y)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]], dtype=np.float64)
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]], dtype=np.float64)
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]], dtype=np.float64)
    return rz @ ry @ rx


def _mat_to_rpy(R: np.ndarray) -> tuple[float, float, float]:
    pitch = math.asin(max(-1.0, min(1.0, float(-R[2, 0]))))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(float(R[2, 1]), float(R[2, 2]))
        yaw = math.atan2(float(R[1, 0]), float(R[0, 0]))
    else:
        roll = 0.0
        yaw = math.atan2(float(-R[0, 1]), float(R[1, 1]))
    return roll, pitch, yaw


def _compose(a: list[float], b: list[float]) -> list[float]:
    Ra = _rpy_to_mat(*a[3:])
    t = Ra @ np.array(b[:3], dtype=np.float64) + np.array(a[:3], dtype=np.float64)
    R = Ra @ _rpy_to_mat(*b[3:])
    r, p, y = _mat_to_rpy(R)
    return [float(t[0]), float(t[1]), float(t[2]), r, p, y]


def _elem_pose(el: ET.Element | None) -> list[float]:
    if el is None:
        return _pose6("")
    return _pose6(el.findtext("pose"))


def _mesh_scale(mesh_el: ET.Element) -> list[float]:
    scale_t = (mesh_el.findtext("scale") or "1 1 1").split()
    return [float(scale_t[i]) if i < len(scale_t) else 1.0 for i in range(3)]


def _sdf_material_name(vis: ET.Element) -> str | None:
    name = vis.findtext("material/script/name") or vis.findtext("material/name")
    return name.strip() if name else None


def _resolve_mesh(office_root: Path, uri: str) -> Path | None:
    uri = uri.strip()
    if uri.startswith("../media/"):
        p = office_root / uri.replace("../", "", 1)
        return p if p.is_file() else None
    if uri.startswith("model://"):
        rest = uri.split("model://", 1)[1]
        p = office_root / "models" / rest
        return p if p.is_file() else None
    p = Path(uri)
    return p if p.is_file() else None


def _used_mtls(obj_text: str) -> set[str]:
    return set(re.findall(r"^usemtl\s+(\S+)", obj_text, flags=re.M))


def _extract_mtl_blocks(mtl_text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in mtl_text.splitlines(True):
        m = re.match(r"^newmtl\s+(\S+)", line)
        if m:
            if cur is not None:
                blocks[cur] = "".join(buf)
            cur = m.group(1)
            buf = [line]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "".join(buf)
    return blocks


def _flatten_map_line(line: str) -> str:
    m = re.match(r"^(\s*map_\w+\s+)(.+)$", line)
    if not m:
        return line
    return m.group(1) + Path(m.group(2).strip().split()[0]).name + "\n"


def _copy_texture(name: str, search: list[Path], dest_dir: Path) -> None:
    dest = dest_dir / name
    if dest.is_file():
        return
    for d in search:
        cand = d / name
        if cand.is_file():
            shutil.copy2(cand, dest)
            return


def _stage_obj(src: Path, office_root: Path, out_dir: Path) -> str:
    """Copy OBJ + a local MTL with basename texture maps (converter-friendly)."""
    key = src.stem
    dest_dir = out_dir / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_obj = dest_dir / f"{key}.obj"
    dest_mtl = dest_dir / f"{key}.mtl"
    tex_search = [
        src.parent,
        office_root / "media" / "materials" / "textures",
        src.parent.parent / "materials" / "textures",
        office_root / "models" / src.parent.parent.name / "materials" / "textures",
    ]
    if dest_obj.is_file() and dest_mtl.is_file():
        return str(dest_obj)

    obj_text = src.read_text(errors="ignore")
    used = _used_mtls(obj_text) or set()
    mtl_src = src.with_suffix(".mtl")
    if not mtl_src.is_file():
        lib = re.search(r"^mtllib\s+(\S+)", obj_text, flags=re.M)
        if lib:
            cand = src.parent / lib.group(1)
            if cand.is_file():
                mtl_src = cand
        if not mtl_src.is_file() and (src.parent / "office.mtl").is_file():
            mtl_src = src.parent / "office.mtl"

    mtl_out = f"# staged from {src.name}\n"
    if mtl_src.is_file():
        blocks = _extract_mtl_blocks(mtl_src.read_text(errors="ignore"))
        names = used if used else set(blocks)
        for name in names:
            block = blocks.get(name)
            if not block:
                continue
            for line in block.splitlines(True):
                if re.match(r"^\s*map_\w+", line):
                    line = _flatten_map_line(line)
                    tex = Path(line.split()[-1]).name
                    _copy_texture(tex, tex_search, dest_dir)
                mtl_out += line
            if not mtl_out.endswith("\n"):
                mtl_out += "\n"
    dest_mtl.write_text(mtl_out)
    obj_text = re.sub(
        r"^mtllib\s+\S+",
        f"mtllib {dest_mtl.name}",
        obj_text,
        count=1,
        flags=re.M,
    )
    dest_obj.write_text(obj_text)
    return str(dest_obj)


def _flatten_staged_dir(dest_dir: Path, office_root: Path) -> None:
    key = dest_dir.name
    tex_search = [
        dest_dir,
        office_root / "media" / "materials" / "textures",
        office_root / "models" / key / "materials" / "textures",
        office_root / "models" / key / "meshes",
    ]
    for mtl in dest_dir.glob("*.mtl"):
        lines = []
        for line in mtl.read_text(errors="ignore").splitlines(True):
            if re.match(r"^\s*map_\w+", line):
                line = _flatten_map_line(line)
                tex = Path(line.split()[-1]).name
                _copy_texture(tex, tex_search, dest_dir)
            lines.append(line)
        mtl.write_text("".join(lines))
    # Copy leftover maps Assimp referenced but did not flatten (same stem).
    for d in tex_search[2:]:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                _copy_texture(f.name, [d], dest_dir)


def _stage_mesh(src: Path, office_root: Path, out_dir: Path) -> str | None:
    if src.suffix.lower() == ".obj":
        return _stage_obj(src, office_root, out_dir)
    if src.suffix.lower() != ".dae":
        print(f"[skip] unsupported mesh {src}", flush=True)
        return None
    key = src.stem
    dest_dir = out_dir / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_obj = dest_dir / f"{key}.obj"
    if dest_obj.is_file():
        return str(dest_obj)
    tmp_dae = dest_dir / f"_tmp_{src.name}"
    text = src.read_text(errors="ignore")
    fixed = re.sub(r"(<color[^>]*>)\s+", r"\1", text)
    tmp_dae.write_text(fixed if fixed != text else text)
    print(f"[assimp] {key}: {src.name}", flush=True)
    r = subprocess.run(
        ["assimp", "export", str(tmp_dae), str(dest_obj)],
        capture_output=True,
        text=True,
    )
    tmp_dae.unlink(missing_ok=True)
    if r.returncode != 0 or not dest_obj.is_file():
        print(
            f"[fail] assimp {key}: {(r.stderr or r.stdout)[-400:]}",
            flush=True,
        )
        return None
    obj_text = dest_obj.read_text(errors="ignore")
    dest_obj.write_text(
        re.sub(
            r"^mtllib\s+\S+",
            f"mtllib {key}.mtl",
            obj_text,
            count=1,
            flags=re.M,
        )
    )
    _flatten_staged_dir(dest_dir, office_root)
    return str(dest_obj)


def _kind_for_stem(stem: str) -> str:
    if stem in VISUAL_ONLY_MESH_STEMS:
        return "overlay"
    return "mesh"


def _append_instance(meshes: list[dict], staged: str, uri: str, pose: list[float], scale: list[float]) -> None:
    meshes.append(
        {
            "uri": uri,
            "obj": staged,
            "model": Path(staged).parent.name,
            "prim_suffix": f"{len(meshes)}",
            "translate": pose[:3],
            "rpy_rad": pose[3:],
            "scale": scale,
            "kind": _kind_for_stem(Path(staged).parent.name),
        }
    )


def _collect_visual_mesh(
    vis: ET.Element,
    pose: list[float],
    office_root: Path,
    out_dir: Path,
    meshes: list[dict],
) -> None:
    mesh_el = vis.find("geometry/mesh")
    if mesh_el is None:
        return
    uri = (mesh_el.findtext("uri") or "").strip()
    if not uri:
        return
    if Path(uri).name.lower() in SKIP_MESH_BASENAMES:
        return
    src = _resolve_mesh(office_root, uri)
    if src is None:
        print(f"[missing] {uri}", flush=True)
        return
    staged = _stage_mesh(src, office_root, out_dir)
    if not staged:
        return
    _append_instance(meshes, staged, uri, pose, _mesh_scale(mesh_el))


def _expand_include(
    uri: str,
    include_pose: list[float],
    office_root: Path,
    out_dir: Path,
    meshes: list[dict],
) -> None:
    if not uri.startswith("model://"):
        return
    model_name = uri.split("model://", 1)[1].split("/")[0]
    if model_name in SKIP_INCLUDE_MODELS:
        return
    sdf_path = office_root / "models" / model_name / "model.sdf"
    if not sdf_path.is_file():
        print(f"[missing] include sdf {model_name}", flush=True)
        return
    try:
        root = ET.parse(sdf_path).getroot()
    except ET.ParseError as exc:
        print(f"[fail] parse {sdf_path}: {exc}", flush=True)
        return
    model_el = root.find("model") if root.tag != "model" else root
    if model_el is None:
        model_el = root
    for link in model_el.findall("link"):
        link_pose = _compose(include_pose, _elem_pose(link))
        for vis in link.findall("visual"):
            vis_pose = _compose(link_pose, _elem_pose(vis))
            _collect_visual_mesh(vis, vis_pose, office_root, out_dir, meshes)


def _walk_world(
    elem: ET.Element,
    pose: list[float],
    office_root: Path,
    out_dir: Path,
    boxes: list[dict],
    meshes: list[dict],
) -> None:
    if elem.tag in {"model", "link", "visual", "collision", "include"}:
        pose = _compose(pose, _elem_pose(elem))
    if elem.tag == "include":
        uri = (elem.findtext("uri") or "").strip()
        _expand_include(uri, pose, office_root, out_dir, meshes)
        return
    if elem.tag == "visual":
        box_el = elem.find("geometry/box/size")
        if box_el is not None and (box_el.text or "").strip():
            size = [float(x) for x in box_el.text.split()]
            boxes.append(
                {
                    "pose": pose,
                    "size": size,
                    "name": elem.get("name"),
                    "material": _sdf_material_name(elem),
                    "kind": "wall_box",
                }
            )
        else:
            _collect_visual_mesh(elem, pose, office_root, out_dir, meshes)
        return
    if elem.tag == "collision":
        box_el = elem.find("geometry/box/size")
        if box_el is not None and (box_el.text or "").strip():
            size = [float(x) for x in box_el.text.split()]
            boxes.append(
                {
                    "pose": pose,
                    "size": size,
                    "name": elem.get("name"),
                    "material": None,
                    "kind": "wall_box",
                }
            )
        return
    for child in list(elem):
        _walk_world(child, pose, office_root, out_dir, boxes, meshes)


def _dedupe_boxes(boxes: list[dict]) -> list[dict]:
    uniq: list[dict] = []
    seen: dict[tuple, int] = {}
    for b in boxes:
        key = tuple(round(x, 4) for x in b["pose"] + b["size"])
        if key in seen:
            idx = seen[key]
            if b.get("material") and not uniq[idx].get("material"):
                uniq[idx]["material"] = b["material"]
            continue
        seen[key] = len(uniq)
        uniq.append(b)
    return uniq


def _raster_occupancy(boxes: list[dict], out_png: Path, out_yaml: Path) -> None:
    origin_x = FLOOR_CENTER[0] - FLOOR_SIZE[0] / 2.0
    origin_y = FLOOR_CENTER[1] - FLOOR_SIZE[1] / 2.0
    w = int(round(FLOOR_SIZE[0] / RES_M))
    h = int(round(FLOOR_SIZE[1] / RES_M))
    grid = np.full((h, w), 254, dtype=np.uint8)
    for b in boxes:
        sx, sy, sz = b["size"]
        if sz < 0.2:
            continue
        yaw = b["pose"][5]
        cx, cy = b["pose"][0], b["pose"][1]
        hx, hy = sx / 2.0, sy / 2.0
        c, s = math.cos(yaw), math.sin(yaw)
        nx = max(1, int(math.ceil(sx / RES_M)))
        ny = max(1, int(math.ceil(sy / RES_M)))
        for i in range(nx + 1):
            for j in range(ny + 1):
                lx = -hx + (sx * i / nx if nx else 0.0)
                ly = -hy + (sy * j / ny if ny else 0.0)
                wx = cx + c * lx - s * ly
                wy = cy + s * lx + c * ly
                col = int(math.floor((wx - origin_x) / RES_M))
                row_from_bottom = int(math.floor((wy - origin_y) / RES_M))
                row = h - 1 - row_from_bottom
                if 0 <= col < w and 0 <= row < h:
                    grid[row, col] = 0
    try:
        from PIL import Image

        Image.fromarray(grid, mode="L").save(out_png)
    except ImportError:
        import matplotlib.pyplot as plt

        plt.imsave(out_png, grid, cmap="gray", vmin=0, vmax=255)
    yaml_text = (
        f"image: {out_png.name}\n"
        f"resolution: {RES_M:.6f}\n"
        f"origin: [{origin_x:.6f}, {origin_y:.6f}, 0.000000]\n"
        "negate: 0\n"
        "occupied_thresh: 0.65\n"
        "free_thresh: 0.196\n"
    )
    out_yaml.write_text(yaml_text)
    occ = int((grid == 0).sum())
    print(
        f"[occupancy] {w}x{h} occupied={occ} origin=({origin_x:.2f},{origin_y:.2f}) "
        f"-> {out_png}",
        flush=True,
    )


def _stage_shared_textures(office_root: Path, out_dir: Path) -> None:
    dest = out_dir / "textures"
    dest.mkdir(parents=True, exist_ok=True)
    src = office_root / "media" / "materials" / "textures"
    if not src.is_dir():
        return
    for f in src.iterdir():
        if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            shutil.copy2(f, dest / f.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cucr-root",
        type=Path,
        default=Path("/tmp/cucr_office_src/cucr_worlds"),
    )
    ap.add_argument("--out", type=Path, default=Path("/tmp/cucr_office_src/obj"))
    args = ap.parse_args()
    office_root = args.cucr_root / "cucr_worlds_office"
    world_path = office_root / "worlds" / "office.world"
    if not world_path.is_file():
        raise FileNotFoundError(world_path)
    args.out.mkdir(parents=True, exist_ok=True)
    _stage_shared_textures(office_root, args.out)
    root = ET.parse(world_path).getroot()
    world = root.find("world")
    if world is None:
        raise RuntimeError("no <world> in office.world")
    boxes: list[dict] = []
    meshes: list[dict] = []
    _walk_world(
        world,
        _pose6(""),
        office_root,
        args.out,
        boxes,
        meshes,
    )
    boxes = _dedupe_boxes(boxes)
    occ_png = args.out / "office.png"
    occ_yaml = args.out / "office.yaml"
    _raster_occupancy(boxes, occ_png, occ_yaml)
    by_model: dict[str, int] = {}
    for m in meshes:
        by_model[m["model"]] = by_model.get(m["model"], 0) + 1
    manifest = {
        "source_world": str(world_path),
        "floor": {
            "center": list(FLOOR_CENTER),
            "size": list(FLOOR_SIZE),
            "z": -0.001,
            "material": "ServiceSim/Hallway",
            "texture": "hallway.png",
        },
        "sdf_mat_to_png": SDF_MAT_TO_PNG,
        "box_count": len(boxes),
        "boxes": boxes,
        "models_staged": sorted(by_model),
        "instance_count": len(meshes),
        "instances": meshes,
    }
    man_path = args.out / "instances.json"
    man_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[done] boxes={len(boxes)} mesh_instances={len(meshes)} "
        f"unique={len(by_model)} {sorted(by_model)} -> {man_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
