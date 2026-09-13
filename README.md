# social-nav-assets

## Origin

This is not a fork of one repository. It holds third-party models and converted
worlds so they do not live inside the HuNav wrapper.

| Piece | Source | Why it is here |
|---|---|---|
| Seven indoor worlds | [cucr_worlds](https://github.com/CardiffUniversityComputationalRobotics/cucr_worlds) `gz_humble` (historical port SHA **unknown**). Some worlds nest AWS RoboMaker / ServiceSim meshes. | Gazebo sources converted to Isaac USD + occupancy / octomap. |
| Stretch SE3 | [hello-robot/stretch_urdf](https://github.com/hello-robot/stretch_urdf) (Clear BSD) | Kinematic lab robot. |
| Reachy 2023 + Zuuu | [pollen-robotics/reachy_2023](https://github.com/pollen-robotics/reachy_2023) (Apache-2.0) | Kinematic lab robot. |
| Convert / octomap / pack | This tree (MIT) | Relocatable recipes to rebuild the USD bundle. |

NVIDIA Isaac Sim is runtime-only and is not redistributed. Crowd YAML and
`robot.yaml` live in `social-nav-platform`. Record: [THIRD_PARTY_ASSETS.yaml](THIRD_PARTY_ASSETS.yaml).

CUCR indoor worlds and kinematic Stretch / Reachy models for Isaac Sim 6.0.1,
plus the conversion, composition, occupancy, and octomap tools that produce them.

This repository is the assets component of the lab handover family. It does **not**
own crowd YAML, hop launchers, `robot.yaml` descriptors, or results.

## Layout

```text
maps/                 occupancy PNG+YAML and baked ESC octomaps (.bt)
worlds/               composed USD + assets/ (prebuilt bundle; gitignored)
robots/stretch/       Hello Robot SE3 URDF/meshes (Clear BSD)
robots/reachy/        Pollen Reachy 2023 + Zuuu (Apache-2.0)
tools/convert/        prepare_*, isaac_convert_*, URDF import, Reachy bake
tools/octomap/        occupancy YAML+PNG → OcTree .bt
tools/validate/       layout checks and prebuilt packer
```

Convert scripts stay in git. Composed world USDs, `worlds/assets/`, and robot
USD payloads ship as `dist/prebuilt-assets-v0.1.0-candidate.tar.zst` (checksum
in `SHA256SUMS`). Not Git LFS.

## Requirements

- Ubuntu 24.04
- For occupancy bake: `liboctomap-dev`, `g++`, Python 3 with PyYAML and Pillow
- For world/robot USD convert: NVIDIA Isaac Sim 6.0.1 (`~/isaacsim/python.sh`), Assimp
- CUCR source checkouts: `cucr_worlds` branch `gz_humble` (historical port SHA **unknown**)

## Tests (no GPU)

```bash
python3 -m pytest tests/ -q
```

## Pack / verify prebuilt

After copying freeze-identical USD trees into `worlds/` and `robots/*/`:

```bash
python3 tools/validate/pack_prebuilt.py
sha256sum -c SHA256SUMS
```

Extract later with `tar --zstd -xf dist/prebuilt-assets-v0.1.0-candidate.tar.zst`.

## Convert (relocatable)

Scripts write to `worlds/` and `maps/` relative to this repository root (override
with `SOCIAL_NAV_WORLDS` / `SOCIAL_NAV_MAPS`). Recipes: [CONVERT.md](CONVERT.md).
Do not start a convert while the Isaac GUI is up.

Museum wall Z offset is **0.1** m (`MUSEUM_Z_OFFSET`). Occupancy `world_to_grid`
uses floor (platform planner), not `round`.

## Licence

- Convert / octomap / validate tools in this tree: **MIT** ([LICENSE](LICENSE)).
- Stretch model: **Clear BSD**, Hello Robot Inc. ([LICENSES/CLEAR_BSD_Hello_Robot.md](LICENSES/CLEAR_BSD_Hello_Robot.md)).
- Reachy 2023 + Zuuu: **Apache-2.0**, Pollen Robotics ([LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt)).
- CUCR / AWS world sources: GitHub `license: null` on `cucr_worlds` and
  `aws-robomaker-hospital-world`. Recorded in [THIRD_PARTY_ASSETS.yaml](THIRD_PARTY_ASSETS.yaml).

Isaac Sim, Kit, and People CDN assets are not in this repo.
