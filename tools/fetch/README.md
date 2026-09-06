# Fetch CUCR world sources

Historical `cucr_worlds` commit used at port time is **unknown**. Clone branch
`gz_humble` only as a regenerate input, not as a freeze pin.

```bash
git clone --filter=blob:none --sparse --depth 1 -b gz_humble \
  https://github.com/CardiffUniversityComputationalRobotics/cucr_worlds.git \
  /tmp/cucr_src/cucr_worlds
cd /tmp/cucr_src/cucr_worlds
git sparse-checkout set \
  cucr_worlds_museum cucr_worlds_hospital cucr_worlds_office \
  cucr_worlds_bookstore cucr_worlds_house_museum \
  cucr_worlds_small_house cucr_worlds_small_warehouse
```

Then follow [CONVERT.md](../../CONVERT.md). Do not vendor Isaac Sim.
