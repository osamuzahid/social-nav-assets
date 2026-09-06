# Convert CUCR worlds and lab robots

Do not run Isaac convert while the Isaac GUI is up. Scripts write to `worlds/`
and `maps/` under this repository (override with `SOCIAL_NAV_WORLDS` /
`SOCIAL_NAV_MAPS`).

`--world warehouse` is stock HuNav (not in this repo). CUCR warehouse is
`small_warehouse`. CUCR office is not the stock HuNav office bake.

Sparse-clone `cucr_worlds` on `gz_humble` for the package you need. Historical
clone SHA used at original port time is **unknown** — do not pin today’s branch
tip as that input.

## Shared compose rules

- Keep Gazebo `<mesh><scale>`. Do not drop it because the OBJ looks large.
- Skip ceiling / roof slabs, Gazebo people, and Gazebo robot models.
- Bookstore Collada `<unit meter="0.01">` is already metres in Assimp OBJ —
  compose scale **1.0**, then **RotateX(90)** (Assimp Y-up).
- Sanitize Assimp `Material #N` names to `Material_N` before convert (`#` is an
  MTL comment).
- house_museum: keep CUCR sunset sky; strip roof slabs only (y≥2.08, |ny|≥0.40).
- Museum building Z offset is **0.1** m so wall bottoms meet the floor.
- Occupancy is walls from YAML+PNG (`occupied_thresh` 0.65), not painted props.

## Remaining worlds (house_museum, small_house, small_warehouse)

```bash
git clone --filter=blob:none --sparse --depth 1 -b gz_humble \
  https://github.com/CardiffUniversityComputationalRobotics/cucr_worlds.git \
  /tmp/cucr_src/cucr_worlds
cd /tmp/cucr_src/cucr_worlds
git sparse-checkout set cucr_worlds_house_museum cucr_worlds_small_house \
  cucr_worlds_small_warehouse

cd /path/to/social-nav-assets
for w in house_museum small_house small_warehouse; do
  python3 tools/convert/prepare_cucr_world.py --world "$w" \
    --cucr-root /tmp/cucr_src/cucr_worlds \
    --out /tmp/cucr_src/obj/$w
  OMNI_KIT_ACCEPT_EULA=YES HUNAV_CUCR_OBJ_DIR=/tmp/cucr_src/obj/$w \
    ~/isaacsim/python.sh tools/convert/isaac_convert_cucr_world.py --world "$w"
done
```

`--compose-only` / `HUNAV_CUCR_COMPOSE_ONLY=1` rewrites lighting and payload refs
without converting meshes.

## Museum

Stage OBJs, then:

```bash
OMNI_KIT_ACCEPT_EULA=YES HUNAV_MUSEUM_OBJ_DIR=/tmp/cucr_museum_src/obj \
  ~/isaacsim/python.sh tools/convert/isaac_convert_museum.py
```

## Hospital

Building meshes (floor / walls / optional nurses station), then props:

```bash
python3 tools/convert/prepare_hospital_props.py \
  --cucr-root /tmp/cucr_hospital_src/cucr_worlds \
  --out /tmp/cucr_hospital_src/obj_props
HUNAV_HOSPITAL_OBJ_DIR=/tmp/cucr_hospital_src/obj \
  OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_convert_hospital.py
```

Not Isaac Environments/Hospital CDN.

## Office / bookstore

```bash
python3 tools/convert/prepare_office.py \
  --cucr-root /tmp/cucr_office_src/cucr_worlds \
  --out /tmp/cucr_office_src/obj
OMNI_KIT_ACCEPT_EULA=YES HUNAV_OFFICE_OBJ_DIR=/tmp/cucr_office_src/obj \
  ~/isaacsim/python.sh tools/convert/isaac_convert_office.py

python3 tools/convert/prepare_bookstore.py \
  --cucr-root /tmp/cucr_bookstore_src/cucr_worlds \
  --out /tmp/cucr_bookstore_src/obj
OMNI_KIT_ACCEPT_EULA=YES HUNAV_BOOKSTORE_OBJ_DIR=/tmp/cucr_bookstore_src/obj \
  ~/isaacsim/python.sh tools/convert/isaac_convert_bookstore.py
```

## Occupancy → ESC octomap

```bash
for w in museum hospital office bookstore house_museum small_house small_warehouse; do
  python3 tools/octomap/occ_yaml_to_octomap.py "maps/$w.yaml" "maps/$w.bt"
done
```

Needs `liboctomap-dev`. Helper binary: `tools/octomap/.occ_xy_to_bt`.

## Stretch USD

```bash
OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_import_robot_urdf.py \
  --urdf robots/stretch/stretch.urdf \
  --usd-path robots/stretch \
  --output-name stretch.usd
```

Empty URDF `<material name="">` tags and the RealSense `d435.dae` Collada visual
are stripped in the vendored URDF so Isaac 6 `getPrimNames()` succeeds.

## Reachy USD

Collada multi-node scenes scatter in Isaac. Bake with Assimp `-ptv`, then
Y-up → Z-up Rx(+90):

```bash
./tools/convert/bake_reachy_meshes.sh
OMNI_KIT_ACCEPT_EULA=YES ~/isaacsim/python.sh tools/convert/isaac_import_robot_urdf.py \
  --urdf robots/reachy/reachy.urdf \
  --usd-path robots/reachy --output-name reachy.usd \
  --no-fix-base --merge-mesh
```

Baked `.obj` / `.mtl` are gitignored. Import with `--no-fix-base` (Zuuu mobile,
not table-top). Upstream `shoulder_x` inertias are CAD-scale (~1e4); vendored
xacro uses cylinder-scale inertias for a parked kinematic pose.
