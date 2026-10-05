// Maya-independent POD ABI. Keep CUDA optional in the CPU Maya plugin.
#pragma once
#include <cstdint>

struct BtDmTriangle { double points[9]; };
struct BtDmBvhNode { double lower[3], upper[3]; int left, right, begin, end; };
using BtDmProgress = int (*)(double, const char*);
using BtDmCudaDistance = int (*)(const BtDmTriangle*, int, const BtDmBvhNode*, int,
                               const int*, const double*, int, double*,
                               BtDmProgress, char*, int);

// Return 0 on success, 1 on failure, 2 on cancellation. Never return partial
// results as success. Each query is one normalized grid corner in double precision.
extern "C" int bt_dynamesh_cuda_version();
extern "C" int bt_dynamesh_cuda_distances_v1(const BtDmTriangle*, int,
    const BtDmBvhNode*, int, const int*, const double*, int, double*,
    BtDmProgress, char*, int);
