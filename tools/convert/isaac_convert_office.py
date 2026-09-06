#!/usr/bin/env python3
"""
Convert CUCR cucr_worlds_office building into Isaac USD.

Source: CardiffUniversityComputationalRobotics/cucr_worlds (gz_humble)
ServiceSim / AWS small-office layout. NOT the stock HuNav office.usd bake.

Prerequisite:
  python3 tools/convert/prepare_office.py \\
    --cucr-root /tmp/cucr_office_src/cucr_worlds \\
    --out /tmp/cucr_office_src/obj

Usage:
  OMNI_KIT_ACCEPT_EULA=YES HUNAV_OFFICE_OBJ_DIR=/tmp/cucr_office_src/obj \\
    ~/isaacsim/python.sh tools/convert/isaac_convert_office.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time

os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")

# Compose-only only needs pxr. Starting Kit while the GUI is up freezes this laptop.
_COMPOSE_ONLY = (
    "--compose-only" in sys.argv
    or os.environ.get("HUNAV_OFFICE_COMPOSE_ONLY", "").strip().lower()
    in {"1", "true", "yes"}
)
simulation_app = None
if not _COMPOSE_ONLY:
    from isaacsim import SimulationApp

    simulation_app = SimulationApp(
        {
            "width": 960,
            "height": 540,
            "headless": True,
            "renderer": "RaytracedLighting",
            "sync_loads": True,
        }
    )
    from omni.kit.asset_converter import AssetConverterContext, get_instance
    from omni.kit.async_engine import run_coroutine
else:
    from isaacsim import SimulationApp

    simulation_app = SimulationApp(
        {
            "width": 64,
            "height": 64,
            "headless": True,
            "renderer": "RaytracedLighting",
            "sync_loads": True,
        }
    )

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

try:
    from pxr import PhysxSchema
except Exception:
    PhysxSchema = None

SRC_OBJ_DIR = os.environ.get("HUNAV_OFFICE_OBJ_DIR", "/tmp/cucr_office_src/obj")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_WORLDS", os.path.join(_REPO_ROOT, "worlds"))
)
ASSETS_DIR = os.path.join(OUT_DIR, "assets", "office")
MAPS_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_MAPS", os.path.join(_REPO_ROOT, "maps"))
)
CONVERT_TIMEOUT_S = 600.0
# Museum uses 5000; 2200 + UV-on-cubes made the office a cave.
DISTANT_LIGHT_INTENSITY = 5000.0
DOME_LIGHT_INTENSITY = 400.0
WALL_COLOR = Gf.Vec3f(0.82, 0.82, 0.78)
FLOOR_COLOR = Gf.Vec3f(0.62, 0.62, 0.58)
TEX_DIR = os.path.join(ASSETS_DIR, "textures")

# Assimp OBJ exports with height on +Y; Isaac stage is Z-up. Rotate at compose on
# the payload geometry xform only (bookstore pattern). Add models one at a time
# after GUI sign-off — do not bulk-apply.
_COMPOSE_ROTATE_X_MODELS = frozenset(
    {
        "office_table",
        "office_chair",
        "toilet",
        "wastebasket",
        "office_cafe_table",
        "tv_stand",
        "office_couch",
    }
)


def _make_context() -> AssetConverterContext:
    ctx = AssetConverterContext()
    ctx.ignore_materials = False
    ctx.ignore_animations = True
    ctx.ignore_camera = True
    ctx.ignore_light = True
    ctx.export_preview_surface = True
    ctx.use_meter_as_world_unit = True
    ctx.create_world_as_default_root_prim = True
    # Keep real PNG filenames (embed produced stub names like M.png#).
    ctx.embed_textures = False
    ctx.keep_all_materials = True
    ctx.merge_all_meshes = False
    # Office OBJs are already Z-up (3ds Max / Gazebo). Do not force Y→Z.
    ctx.convert_stage_up_z = False
    ctx.convert_stage_up_y = False
    return ctx


def convert_asset(src: str, dst: str) -> None:
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    mgr = get_instance()
    if mgr is None:
        raise RuntimeError("omni.kit.asset_converter get_instance() returned None")
    ctx = _make_context()

    async def _run():
        print(f"[convert] start {src} -> {dst}", flush=True)

        def progress(cur, total=None):
            if total:
                print(f"[convert] progress {cur}/{total}", flush=True)

        task = mgr.create_converter_task(src, dst, progress, ctx)
        ok = await task.wait_until_finished()
        status = task.get_status()
        err = task.get_error_message()
        print(f"[convert] done ok={ok} status={status} err={err!r}", flush=True)
        return ok, status, err

    fut = run_coroutine(_run())
    t0 = time.time()
    while not fut.done():
        simulation_app.update()
        if time.time() - t0 > CONVERT_TIMEOUT_S:
            raise TimeoutError(f"Convert timed out after {CONVERT_TIMEOUT_S}s: {src}")
    ok, status, err = fut.result()
    if not ok or not os.path.isfile(dst):
        raise RuntimeError(f"Convert failed {src} -> {dst}: status={status} err={err!r}")
    print(f"[convert] OK {dst} ({os.path.getsize(dst)} bytes)", flush=True)


def _zero_preview_emissive(usd_path: str) -> int:
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        raise RuntimeError(f"Cannot open for emissive patch: {usd_path}")
    zero = Gf.Vec3f(0.0, 0.0, 0.0)
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        sid = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        if sid != "UsdPreviewSurface":
            continue
        inp = shader.GetInput("emissiveColor")
        if inp is None:
            continue
        cur = inp.Get()
        if cur is None or (cur[0] == 0.0 and cur[1] == 0.0 and cur[2] == 0.0):
            continue
        inp.Set(zero)
        n += 1
    stage.GetRootLayer().Save()
    print(f"[materials] zeroed emissive on {n} shaders in {usd_path}", flush=True)
    return n


def _mark_static_colliders(root_prim) -> None:
    UsdPhysics.RigidBodyAPI.Apply(root_prim)
    UsdPhysics.RigidBodyAPI(root_prim).CreateRigidBodyEnabledAttr(False)
    UsdPhysics.CollisionAPI.Apply(root_prim)
    for prim in Usd.PrimRange(root_prim):
        if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Cube):
            UsdPhysics.CollisionAPI.Apply(prim)
            if prim.IsA(UsdGeom.Mesh):
                UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(
                    "meshSimplification"
                )
            if PhysxSchema is not None:
                try:
                    PhysxSchema.PhysxCollisionAPI.Apply(prim)
                except Exception:
                    pass


def _safe_prim_token(name: str) -> str:
    tok = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if tok and tok[0].isdigit():
        tok = "m_" + tok
    return tok or "mesh"


def _sample_png_rgb(path: str, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        im.thumbnail((32, 32))
        pixels = list(im.getdata())
        n = max(1, len(pixels))
        r = sum(p[0] for p in pixels) / n / 255.0
        g = sum(p[1] for p in pixels) / n / 255.0
        b = sum(p[2] for p in pixels) / n / 255.0
        return (r, g, b)
    except Exception:
        return fallback


def _index_pngs(*dirs: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                out[fn.lower()] = os.path.join(d, fn)
                out[os.path.splitext(fn)[0].lower()] = os.path.join(d, fn)
    return out


def _rewire_textures(usd_path: str, *search_dirs: str) -> int:
    """Point UsdUVTexture file inputs at real PNGs beside the USD / shared textures."""
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        return 0
    pngs = _index_pngs(*search_dirs)
    usd_dir = os.path.dirname(usd_path)
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        sid = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        if sid != "UsdUVTexture":
            continue
        inp = shader.GetInput("file")
        if inp is None:
            inp = shader.CreateInput("file", Sdf.ValueTypeNames.Asset)
        cur = inp.Get()
        cur_s = str(cur) if cur else ""
        name = os.path.basename(cur_s.replace("@", "").split("#")[0])
        stem = os.path.splitext(name)[0].lower()
        resolved = pngs.get(name.lower()) or pngs.get(stem)
        if resolved is None:
            pname = prim.GetName().lower()
            for key, path in pngs.items():
                if key and key in pname:
                    resolved = path
                    break
        if resolved is None:
            continue
        # Absolute paths: relative `textures/foo.png` is resolved against the
        # composing stage (worlds/office.usd), not this asset USD — RTX then
        # drops the map and every desk/chair/cubicle renders clay-white.
        inp.Set(Sdf.AssetPath(os.path.abspath(resolved)))
        n += 1
    stage.GetRootLayer().Save()
    print(f"[materials] rewired {n} textures in {usd_path}", flush=True)
    return n


def _png_has_alpha(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return False
            f.read(4)
            if f.read(4) != b"IHDR":
                return False
            ihdr = f.read(13)
            # color type: 4=gray+alpha, 6=rgba
            return ihdr[9] in (4, 6)
    except Exception:
        return False


def _patch_office_asset_usd(usd_path: str) -> None:
    """Make referenced furniture/room USDs render in the composed office stage."""
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        return
    usd_dir = os.path.dirname(usd_path)
    pngs = _index_pngs(usd_dir, os.path.join(usd_dir, "textures"), TEX_DIR)
    n_tex = 0
    n_op = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        sid = shader.GetIdAttr().Get() if shader.GetIdAttr() else None
        if sid == "UsdUVTexture":
            inp = shader.GetInput("file")
            if inp is None:
                inp = shader.CreateInput("file", Sdf.ValueTypeNames.Asset)
            cur = inp.Get()
            cur_s = str(cur.path) if cur is not None and hasattr(cur, "path") else str(cur or "")
            name = os.path.basename(cur_s.replace("@", "").split("#")[0])
            stem = os.path.splitext(name)[0].lower()
            resolved = pngs.get(name.lower()) or pngs.get(stem)
            if resolved:
                inp.Set(Sdf.AssetPath(os.path.abspath(resolved)))
                n_tex += 1
        elif sid == "UsdPreviewSurface":
            op = shader.GetInput("opacity")
            if op is not None and op.HasConnectedSource():
                src = op.GetConnectedSource()
                keep_alpha = False
                if src:
                    tex = UsdShade.Shader(src[0].GetPrim())
                    fin = tex.GetInput("file") if tex else None
                    fval = fin.Get() if fin else None
                    fpath = str(fval.path) if fval is not None and hasattr(fval, "path") else str(fval or "")
                    fpath = fpath.replace("@", "").split("#")[0]
                    if fpath and os.path.isfile(fpath) and _png_has_alpha(fpath):
                        keep_alpha = True
                if not keep_alpha:
                    op.DisconnectSource()
                    op.Set(1.0)
                    n_op += 1
            diff = shader.GetInput("diffuseColor")
            if diff is not None and diff.HasConnectedSource():
                src = diff.GetConnectedSource()
                if src:
                    tex = UsdShade.Shader(src[0].GetPrim())
                    fin = tex.GetInput("file") if tex else None
                    fval = fin.Get() if fin else None
                    fpath = str(fval.path) if fval is not None and hasattr(fval, "path") else str(fval or "")
                    fpath = fpath.replace("@", "").split("#")[0]
                    if fpath and os.path.isfile(fpath):
                        rgb = _sample_png_rgb(fpath, (0.55, 0.5, 0.45))
                        # Fallback if the map still fails to load.
                        diff.Set(Gf.Vec3f(*rgb))
    stage.GetRootLayer().Save()
    print(
        f"[materials] patched {os.path.basename(usd_path)} tex={n_tex} opacity_cleared={n_op}",
        flush=True,
    )


def _copy_model_textures(obj_path: str, usd_path: str) -> None:
    src_dir = os.path.dirname(obj_path)
    dst_dir = os.path.dirname(usd_path)
    os.makedirs(TEX_DIR, exist_ok=True)
    for fn in os.listdir(src_dir):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(dst_dir, fn))
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(TEX_DIR, fn))


def _install_shared_textures() -> None:
    os.makedirs(TEX_DIR, exist_ok=True)
    src = os.path.join(SRC_OBJ_DIR, "textures")
    if not os.path.isdir(src):
        return
    for fn in os.listdir(src):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            shutil.copy2(os.path.join(src, fn), os.path.join(TEX_DIR, fn))


def _ensure_textured_material(stage, name: str, png_name: str, fallback: Gf.Vec3f):
    """Paint for UsdGeom.Cube walls/floor.

    Do **not** connect UsdUVTexture here. Cubes have no UVs, so RTX samples the
    texture as black and the whole office looks unlit.
    """
    mat_path = f"/World/Looks/{name}"
    existing = stage.GetPrimAtPath(mat_path)
    png = os.path.join(TEX_DIR, png_name)
    rgb = fallback
    if os.path.isfile(png):
        r, g, b = _sample_png_rgb(png, (fallback[0], fallback[1], fallback[2]))
        rgb = Gf.Vec3f(r, g, b)
    if existing and existing.IsValid():
        return UsdShade.Material(existing), rgb
    UsdGeom.Scope.Define(stage, "/World/Looks")
    mat = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, f"{mat_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.85)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(rgb)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return mat, rgb


def _bind_mat(prim, mat) -> None:
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)


def _set_display_color(prim, rgb: Gf.Vec3f) -> None:
    UsdGeom.Gprim(prim).CreateDisplayColorAttr().Set([rgb])


def _add_payload(
    stage,
    path: str,
    rel_usd: str,
    translate,
    rpy_rad,
    scale,
    *,
    rotate_y_to_z: bool = False,
) -> None:
    xf = UsdGeom.Xform.Define(stage, path)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(float(translate[0]), float(translate[1]), float(translate[2]))
    )
    r, p, y = rpy_rad
    xf.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(math.degrees(r), math.degrees(p), math.degrees(y))
    )
    xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(float(scale[0]), float(scale[1]), float(scale[2]))
    )
    child = UsdGeom.Xform.Define(stage, f"{path}/geometry")
    if rotate_y_to_z:
        child.ClearXformOpOrder()
        child.AddRotateXOp(UsdGeom.XformOp.PrecisionDouble).Set(90.0)
    # Absolute refs: Isaac often opens the install-prefix symlink of office.usd,
    # and ./assets/office then misses the uninstalled furniture USDs.
    abs_usd = rel_usd
    if not os.path.isabs(rel_usd):
        candidate = os.path.normpath(os.path.join(OUT_DIR, rel_usd))
        if os.path.isfile(candidate):
            abs_usd = candidate
    child.GetPrim().GetReferences().AddReference(abs_usd)


def _backup_stock_office(final_out: str) -> None:
    bak = final_out + ".bak_hunav_isaac_stock"
    if os.path.isfile(final_out) and not os.path.isfile(bak):
        # Stock HuNav bake is a multi-MB packed USD; CUCR compose is a thin reference.
        if os.path.getsize(final_out) > 1_000_000:
            os.rename(final_out, bak)
            print(f"[compose] moved stock HuNav office → {bak}", flush=True)
    maps_png = os.path.join(MAPS_DIR, "office.png")
    maps_yaml = os.path.join(MAPS_DIR, "office.yaml")
    if os.path.isfile(maps_png) and not os.path.isfile(
        os.path.join(MAPS_DIR, "office_isaac_stock.png")
    ):
        shutil.copy2(maps_png, os.path.join(MAPS_DIR, "office_isaac_stock.png"))
        shutil.copy2(maps_yaml, os.path.join(MAPS_DIR, "office_isaac_stock.yaml"))
        print("[compose] copied stock office map → office_isaac_stock.*", flush=True)


def compose_office_stage(manifest: dict, out_usd: str) -> None:
    if os.path.exists(out_usd):
        os.remove(out_usd)
    stage = Usd.Stage.CreateNew(out_usd)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    light = stage.DefinePrim("/World/DistantLight", "DistantLight")
    light.CreateAttribute("inputs:intensity", Sdf.ValueTypeNames.Float).Set(
        DISTANT_LIGHT_INTENSITY
    )
    dome = stage.DefinePrim("/World/DomeLight", "DomeLight")
    dome.CreateAttribute("inputs:intensity", Sdf.ValueTypeNames.Float).Set(
        DOME_LIGHT_INTENSITY
    )
    dome.CreateAttribute("inputs:color", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(1.0, 1.0, 1.0)
    )

    mat_map = manifest.get("sdf_mat_to_png") or {
        "ServiceSim/PlainWall": "plain.png",
        "ServiceSim/Elevator": "elevator.png",
        "ServiceSim/Door": "door_wall.png",
        "ServiceSim/Ceiling": "ceiling.png",
        "ServiceSim/Hallway": "hallway.png",
    }
    fallback = {
        "plain.png": WALL_COLOR,
        "hallway.png": FLOOR_COLOR,
        "ceiling.png": Gf.Vec3f(0.85, 0.85, 0.82),
        "elevator.png": Gf.Vec3f(0.55, 0.55, 0.58),
        "door_wall.png": Gf.Vec3f(0.45, 0.38, 0.32),
    }
    mats = {}
    mat_rgb = {}
    for png in set(mat_map.values()):
        token = _safe_prim_token(os.path.splitext(png)[0])
        mat, rgb = _ensure_textured_material(
            stage, token, png, fallback.get(png, WALL_COLOR)
        )
        mats[png] = mat
        mat_rgb[png] = rgb

    floor_info = manifest["floor"]
    cx, cy = floor_info["center"]
    sx, sy = floor_info["size"]
    fz = float(floor_info.get("z", -0.05))
    floor = UsdGeom.Cube.Define(stage, "/World/office_floor")
    floor.GetSizeAttr().Set(2.0)
    xf = UsdGeom.Xformable(floor)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(cx, cy, fz))
    xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(sx / 2.0, sy / 2.0, 0.02))
    floor_png = floor_info.get("texture", "hallway.png")
    if floor_png in mats:
        _bind_mat(floor.GetPrim(), mats[floor_png])
        _set_display_color(floor.GetPrim(), mat_rgb[floor_png])
    # No ceiling slab — it blocked top-down / zoomed-out views (hospital #42 same lesson).

    UsdGeom.Xform.Define(stage, "/World/office_walls")
    for i, box in enumerate(manifest.get("boxes", [])):
        pose = box["pose"]
        size = box["size"]
        if size[2] < 0.3:
            continue
        prim = UsdGeom.Cube.Define(stage, f"/World/office_walls/wall_{i}")
        prim.GetSizeAttr().Set(2.0)
        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(pose[0], pose[1], pose[2])
        )
        xf.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(
                math.degrees(pose[3]), math.degrees(pose[4]), math.degrees(pose[5])
            )
        )
        xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
        )
        png = mat_map.get(box.get("material") or "", "plain.png")
        if png in mats:
            _bind_mat(prim.GetPrim(), mats[png])
            _set_display_color(prim.GetPrim(), mat_rgb[png])

    collider_paths = ["/World/office_floor", "/World/office_walls"]
    UsdGeom.Xform.Define(stage, "/World/office_rooms")
    UsdGeom.Xform.Define(stage, "/World/office_furniture")
    for inst in manifest.get("instances", []):
        model = inst["model"]
        rel = f"./assets/office/{_safe_prim_token(model)}.usd"
        abs_usd = os.path.join(ASSETS_DIR, f"{_safe_prim_token(model)}.usd")
        if not os.path.isfile(abs_usd):
            print(f"[compose] skip missing usd for {model}", flush=True)
            continue
        kind = inst.get("kind") or "mesh"
        group = "office_furniture" if kind not in {"overlay", "mesh"} else (
            "office_rooms" if kind != "overlay" else "office_rooms"
        )
        # Overlays (carpet/tile) + building meshes under rooms; furniture too
        # uses furniture group when model looks like a prop. Use kind.
        if kind == "overlay":
            group = "office_rooms"
        elif kind == "mesh":
            # room shells vs props: cubicle/office/bathroom/door/carpet stay rooms
            group = "office_rooms"
        path = f"/World/{group}/{_safe_prim_token(model)}_{inst['prim_suffix']}"
        _add_payload(
            stage,
            path,
            rel,
            inst["translate"],
            inst["rpy_rad"],
            inst["scale"],
            rotate_y_to_z=model in _COMPOSE_ROTATE_X_MODELS,
        )
        if kind != "overlay":
            collider_paths.append(f"{path}/geometry")

    stage.GetRootLayer().Save()
    stage = Usd.Stage.Open(out_usd)
    for path in collider_paths:
        root = stage.GetPrimAtPath(path)
        if root and root.IsValid():
            _mark_static_colliders(root)
    stage.GetRootLayer().Save()
    bbox = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), ["default", "render"]
    ).ComputeWorldBound(stage.GetPrimAtPath("/World"))
    print(f"[compose] wrote {out_usd} instances={len(manifest.get('instances', []))}", flush=True)
    print(f"[compose] bbox={bbox.GetRange()}", flush=True)
    print(f"[compose] prims={len(list(stage.Traverse()))}", flush=True)


def _install_map() -> None:
    src_png = os.path.join(SRC_OBJ_DIR, "office.png")
    src_yaml = os.path.join(SRC_OBJ_DIR, "office.yaml")
    if os.path.isfile(src_png) and os.path.isfile(src_yaml):
        shutil.copy2(src_png, os.path.join(MAPS_DIR, "office.png"))
        shutil.copy2(src_yaml, os.path.join(MAPS_DIR, "office.yaml"))
        print("[maps] installed CUCR office occupancy into maps/", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--compose-only",
        action="store_true",
        help="Reuse existing asset USDs; only rewrite office.usd",
    )
    args, _unknown = ap.parse_known_args()
    man_path = os.path.join(SRC_OBJ_DIR, "instances.json")
    if not os.path.isfile(man_path):
        raise FileNotFoundError(f"{man_path}; run tools/prepare_office.py first")
    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)
    # Never instance a ceiling slab (blocks top-down).
    manifest.pop("ceiling", None)
    final_out = os.path.join(OUT_DIR, "office.usd")
    _backup_stock_office(final_out)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    _install_shared_textures()

    compose_only = (
        args.compose_only
        or os.environ.get("HUNAV_OFFICE_COMPOSE_ONLY", "").strip() in {"1", "true", "yes"}
    )
    if not compose_only:
        models = manifest.get("models_staged") or sorted(
            {i["model"] for i in manifest.get("instances", [])}
        )
        for model in models:
            obj = os.path.join(SRC_OBJ_DIR, model, f"{model}.obj")
            if not os.path.isfile(obj):
                for inst in manifest.get("instances", []):
                    if inst["model"] == model and os.path.isfile(inst.get("obj", "")):
                        obj = inst["obj"]
                        break
            if not os.path.isfile(obj):
                print(f"[skip] missing obj for {model}", flush=True)
                continue
            out = os.path.join(ASSETS_DIR, f"{_safe_prim_token(model)}.usd")
            try:
                convert_asset(obj, out)
                _copy_model_textures(obj, out)
                _zero_preview_emissive(out)
                _rewire_textures(
                    out, os.path.dirname(out), os.path.dirname(obj), TEX_DIR
                )
            except Exception as exc:
                print(f"[fail] {model}: {exc}", flush=True)
                continue
    else:
        print("[compose-only] reusing existing assets/office/*.usd (no material patch)", flush=True)

    if not compose_only:
        for fn in sorted(os.listdir(ASSETS_DIR)):
            if fn.endswith(".usd"):
                _patch_office_asset_usd(os.path.join(ASSETS_DIR, fn))

    compose_office_stage(manifest, final_out)
    _install_map()
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception as exc:
        print(f"[FATAL] {exc}", flush=True)
        raise
    finally:
        if simulation_app is not None:
            simulation_app.close()
    sys.exit(rc)
