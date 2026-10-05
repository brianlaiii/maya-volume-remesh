# Testing

Use synthetic geometry and external temporary folders. Never commit generated scenes,
model copies, runtime binaries or large evidence files.

## Host checks

From the repository root:

```bash
python -m unittest discover -s tests -p 'test_dynamesh_solver.py' -v
python -m unittest discover -s tests -p 'test_standalone_package.py' -v
python -m unittest discover -s tests -p 'test_dual_backend_loader.py' -v
```

The solver checks need a working C++ compiler for native parity.
The scalar implementation has no numeric or geometry package dependency.
GitHub Actions runs these checks without Maya or CUDA.

## Maya node

Run each implementation in a fresh mayapy process:

```bash
BT_DM_NODE_BACKEND=cpp mayapy tests/test_dynamesh_node_maya.py
BT_DM_NODE_BACKEND=python mayapy tests/test_dynamesh_node_maya.py
```

Use an external `MAYA_APP_DIR`, `MAYA_SKIP_USERSETUP_PY=1`, `PYTHONNOUSERSITE=1`
and `QT_QPA_PLATFORM=offscreen` for isolated headless tests.
On Apple Silicon, use `arch -arm64` for Maya 2027.
Restricted sandboxes can prevent Qt's CPU-feature detection.

The suite covers schema, units, transformed unions, source retention, preview and creases,
live changes, caching, sparse inputs, instances, rollback, undo/redo, baking,
automatic Python recovery and scene reopening without repository imports.
The Python run skips the separate fresh-native-failure fixture.
Both runs skip the real GPU fixture unless explicitly enabled.

For the earlier static-output compatibility API:

```bash
MAYA_VOLUME_REMESH_BACKEND=cpp mayapy tests/test_dynamesh_maya.py
```

Compare authored/reopened Maya points with a 1e-6 tolerance.
The Maya-free solver tests use tighter double-precision comparisons.

## Module installation

Install into a temporary module directory, then start a fresh mayapy with that
directory in `MAYA_MODULE_PATH`. Confirm `import maya_volume_remesh` works without
manually modifying `sys.path`, and create a node from generated primitives.

## Benchmark

```bash
mayapy tests/benchmark_dynamesh_node_maya.py --backend cpp --resolution 128
```

The fixture unions two spheres and prints full, Polish and Offset evaluation times.
It verifies cache-build counts. It does not save a scene or measure viewport drawing.

## NVIDIA validation still needed

On Linux with a suitable NVIDIA GPU and CUDA Toolkit:

```bash
BT_DM_TEST_CUDA=1 BT_DM_NODE_BACKEND=cpp mayapy tests/test_dynamesh_node_maya.py
```

The GPU fixture requires `CUDA + C++` and compares output to the CPU solver.
Also test GPU memory pressure, runtime failure and whole-command timing.
No CUDA hardware pass is claimed by the current repository.
