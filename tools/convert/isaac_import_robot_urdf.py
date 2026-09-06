#!/usr/bin/env python3
"""
Convert a robot URDF to USD using Isaac Sim's URDF importer (headless).

Usage:
  OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/isaac_import_robot_urdf.py \
    --urdf robots/stretch/stretch.urdf \
    --usd-path robots/stretch \
    --output-name stretch.usd

PATCH (isaac-social-nav): vendored Stretch assets from hello-robot-stretch-urdf
(SE3 / eoa_wrist_dw3_tool_sg3). Source URDF/meshes live under robots/stretch/.
"""

from __future__ import annotations

import argparse
import os
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

import omni.kit.app


def _enable_extensions() -> None:
    ext_manager = omni.kit.app.get_app().get_extension_manager()
    for ext in ("omni.scene.optimizer.core", "isaacsim.robot.schema"):
        ext_manager.set_extension_enabled_immediate(ext, True)


_enable_extensions()

from isaacsim.asset.importer.urdf.impl import URDFImporter, URDFImporterConfig

CONVERT_TIMEOUT_S = 300.0


def parse_args() -> argparse.Namespace:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    default_urdf = os.path.join(repo_root, "robots", "stretch", "stretch.urdf")
    default_out = os.path.join(repo_root, "robots", "stretch")

    parser = argparse.ArgumentParser(description="Import URDF to USD via Isaac Sim.")
    parser.add_argument("--urdf", default=default_urdf, help="Path to URDF file.")
    parser.add_argument(
        "--usd-path",
        default=default_out,
        help="Output directory for converted USD assets.",
    )
    parser.add_argument(
        "--output-name",
        default="stretch.usd",
        help="Basename of the main USD file to copy/rename after import.",
    )
    parser.add_argument(
        "--merge-mesh",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Merge meshes during import.",
    )
    parser.add_argument(
        "--fix-base",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fix base to world (False = floating base for chassis drive wrapper).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    urdf_path = os.path.abspath(args.urdf)
    usd_dir = os.path.abspath(args.usd_path)

    if not os.path.isfile(urdf_path):
        raise FileNotFoundError(f"URDF not found: {urdf_path}")

    os.makedirs(usd_dir, exist_ok=True)

    import_config = URDFImporterConfig(
        urdf_path=urdf_path,
        usd_path=usd_dir,
        merge_mesh=args.merge_mesh,
        fix_base=args.fix_base,
        joint_target_type="none",
        override_joint_stiffness=0.0,
        override_joint_damping=1000.0,
        allow_self_collision=False,
        collision_from_visuals=False,
    )

    print(f"[import] URDF: {urdf_path}", flush=True)
    print(f"[import] USD dir: {usd_dir}", flush=True)

    importer = URDFImporter(import_config)
    t0 = time.time()
    output_usd = importer.import_urdf()
    while simulation_app.is_running() and time.time() - t0 < CONVERT_TIMEOUT_S:
        simulation_app.update()
        if output_usd and os.path.isfile(output_usd):
            break

    if not output_usd or not os.path.isfile(output_usd):
        raise RuntimeError("URDF import failed: no output USD produced")

    # Isaac writes {usd_dir}/{robot_name}/ with payloads/ beside the root USD.
    # Flatten to {usd_dir}/{output_name} + {usd_dir}/payloads/ for stable share paths.
    final_path = os.path.join(usd_dir, args.output_name)
    imported_root = os.path.dirname(output_usd)
    imported_payloads = os.path.join(imported_root, "payloads")
    flat_payloads = os.path.join(usd_dir, "payloads")

    if os.path.isdir(imported_payloads):
        if os.path.exists(flat_payloads):
            import shutil

            shutil.rmtree(flat_payloads)
        os.replace(imported_payloads, flat_payloads)

    if os.path.abspath(output_usd) != os.path.abspath(final_path):
        if os.path.exists(final_path):
            os.remove(final_path)
        os.replace(output_usd, final_path)
        output_usd = final_path

    # Remove empty importer wrapper dir if present.
    if os.path.isdir(imported_root) and not os.listdir(imported_root):
        os.rmdir(imported_root)

    print(f"[import] OK {output_usd} ({os.path.getsize(output_usd)} bytes)", flush=True)
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
