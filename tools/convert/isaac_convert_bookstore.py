#!/usr/bin/env python3
"""
Convert CUCR cucr_worlds_bookstore (AWS RoboMaker retail) to Isaac USD.

Prerequisite:
  python3 tools/convert/prepare_bookstore.py \\
    --cucr-root /tmp/cucr_bookstore_src/cucr_worlds \\
    --out /tmp/cucr_bookstore_src/obj

Usage:
  OMNI_KIT_ACCEPT_EULA=YES HUNAV_BOOKSTORE_OBJ_DIR=/tmp/cucr_bookstore_src/obj \\
    ~/isaacsim/python.sh tools/convert/isaac_convert_bookstore.py

Assimp OBJs are Y-up (Y = height). Converter convert_stage_up_z=True
sets USD upAxis=Z but does not rotate vertices; compose adds RotateX(90)
on each payload. Do not start this while the Isaac GUI is up (16 GB host).
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

_COMPOSE_ONLY = (
    "--compose-only" in sys.argv
    or os.environ.get("HUNAV_BOOKSTORE_COMPOSE_ONLY", "").strip().lower()
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

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

try:
    from pxr import PhysxSchema
except Exception:
    PhysxSchema = None

SRC_OBJ_DIR = os.environ.get("HUNAV_BOOKSTORE_OBJ_DIR", "/tmp/cucr_bookstore_src/obj")
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_WORLDS", os.path.join(_REPO_ROOT, "worlds"))
)
ASSETS_DIR = os.path.join(OUT_DIR, "assets", "bookstore")
MAPS_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_MAPS", os.path.join(_REPO_ROOT, "maps"))
)
TEX_DIR = os.path.join(ASSETS_DIR, "textures")
CONVERT_TIMEOUT_S = 600.0
DISTANT_LIGHT_INTENSITY = 5000.0
DOME_LIGHT_INTENSITY = 400.0
SKIP_MODELS = {"aws_robomaker_retail_RetailShopCeiling_01"}


def _make_context() -> AssetConverterContext:
    ctx = AssetConverterContext()
    ctx.ignore_materials = False
    ctx.ignore_animations = True
    ctx.ignore_camera = True
    ctx.ignore_light = True
    ctx.export_preview_surface = True
    ctx.use_meter_as_world_unit = True
    ctx.create_world_as_default_root_prim = True
    ctx.embed_textures = False
    ctx.keep_all_materials = True
    ctx.merge_all_meshes = False
    # Assimp OBJ from Z_UP Collada comes out Y-up (Y = height). Rotate to Z-up.
    ctx.convert_stage_up_z = True
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


def _safe_prim_token(name: str) -> str:
    tok = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if tok and tok[0].isdigit():
        tok = "m_" + tok
    return tok or "mesh"


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
            return ihdr[9] in (4, 6)
    except Exception:
        return False


def _zero_preview_emissive(usd_path: str) -> None:
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        return
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
    if n:
        stage.GetRootLayer().Save()
        print(f"[materials] zeroed emissive on {n} shaders in {usd_path}", flush=True)


def _copy_model_textures(obj_path: str, usd_path: str) -> None:
    src_dir = os.path.dirname(obj_path)
    dst_dir = os.path.dirname(usd_path)
    os.makedirs(TEX_DIR, exist_ok=True)
    for fn in os.listdir(src_dir):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(dst_dir, fn))
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(TEX_DIR, fn))


def _patch_asset_usd(usd_path: str) -> None:
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
                    fpath = (
                        str(fval.path)
                        if fval is not None and hasattr(fval, "path")
                        else str(fval or "")
                    )
                    fpath = fpath.replace("@", "").split("#")[0]
                    if fpath and os.path.isfile(fpath) and _png_has_alpha(fpath):
                        keep_alpha = True
                if not keep_alpha:
                    op.DisconnectSource()
                    op.Set(1.0)
                    n_op += 1
    stage.GetRootLayer().Save()
    print(
        f"[materials] patched {os.path.basename(usd_path)} tex={n_tex} opacity_cleared={n_op}",
        flush=True,
    )


def _mark_static_colliders(root_prim) -> None:
    UsdPhysics.RigidBodyAPI.Apply(root_prim)
    UsdPhysics.RigidBodyAPI(root_prim).CreateRigidBodyEnabledAttr(False)
    UsdPhysics.CollisionAPI.Apply(root_prim)
    for prim in Usd.PrimRange(root_prim):
        if prim.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(prim)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(
                "meshSimplification"
            )
            if PhysxSchema is not None:
                try:
                    PhysxSchema.PhysxCollisionAPI.Apply(prim)
                except Exception:
                    pass


def _add_payload(stage, path: str, abs_usd: str, translate, rpy_rad, scale) -> None:
    xf = UsdGeom.Xform.Define(stage, path)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(float(translate[0]), float(translate[1]), float(translate[2]))
    )
    r, p, y = rpy_rad
    xf.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(
        Gf.Vec3d(math.degrees(r), math.degrees(p), math.degrees(y))
    )
    if any(abs(float(s) - 1.0) > 1e-9 for s in scale):
        xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(float(scale[0]), float(scale[1]), float(scale[2]))
        )
    child = UsdGeom.Xform.Define(stage, f"{path}/geometry")
    # Asset converter set upAxis=Z but left Y-up vertex data (floor thin in Y).
    # RotateX(90) maps Y-up → Z-up so Gazebo XY poses sit on the floor.
    child.ClearXformOpOrder()
    child.AddRotateXOp(UsdGeom.XformOp.PrecisionDouble).Set(90.0)
    child.GetPrim().GetReferences().AddReference(os.path.abspath(abs_usd))


def compose_bookstore_stage(manifest: dict, out_usd: str) -> None:
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

    UsdGeom.Xform.Define(stage, "/World/bookstore")
    collider_paths = []
    n = 0
    for inst in manifest.get("instances", []):
        model = inst["model"]
        if model in SKIP_MODELS:
            continue
        abs_usd = os.path.join(ASSETS_DIR, f"{_safe_prim_token(model)}.usd")
        if not os.path.isfile(abs_usd):
            print(f"[compose] skip missing usd for {model}", flush=True)
            continue
        path = f"/World/bookstore/{_safe_prim_token(model)}_{inst['prim_suffix']}"
        _add_payload(
            stage,
            path,
            abs_usd,
            inst["translate"],
            inst["rpy_rad"],
            inst.get("scale") or [1, 1, 1],
        )
        collider_paths.append(f"{path}/geometry")
        n += 1

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
    print(f"[compose] wrote {out_usd} instances={n}", flush=True)
    print(f"[compose] bbox={bbox.GetRange()}", flush=True)
    print(f"[compose] prims={len(list(stage.Traverse()))}", flush=True)


def _install_map() -> None:
    src_png = os.path.join(SRC_OBJ_DIR, "bookstore.png")
    src_yaml = os.path.join(SRC_OBJ_DIR, "bookstore.yaml")
    if os.path.isfile(src_png) and os.path.isfile(src_yaml):
        os.makedirs(MAPS_DIR, exist_ok=True)
        shutil.copy2(src_png, os.path.join(MAPS_DIR, "bookstore.png"))
        shutil.copy2(src_yaml, os.path.join(MAPS_DIR, "bookstore.yaml"))
        print("[maps] installed CUCR bookstore occupancy into maps/", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compose-only", action="store_true")
    args, _unknown = ap.parse_known_args()
    man_path = os.path.join(SRC_OBJ_DIR, "instances.json")
    if not os.path.isfile(man_path):
        raise FileNotFoundError(f"{man_path}; run tools/prepare_bookstore.py first")
    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(TEX_DIR, exist_ok=True)

    compose_only = (
        args.compose_only
        or os.environ.get("HUNAV_BOOKSTORE_COMPOSE_ONLY", "").strip().lower()
        in {"1", "true", "yes"}
    )
    if not compose_only:
        models = manifest.get("models_staged") or sorted(
            {i["model"] for i in manifest.get("instances", [])}
        )
        for model in models:
            if model in SKIP_MODELS:
                continue
            obj = os.path.join(SRC_OBJ_DIR, model, f"{model}.obj")
            if not os.path.isfile(obj):
                print(f"[skip] missing obj for {model}", flush=True)
                continue
            out = os.path.join(ASSETS_DIR, f"{_safe_prim_token(model)}.usd")
            try:
                convert_asset(obj, out)
                _copy_model_textures(obj, out)
                _zero_preview_emissive(out)
                _patch_asset_usd(out)
            except Exception as exc:
                print(f"[fail] {model}: {exc}", flush=True)
                continue
    else:
        print("[compose-only] reusing existing assets/bookstore/*.usd", flush=True)
        for fn in sorted(os.listdir(ASSETS_DIR)):
            if fn.endswith(".usd"):
                _patch_asset_usd(os.path.join(ASSETS_DIR, fn))

    compose_bookstore_stage(manifest, os.path.join(OUT_DIR, "bookstore.usd"))
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
