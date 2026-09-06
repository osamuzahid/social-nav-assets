#!/usr/bin/env bash
# Bake Reachy Collada multi-node meshes → single OBJs (assimp -ptv), convert
# Assimp Y-up → ROS/Isaac Z-up, retarget xacro/URDF, strip Gazebo/ros2_control.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/robots/reachy"
MESH="$DEST/meshes"

command -v assimp >/dev/null || { echo "assimp required"; exit 1; }
command -v xacro >/dev/null || { echo "source ROS 2 first (xacro)"; exit 1; }

echo "[bake] DAE → OBJ (-ptv) + Y-up→Z-up in $MESH"
cd "$MESH"
python3 - <<'PY'
import subprocess
from pathlib import Path

mesh = Path(".")

def yup_to_zup_obj(path: Path) -> None:
    """Assimp OBJ is Y-up; Isaac/URDF expect Z-up. Rx(+90): (x,y,z)->(x,-z,y)."""
    lines = path.read_text().splitlines(True)
    out = []
    for line in lines:
        if line.startswith("v ") or line.startswith("vn "):
            parts = line.split()
            x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            parts[1], parts[2], parts[3] = f"{x:.8g}", f"{-z:.8g}", f"{y:.8g}"
            out.append(" ".join(parts) + "\n")
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    path.write_text("".join(out))

for dae in sorted(mesh.glob("*.dae")):
    obj = dae.with_suffix(".obj")
    subprocess.check_call(
        ["assimp", "export", str(dae), str(obj), "-fobj", "-ptv"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    yup_to_zup_obj(obj)
    print(f"  {obj.name}")
PY

echo "[bake] retarget xacro mesh refs to .obj (fix double file://)"
find "$DEST/urdf" -name '*.xacro' -print0 | xargs -0 sed -i \
  -e 's|file://file:///|file:///|g' \
  -e 's|\.dae"|.obj"|g'

echo "[bake] expand + strip gazebo/ros2_control → reachy.urdf"
# shellcheck disable=SC1091
set +u
source /opt/ros/jazzy/setup.bash
set -u
xacro "$DEST/urdf/reachy.urdf.xacro" \
  robot_config:=full_kit \
  use_gazebo:=true \
  use_fake_hardware:=true \
  > /tmp/reachy_raw.urdf

python3 - "$DEST/reachy.urdf" <<'PY'
import re
import sys
from pathlib import Path

raw = Path("/tmp/reachy_raw.urdf").read_text()
raw = re.sub(r"<gazebo[\s\S]*?</gazebo>\s*", "", raw)
raw = re.sub(r"<ros2_control[\s\S]*?</ros2_control>\s*", "", raw)
raw = re.sub(r"<transmission[\s\S]*?</transmission>\s*", "", raw)
Path(sys.argv[1]).write_text(raw)
n_obj = raw.count(".obj")
print(f"[bake] wrote {sys.argv[1]} ({len(raw.splitlines())} lines, {n_obj} .obj refs)")
for need in (
    "left_camera_optical",
    "right_camera_optical",
    "torso",
    "base_footprint",
    "base_link",
    "lidar_link",
    "mobile_base_visual.obj",
):
    assert need in raw, need
PY

echo "[bake] done — run tools/convert/isaac_import_robot_urdf.py next (see robots/reachy/README.md)"
