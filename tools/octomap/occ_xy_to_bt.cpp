// Occupied XY (stdin) → octomap binary .bt. z extruded for Stretch-height GridMap.
#include <cstdio>
#include <cstdlib>
#include <octomap/OcTree.h>

int main(int argc, char **argv)
{
  if (argc != 3)
  {
    std::fprintf(stderr, "usage: %s resolution out.bt < xy.txt\n", argv[0]);
    return 2;
  }
  const double res = std::atof(argv[1]);
  octomap::OcTree tree(res);
  const double zs[] = {0.15, 0.45, 0.75, 1.05};
  double x = 0.0, y = 0.0;
  int n = 0;
  while (std::fscanf(stdin, "%lf %lf", &x, &y) == 2)
  {
    for (double z : zs)
    {
      tree.updateNode(x, y, z, true);
    }
    n++;
  }
  if (n == 0)
  {
    std::fprintf(stderr, "no points\n");
    return 3;
  }
  tree.updateInnerOccupancy();
  if (!tree.writeBinary(argv[2]))
  {
    std::fprintf(stderr, "writeBinary failed: %s\n", argv[2]);
    return 4;
  }
  std::fprintf(stderr, "nodes=%zu xy=%d\n", tree.size(), n);
  return 0;
}
