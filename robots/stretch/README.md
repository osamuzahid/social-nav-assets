# Hello Robot Stretch (SE3)

Vendored URDF and meshes for the kinematic Stretch model (Clear BSD — see
`LICENSE.md`). Upstream: [hello-robot/stretch_urdf](https://github.com/hello-robot/stretch_urdf).
Model: **SE3** with `eoa_wrist_dw3_tool_sg3` gripper.

Campaign drive is kinematic `Physics=none`. Do not treat PhysX wheeled Stretch
as a supported path.

## Rebuild USD

```bash
OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_import_robot_urdf.py \
  --urdf robots/stretch/stretch.urdf \
  --usd-path robots/stretch \
  --output-name stretch.usd
```

Output (`stretch.usd` + `payloads/`) is part of the prebuilt bundle, not git.

The vendored URDF strips empty `<material name="">` tags and the RealSense
`d435.dae` visual. Isaac 6 `getPrimNames()` fails on unnamed Collada materials.
