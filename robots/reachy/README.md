# Reachy 2023 (Pollen) + Zuuu

Apache-2.0 (Pollen Robotics). Source: [pollen-robotics/reachy_2023](https://github.com/pollen-robotics/reachy_2023)
`reachy_description` plus Zuuu `mobile_base_visual.dae`. Historical description
commit is **unknown**. Licence text: `LICENSE` in this folder (Apache-2.0).

Wheels are **fixed**. Planar motion is kinematic chassis drive, not PhysX
omniwheels. Import with `--no-fix-base` (mobile base, not table-top torso).

## Rebuild meshes and USD

Collada (`.dae`) multi-node scenes import scattered in Isaac. Bake with
**`-ptv`** so each OBJ is a single mesh. Assimp OBJ is Y-up — apply Rx(+90)
`(x,y,z)→(x,-z,y)` after bake.

```bash
./tools/convert/bake_reachy_meshes.sh
OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_import_robot_urdf.py \
  --urdf robots/reachy/reachy.urdf \
  --usd-path robots/reachy --output-name reachy.usd \
  --no-fix-base --merge-mesh
```

Baked `.obj` / `.mtl` are gitignored. `orbita_arm.dae` material is named
`orbita_arm_mat` (Isaac rejects unnamed Collada materials). Upstream
`shoulder_x` inertias are ~1e4; vendored xacro uses cylinder-scale inertias.
