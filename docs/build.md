# Build guide

The node uses the Maya SDK. The standalone solver library needs only a C++17 compiler.
The CUDA sidecar is optional and needs no Maya SDK. Generated outputs are ignored by Git.

## Optional OpenVDB

OpenVDB is built as a separate Maya-independent library. The main Maya plugin does
not include OpenVDB headers or link to OpenVDB/TBB. A missing sidecar or runtime can
therefore recover with the built-in C++ implementation.

Install compatible OpenVDB/TBB headers and runtime libraries. Standard prefixes are
discovered automatically. Set `MAYA_VOLUME_REMESH_OPENVDB_ROOT` to another SDK prefix.
The SDK must match the running process architecture. Keep its runtime libraries available.

```bash
PYTHONPATH=src python -m MESH_DynaMesh_Core.vdb_build
```

Generated OpenVDB binaries use an external temporary cache. Override it with
`MAYA_VOLUME_REMESH_CACHE_DIR`. `MAYA_VOLUME_REMESH_OPENVDB_LIBRARY` accepts a matching
prebuilt sidecar; Windows currently requires that path. These builders do not download
or vendor OpenVDB, TBB, or Autodesk libraries.

The Maya loader installs an immutable sidecar and a relative `btDynaMeshVdb.path`
manifest beside the existing native plugin. Its ownership marker protects edited
manifests. Active runtimes are never replaced. Save and restart Maya after runtime
changes. Saved native scenes can reopen without this source checkout; a missing
OpenVDB dependency uses built-in C++ at the requested Resolution when supported.

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
It is only used by the built-in C++ solver, after OpenVDB is disabled or unavailable.
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
