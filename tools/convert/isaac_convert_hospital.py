#!/usr/bin/env python3
"""
Convert CUCR cucr_worlds_hospital building meshes with Isaac asset_converter.

Source: CardiffUniversityComputationalRobotics/cucr_worlds (gz_humble) —
aws_robomaker_hospital_floor_01_{floor,walls} (+ optional nurses station).
Replica of AWS RoboMaker hospital; NOT the stock Isaac Environments/Hospital.

Prerequisite OBJ exports (Assimp Collada front-end; color-tag whitespace may
need stripping — see CONVERT.md):
  floor.obj / walls.obj [/ nursesstation.obj] + textures beside them

Usage:
  HUNAV_HOSPITAL_OBJ_DIR=/tmp/cucr_hospital_src/obj \\
    OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_convert_hospital.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time

os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")

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
from pxr import Gf, PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

SRC_OBJ_DIR = os.environ.get("HUNAV_HOSPITAL_OBJ_DIR", "/tmp/cucr_hospital_src/obj")
PROPS_OBJ_DIR = os.environ.get(
    "HUNAV_HOSPITAL_PROPS_OBJ_DIR", "/tmp/cucr_hospital_src/obj_props"
)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.abspath(
    os.environ.get("SOCIAL_NAV_WORLDS", os.path.join(_REPO_ROOT, "worlds"))
)
ASSETS_DIR = os.path.join(OUT_DIR, "assets", "hospital")
PROPS_USD_DIR = os.path.join(ASSETS_DIR, "props")
CONVERT_TIMEOUT_S = 300.0
# ORIGINALLY: 5000 (museum copy) — white AWS hospital + Assimp Ke→emissive washed out.
# PATCH: lower intensity; also zero emissive on PreviewSurfaces after convert.
DISTANT_LIGHT_INTENSITY = 1800.0

# Gazebo poses from cucr_worlds_hospital/worlds/hospital.world (Z-up).
POSE_FLOOR = Gf.Vec3d(-0.001425, -0.014447, 0.0)
POSE_WALLS = Gf.Vec3d(-0.013823, -0.013783, 0.0)
POSE_NURSES = Gf.Vec3d(0.0, 1.5, 0.0)


def _make_context() -> AssetConverterContext:
    ctx = AssetConverterContext()
    ctx.ignore_materials = False
    ctx.ignore_animations = True
    ctx.ignore_camera = True
    ctx.ignore_light = True
    ctx.export_preview_surface = True
    ctx.use_meter_as_world_unit = True
    ctx.create_world_as_default_root_prim = True
    ctx.embed_textures = True
    ctx.keep_all_materials = True
    ctx.merge_all_meshes = False
    # Assimp hospital OBJs already sit in XY with Z up (map frame).
    # Do NOT force convert_stage_up_z the way museum does — that would tip the floor.
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
    """Assimp MTL Ke (often 0.2–1.0) becomes UsdPreviewSurface emissiveColor → glow."""
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
        if prim.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(prim)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(
                "meshSimplification"
            )
            PhysxSchema.PhysxCollisionAPI.Apply(prim)


def _add_payload(
    stage,
    path: str,
    rel_usd: str,
    translate: Gf.Vec3d,
    rpy_rad: tuple[float, float, float] | None = None,
    scale: tuple[float, float, float] | None = None,
) -> None:
    """Hospital meshes are Z-up (floor in XY). Optional Gazebo RPY + mesh scale."""
    xf = UsdGeom.Xform.Define(stage, path)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(translate)
    if rpy_rad is not None:
        r, p, y = rpy_rad
        xf.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(math.degrees(r), math.degrees(p), math.degrees(y))
        )
    if scale is not None and any(abs(s - 1.0) > 1e-9 for s in scale):
        xf.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(
            Gf.Vec3d(scale[0], scale[1], scale[2])
        )
    child = UsdGeom.Xform.Define(stage, f"{path}/geometry")
    child.GetPrim().GetReferences().AddReference(rel_usd)


def _safe_prim_token(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def compose_hospital_stage(
    floor_usd: str,
    walls_usd: str,
    out_usd: str,
    nurses_usd: str | None = None,
    prop_instances: list[dict] | None = None,
) -> None:
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

    _add_payload(stage, "/World/hospital_floor", "./assets/hospital/hospital_floor.usd", POSE_FLOOR)
    _add_payload(stage, "/World/hospital_walls", "./assets/hospital/hospital_walls.usd", POSE_WALLS)
    if nurses_usd and os.path.isfile(nurses_usd):
        _add_payload(
            stage,
            "/World/hospital_nursesstation",
            "./assets/hospital/hospital_nursesstation.usd",
            POSE_NURSES,
        )

    collider_paths = [
        "/World/hospital_floor/geometry",
        "/World/hospital_walls/geometry",
        "/World/hospital_nursesstation/geometry",
    ]

    if prop_instances:
        props_xf = UsdGeom.Xform.Define(stage, "/World/hospital_props")
        for inst in prop_instances:
            model = inst["model"]
            suffix = inst.get("prim_suffix", "0")
            rel = f"./assets/hospital/props/{_safe_prim_token(model)}.usd"
            abs_usd = os.path.join(PROPS_USD_DIR, f"{_safe_prim_token(model)}.usd")
            if not os.path.isfile(abs_usd):
                print(f"[compose] skip missing usd for {model}", flush=True)
                continue
            tx, ty, tz = inst["translate"]
            r, p, y = inst["rpy_rad"]
            sc = inst.get("scale") or [1.0, 1.0, 1.0]
            prim_path = f"/World/hospital_props/{_safe_prim_token(model)}_{suffix}"
            _add_payload(
                stage,
                prim_path,
                rel,
                Gf.Vec3d(tx, ty, tz),
                rpy_rad=(r, p, y),
                scale=(float(sc[0]), float(sc[1]), float(sc[2])),
            )
            collider_paths.append(f"{prim_path}/geometry")
        _ = props_xf  # keep define for hierarchy clarity

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
    n_props = len(prop_instances) if prop_instances else 0
    print(f"[compose] wrote {out_usd} props_instances={n_props}", flush=True)
    print(f"[compose] bbox={bbox.GetRange()}", flush=True)
    print(f"[compose] prims={len(list(stage.Traverse()))}", flush=True)


def _convert_props(manifest_path: str) -> list[dict]:
    with open(manifest_path, "r", encoding="utf-8") as f:
        man = json.load(f)
    os.makedirs(PROPS_USD_DIR, exist_ok=True)
    models = man.get("models_staged") or sorted(
        {i["model"] for i in man.get("instances", [])}
    )
    for model in models:
        obj = os.path.join(PROPS_OBJ_DIR, model, f"{model}.obj")
        if not os.path.isfile(obj):
            # fall back to path recorded in first instance
            for inst in man.get("instances", []):
                if inst["model"] == model and os.path.isfile(inst.get("obj", "")):
                    obj = inst["obj"]
                    break
        if not os.path.isfile(obj):
            print(f"[props] missing obj for {model}", flush=True)
            continue
        out = os.path.join(PROPS_USD_DIR, f"{_safe_prim_token(model)}.usd")
        if os.path.isfile(out) and os.path.getsize(out) > 1000:
            print(f"[props] reuse {out}", flush=True)
        else:
            convert_asset(obj, out)
            _zero_preview_emissive(out)
    return man.get("instances", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--building",
        action="store_true",
        help="(Re)convert floor/walls/nurses from HUNAV_HOSPITAL_OBJ_DIR",
    )
    ap.add_argument(
        "--props",
        action="store_true",
        help="Convert staged props + compose into hospital.usd",
    )
    ap.add_argument(
        "--props-manifest",
        default=os.path.join(PROPS_OBJ_DIR, "instances.json"),
        help="instances.json from prepare_hospital_props.py",
    )
    args = ap.parse_args()
    # Default: building-only (legacy). Prefer explicit --props for ship compose.
    if not args.building and not args.props:
        args.building = True

    os.makedirs(ASSETS_DIR, exist_ok=True)
    floor_out = os.path.join(ASSETS_DIR, "hospital_floor.usd")
    walls_out = os.path.join(ASSETS_DIR, "hospital_walls.usd")
    nurses_out = os.path.join(ASSETS_DIR, "hospital_nursesstation.usd")
    final_out = os.path.join(OUT_DIR, "hospital.usd")

    if os.path.isfile(final_out) and os.path.getsize(final_out) > 1_000_000:
        # Only auto-backup huge stock Isaac bake; prop-rich compose can also be large.
        if not os.path.isdir(os.path.join(ASSETS_DIR, "props")):
            bak = final_out + ".bak_hunav_isaac_stock"
            if not os.path.isfile(bak):
                os.rename(final_out, bak)
                print(f"[compose] moved stock Isaac hospital → {bak}", flush=True)

    nurses = nurses_out if os.path.isfile(nurses_out) else None
    if args.building:
        floor_src = os.path.join(SRC_OBJ_DIR, "floor.obj")
        walls_src = os.path.join(SRC_OBJ_DIR, "walls.obj")
        nurses_src = os.path.join(SRC_OBJ_DIR, "nursesstation.obj")
        convert_asset(floor_src, floor_out)
        convert_asset(walls_src, walls_out)
        if os.path.isfile(nurses_src):
            convert_asset(nurses_src, nurses_out)
            nurses = nurses_out
        for path in (floor_out, walls_out, nurses_out if nurses else None):
            if path:
                _zero_preview_emissive(path)
    else:
        if not (os.path.isfile(floor_out) and os.path.isfile(walls_out)):
            raise FileNotFoundError(
                "Building USDs missing; run with --building once or provide assets"
            )
        if os.path.isfile(nurses_out):
            nurses = nurses_out

    prop_instances = None
    if args.props:
        if not os.path.isfile(args.props_manifest):
            raise FileNotFoundError(
                f"Missing {args.props_manifest}; run tools/prepare_hospital_props.py first"
            )
        prop_instances = _convert_props(args.props_manifest)

    compose_hospital_stage(
        floor_out, walls_out, final_out, nurses, prop_instances=prop_instances
    )
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception as exc:
        print(f"[FATAL] {exc}", flush=True)
        raise
    finally:
        simulation_app.close()
    sys.exit(rc)
