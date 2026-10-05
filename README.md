# Maya Volume Remesh

Live voxel remeshing for Autodesk Maya. Select polygon meshes, create one node,
then adjust the result in Maya's Attribute Editor or Channel Box.

The tool unions solids into a closed quad mesh. It preserves source geometry,
follows connected inputs and keeps useful parameters on the `btDynaMesh` node.
There is no custom tool window.

The demonstration GIF is coming after recording. See [demo media](docs/media/README.md).

## Features

- Connected, live volume union from meshes, groups or component selections.
- Automatic temporary closure of usable open borders.
- Resolution, Polish, Offset, Smooth Preview and normal controls.
- C++ evaluation with an independent Python fallback.
- Optional CUDA distance batches, with recovery to CPU.
- Cached volume and surface data for repeated parameter edits.
- One-step creation undo, normal parameter undo and Delete History baking.

## Install

Download or clone this repository and keep the complete folder in one location.
Drag `install.py` into the Maya viewport. It registers a Maya module and activates
the source folder in the current session. It does not create scene geometry.

You can also run this from a terminal, then restart Maya:

```bash
python install.py
```

For a custom module directory:

```bash
python install.py --module-dir /path/to/maya/modules
```

The installer registers this source location. Rerun it after moving the checkout.
No CodeHive, Brian Tools, NumPy, Qt interface or third-party geometry package is needed.

## Quick start

Select polygon meshes or a group. Run this in Maya's Python Script Editor:

```python
import maya_volume_remesh
node, mesh = maya_volume_remesh.create()
```

The node is selected for parameter editing. Sources stay visible and unchanged.
Hide them manually when comparing the result. Delete History on the output mesh
to bake the current result into an ordinary Maya mesh.

You can pass initial settings:

```python
node, mesh = maya_volume_remesh.create(resolution=96, polish=2, offset=0.02)
```

The Python `offset` argument uses centimeters. The node's Offset attribute displays
and edits in Maya's current distance units.

## Parameters

| Control | Default | Meaning |
| --- | --- | --- |
| Enabled | On | Off passes the first connected cage through. |
| Resolution | 128 | 16–512 divisions across the longest combined world-space bound. |
| Polish | 1 | 0–10 surface-fitted smoothing passes. Reuses the cached volume. |
| Offset | 0 | Signed displacement along rebuilt vertex normals. |
| Use Smooth Preview | On | Follows each input's active Smooth Mesh Preview and creases. |
| Smooth Normals | On | Soft output edges; disable for hard edges. |

Result controls show status, counts, voxel width and closed borders.
Hidden diagnostics expose the actual backend and compute time.

## Requirements and backend status

| Path | Requirements | Status |
| --- | --- | --- |
| Python node | Maya 2024 or later | Standalone tests on macOS Maya 2024/2027 |
| C++ node | Matching Maya SDK and C++17 compiler, or a matching prebuilt plugin | macOS Maya 2024/2027 builds and tests |
| CUDA distance batches | Linux x86_64, NVIDIA GPU, CUDA Toolkit/nvcc | Experimental; hardware validation pending |
| Linux/Windows Maya deployment | Matching Maya runtime | Not yet validated |

Default routing is CUDA → C++ → Python. Build/load failures report their reason
and continue automatically. CUDA accelerates nearest-triangle queries; CPU still
owns solid topology, quad extraction, fitting and validation.

This initial repository contains source, not prebuilt Maya plugins. First native
use may compile once. If a native build cannot run, the Python node stays available.
The installed runtime can reopen saved scenes without the source checkout.
Provision the matching runtime on other workstations before opening those scenes.

Useful environment settings:

```text
MAYA_VOLUME_REMESH_BACKEND=auto      # auto, strict cpp, or python
MAYA_VOLUME_REMESH_FORCE_PY=1        # force the independent Python node
MAYA_VOLUME_REMESH_PLUGIN_DIR=...    # override runtime deployment directory
MAYA_VOLUME_REMESH_CUDA=off          # disable the optional GPU stage
MAYA_VOLUME_REMESH_CUDA_LIBRARY=...  # use a prebuilt CUDA sidecar
MAYA_LOCATION=...                   # explicit Maya SDK root
CXX=...                            # C++ compiler
CUDACXX=...                        # nvcc path
CUDA_HOME=...                       # CUDA Toolkit root
```

Save and restart Maya before changing an already loaded implementation or source
installation. Native and Python versions use the same node type and ID.

## Surface behavior and limits

This rebuilds topology. Source edge loops, UVs, materials, skin weights and
deformation history are not transferred. The result starts in the initial shading group.
Nested solids are filled. Thin parts and narrow gaps can disappear at low resolution.
Flat sheets do not gain thickness. Complex or invalid borders can fail closure.
Large offsets can cross nearby surfaces.

Limits are 32 million grid samples, 16 million projected crossings and 500,000
output vertices/quads. Active preview has a two-million-face estimate limit per input.
Invalid input passes the first usable cage through and reports its status.
Large node evaluations are synchronous Maya DG work.

## Performance

Cached Polish edits reuse the sampled field. Offset and normal edits reuse polished
points. Source geometry, transforms and resolution changes rebuild the volume.

The original synthetic 128-resolution fixture used two spheres with 16,384 source
faces and produced about 54,000 quads. Warm C++ medians were 50–54 ms for full
rebuilds, 18–23 ms for Polish edits and 7–8 ms for Offset edits on macOS Maya
2024/2027. These timings exclude first compilation and viewport drawing.
They do not establish production or GPU performance.

See [testing](docs/testing.md) to reproduce the fixture and test this standalone version.

## Build, test and contribute

- [Build guide](docs/build.md)
- [Testing](docs/testing.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE). Copyright 2026 Brian Lai.
