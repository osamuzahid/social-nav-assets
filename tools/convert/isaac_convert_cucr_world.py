#!/usr/bin/env python3
"""
Convert a remaining CUCR world (house_museum / small_house / small_warehouse)
to Isaac USD. Same pipeline as bookstore (#58): Assimp OBJ → asset_converter
(embed_textures=False) → absolute texture paths → compose RotateX(90).

Prerequisite:
  python3 tools/convert/prepare_cucr_world.py --world house_museum \\
    --cucr-root /tmp/cucr_remaining_src/cucr_worlds \\
    --out /tmp/cucr_remaining_src/obj/house_museum

Usage:
  OMNI_KIT_ACCEPT_EULA=YES HUNAV_CUCR_OBJ_DIR=/tmp/cucr_remaining_src/obj/house_museum \\
    ~/isaacsim/python.sh tools/convert/isaac_convert_cucr_world.py --world house_museum

Do not start this while the Isaac GUI is up (16 GB host).
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

_argv_world = None
for i, a in enumerate(sys.argv):
    if a == "--world" and i + 1 < len(sys.argv):
        _argv_world = sys.argv[i + 1]
        break
WORLD = (
    os.environ.get("HUNAV_CUCR_WORLD")
    or _argv_world
    or "house_museum"
)

_COMPOSE_ONLY = (
    "--compose-only" in sys.argv
    or os.environ.get("HUNAV_CUCR_COMPOSE_ONLY", "").strip().lower()
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

SRC_OBJ_DIR = os.environ.get(
    "HUNAV_CUCR_OBJ_DIR", f"/tmp/cucr_remaining_src/obj/{WORLD}"
)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_WORLDS", os.path.join(_REPO_ROOT, "worlds"))
)
ASSETS_DIR = os.path.join(OUT_DIR, "assets", WORLD)
MAPS_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_MAPS", os.path.join(_REPO_ROOT, "maps"))
)
TEX_DIR = os.path.join(ASSETS_DIR, "textures")
CONVERT_TIMEOUT_S = 900.0
DISTANT_LIGHT_INTENSITY = 5000.0
DOME_LIGHT_INTENSITY = 400.0


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


def _index_images(*dirs: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                out[fn.lower()] = os.path.join(d, fn)
                out[os.path.splitext(fn)[0].lower()] = os.path.join(d, fn)
    return out


def _resolve_tex_file(name: str, pngs: dict[str, str]) -> str | None:
    if not name:
        return None
    name = name.replace("@", "").split("#")[0]
    base = os.path.basename(name)
    stem = os.path.splitext(base)[0].lower()
    hit = pngs.get(base.lower()) or pngs.get(stem)
    if hit:
        return hit
    stub = re.sub(r"_jpeg--sampler$", "", stem, flags=re.I)
    if stub != stem:
        for ext in (".jpeg", ".jpg", ".png"):
            hit = pngs.get((stub + ext).lower()) or pngs.get(stub)
            if hit:
                return hit
    return None


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
    pngs = _index_images(usd_dir, os.path.join(usd_dir, "textures"), TEX_DIR)
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
            resolved = _resolve_tex_file(cur_s, pngs)
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


def _house_museum_sky_mesh_fix(usd_path: str) -> None:
    """CUCR sky shell (`material.001`) must render from inside windows.

    Double-sided so Stretch cameras see the sunset, not backface black. Collision
    off so RTX lidar does not hit the ~50 m dome.
    """
    stage = Usd.Stage.Open(usd_path)
    if stage is None:
        return
    n = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        binding = UsdShade.MaterialBindingAPI(prim).GetDirectBinding()
        mat = binding.GetMaterial() if binding else None
        path = str(mat.GetPath()) if mat else ""
        looks = str(prim.GetPath())
        if "material_001" not in path.lower() and "material_001" not in looks.lower():
            # Also match bound material prim name via relationships
            rel = prim.GetRelationship("material:binding")
            targets = [str(t) for t in rel.GetTargets()] if rel else []
            blob = path + " ".join(targets)
            if "material_001" not in blob.lower() and "material.001" not in blob.lower():
                continue
        mesh = UsdGeom.Mesh(prim)
        mesh.CreateDoubleSidedAttr().Set(True)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr().Set(False)
        else:
            UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr().Set(False)
        n += 1
    if n:
        stage.GetRootLayer().Save()
        print(f"[sky] doubleSided + no-collision on {n} house_museum sky meshes", flush=True)


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
    child.ClearXformOpOrder()
    child.AddRotateXOp(UsdGeom.XformOp.PrecisionDouble).Set(90.0)
    child.GetPrim().GetReferences().AddReference(os.path.abspath(abs_usd))


def compose_stage(manifest: dict, out_usd: str) -> None:
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
    if WORLD == "house_museum":
        sky_tex = None
        for cand in (
            os.path.join(ASSETS_DIR, "material_baseColor.jpeg"),
            os.path.join(ASSETS_DIR, "textures", "material_baseColor.jpeg"),
        ):
            if os.path.isfile(cand):
                sky_tex = os.path.abspath(cand)
                break
        if sky_tex:
            dome.CreateAttribute("inputs:texture:file", Sdf.ValueTypeNames.Asset).Set(
                Sdf.AssetPath(sky_tex)
            )
            print(f"[compose] house_museum DomeLight sky {sky_tex}", flush=True)

    UsdGeom.Xform.Define(stage, f"/World/{WORLD}")
    collider_paths = []
    n = 0
    for inst in manifest.get("instances", []):
        model = inst["model"]
        abs_usd = os.path.join(ASSETS_DIR, f"{_safe_prim_token(model)}.usd")
        if not os.path.isfile(abs_usd):
            print(f"[compose] skip missing usd for {model}", flush=True)
            continue
        path = f"/World/{WORLD}/{_safe_prim_token(model)}_{inst['prim_suffix']}"
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
    src_png = os.path.join(SRC_OBJ_DIR, f"{WORLD}.png")
    src_yaml = os.path.join(SRC_OBJ_DIR, f"{WORLD}.yaml")
    if os.path.isfile(src_png) and os.path.isfile(src_yaml):
        os.makedirs(MAPS_DIR, exist_ok=True)
        shutil.copy2(src_png, os.path.join(MAPS_DIR, f"{WORLD}.png"))
        shutil.copy2(src_yaml, os.path.join(MAPS_DIR, f"{WORLD}.yaml"))
        print(f"[maps] installed CUCR {WORLD} occupancy into maps/", flush=True)


def main() -> int:
    global WORLD, SRC_OBJ_DIR, ASSETS_DIR, TEX_DIR
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--world",
        default=WORLD,
        choices=["house_museum", "small_house", "small_warehouse"],
    )
    ap.add_argument("--compose-only", action="store_true")
    args, _unknown = ap.parse_known_args()
    WORLD = args.world
    if "HUNAV_CUCR_OBJ_DIR" not in os.environ:
        SRC_OBJ_DIR = f"/tmp/cucr_remaining_src/obj/{WORLD}"
    ASSETS_DIR = os.path.join(OUT_DIR, "assets", WORLD)
    TEX_DIR = os.path.join(ASSETS_DIR, "textures")

    man_path = os.path.join(SRC_OBJ_DIR, "instances.json")
    if not os.path.isfile(man_path):
        raise FileNotFoundError(f"{man_path}; run tools/prepare_cucr_world.py first")
    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(TEX_DIR, exist_ok=True)

    compose_only = args.compose_only or _COMPOSE_ONLY
    if not compose_only:
        models = manifest.get("models_staged") or sorted(
            {i["model"] for i in manifest.get("instances", [])}
        )
        for model in models:
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
                if WORLD == "house_museum":
                    _house_museum_sky_mesh_fix(out)
            except Exception as exc:
                print(f"[fail] {model}: {exc}", flush=True)
                continue
    else:
        print(f"[compose-only] reusing existing assets/{WORLD}/*.usd", flush=True)
        for fn in sorted(os.listdir(ASSETS_DIR)):
            if fn.endswith(".usd"):
                _patch_asset_usd(os.path.join(ASSETS_DIR, fn))
                if WORLD == "house_museum":
                    _house_museum_sky_mesh_fix(os.path.join(ASSETS_DIR, fn))

    compose_stage(manifest, os.path.join(OUT_DIR, f"{WORLD}.usd"))
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
