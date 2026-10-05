# Build guide

The node uses the Maya SDK. The standalone solver library needs only a C++17 compiler.
The CUDA sidecar is optional and needs no Maya SDK. Generated outputs are ignored by Git.

## Maya node

macOS example:

```bash
bash src/MESH_DynaMesh_CPP/build_dynamesh_node.sh /Applications/Autodesk/maya2027 2027
```

Linux example:

```bash
bash src/MESH_DynaMesh_CPP/build_dynamesh_node.sh /usr/autodesk/maya2024 2024
```

Set `MAYA_LOCATION` for a different SDK path. `CXX` selects the compiler.
The output is `src/MESH_DynaMesh_CPP/plug-ins/maya<version>/btDynaMesh_0012E55A`
with the platform suffix. macOS builds both arm64 and x86_64.
The first-use loader installs the selected binary into Maya's versioned user plugin directory.

The current native builder supports macOS and Linux. Windows uses Python fallback
unless a separately built matching `.mll` is provided. Windows deployment is unverified.
Do not bundle Autodesk SDK headers or Maya libraries into the repository or source package.

## Standalone geometry solver

```bash
bash src/MESH_DynaMesh_CPP/build_dynamesh.sh
```

Set `PYTHON` to choose the build interpreter. The content-keyed library is loaded
through ctypes for Maya-free solver checks. The live C++ Maya node calls the native
solver directly and does not transfer geometry through ctypes.

## CUDA

CUDA is currently an experimental Linux x86_64 sidecar.
Automatic preparation finds nvcc from `CUDACXX`, PATH or CUDA Toolkit roots.
To build explicitly:

```bash
bash src/MESH_DynaMesh_CPP/build_dynamesh_cuda.sh /path/to/output/libbtDynaMeshCuda.so
```

Set `MAYA_VOLUME_REMESH_CUDA_LIBRARY` to that file before starting Maya.
The target is `sm_86` plus `compute_86` PTX, with static cudart and double-precision queries.
Actual compilation and execution still require NVIDIA-host validation.

GPU failures, invalid distances and memory-budget failures recover with C++.
The GPU allocation budget is 60% of free memory with a 256 MiB reserve.
Queries run in batches of at most 65,536 points.

## Runtime compatibility

Registered type: `btDynaMesh`. Type ID: `0x0012E55A`.
Keep deployed plugin filenames and the node schema stable for saved scenes.
Do not load another implementation of the same type in an active session.
The loader protects files without a matching ownership record and never replaces a live runtime.
Legacy ownership marker filenames are retained for migration from the original tool.
