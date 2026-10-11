# Testing

Use synthetic geometry and external temporary folders. Never commit generated scenes,
model copies, runtime binaries or large evidence files.

## Host checks

From the repository root:

```bash
python -m unittest discover -s tests -p 'test_dynamesh_solver.py' -v
python -m unittest discover -s tests -p 'test_standalone_package.py' -v
python -m unittest discover -s tests -p 'test_dual_backend_loader.py' -v
python -m unittest discover -s tests -p 'test_openvdb_solver.py' -v
python -m unittest discover -s tests -p 'test_openvdb_runtime.py' -v
```

The solver checks need a working C++ compiler for native parity.
The scalar implementation has no numeric or geometry package dependency.
GitHub Actions runs these checks without Maya or CUDA.

OpenVDB tests execute the real optional solver and C++ sidecar loader when an SDK
is available. `MVR_REQUIRE_OPENVDB=1` makes a missing SDK fail instead of skipping.
Routing tests still execute real built-in C++ and Python recovery without an SDK.
They verify geometry/callback errors, cancellation and memory failures do not retry,
and that fallback never clamps Resolution. Runtime tests protect edited files and
require restart when a loaded sidecar changes.

## Full standalone Linux gate

Use an existing Linux x86_64 test image with Python 3.10+, a C++17 compiler, OpenVDB
and TBB SDKs. The runner never downloads images or accesses the network during tests.

```bash
python3 tests/linux_compat/run.py test --image YOUR_TEST_IMAGE --report /tmp/mvr-linux.json
```

The runner stages Git-visible local changes into a read-only snapshot and compiles in
disposable Linux storage. It registers all native sources, requires actual OpenVDB
execution, rejects empty/skipped mandatory suites and rejects changed run inputs.
Image identity and source hashes are recorded. Linux Maya/SDK and NVIDIA execution
remain separate checks. Compiled Mac binaries cannot supply a Linux pass.

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

The existing node suite explicitly disables OpenVDB to verify the preserved C++/Python
behavior. Run the added three-stage suite in fresh processes too:

```bash
MVR_NODE_ENGINE=openvdb mayapy tests/test_openvdb_node_maya.py
MVR_NODE_ENGINE=cpp mayapy tests/test_openvdb_node_maya.py
MVR_NODE_ENGINE=python mayapy tests/test_openvdb_node_maya.py
```

The second run simulates a missing OpenVDB library. The third also simulates missing
native compilation. These checks cover actual solver geometry, cache reuse, source
retention, Undo/Redo, baking, high Resolution, unchanged fallback Resolution, and
saved-scene reopening without source imports. An OpenVDB scene also reopens after
its sidecar becomes unavailable and reports built-in C++ recovery.

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
