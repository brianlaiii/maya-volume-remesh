"""mayapy benchmark: full volume rebuild versus cached polish/offset edits.

Synthetic fixtures stay in memory. Reports go to stdout; no scene is saved.
Set MAYA_VOLUME_REMESH_PLUGIN_DIR to an external temporary deployment directory.
"""

# ruff: noqa: E402
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--resolution", type=int, default=128)
parser.add_argument("--subdivisions", type=int, default=128)
parser.add_argument("--repeats", type=int, default=3)
parser.add_argument("--backend", choices=("auto", "cpp", "python"), default="auto")
options = parser.parse_args()
if options.repeats < 1 or not 8 <= options.subdivisions <= 512:
    parser.error("Use at least one repeat and subdivisions from 8 to 512")

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from MESH_DynaMesh_Core.scene import create_node_from_selection
from MESH_DynaMesh_Core.runtime import ensure_loaded

ensure_loaded(options.backend)
cmds.file(new=True, force=True)
source = cmds.polySphere(
    subdivisionsX=options.subdivisions, subdivisionsY=options.subdivisions // 2, constructionHistory=False
)[0]
second = cmds.polySphere(
    subdivisionsX=options.subdivisions, subdivisionsY=options.subdivisions // 2, constructionHistory=False
)[0]
cmds.setAttr(second + ".translateX", 0.8)
cmds.select([source, second])
started = time.perf_counter()
node, mesh = create_node_from_selection(resolution=options.resolution, backend=options.backend)
creation = (time.perf_counter() - started) * 1000
samples = {"full": [], "polish": [], "offset": []}
for trial in range(options.repeats):
    for label, plug, value in (
        ("full", source + ".translateY", 0.01 * (trial + 1)),
        ("polish", node + ".polish", 2 + trial % 2),
        ("offset", node + ".offset", 0.01 * (trial + 1)),
    ):
        before = cmds.getAttr(node + ".volumeBuilds")
        started = time.perf_counter()
        cmds.setAttr(plug, value)
        status = cmds.getAttr(node + ".status")
        elapsed = (time.perf_counter() - started) * 1000
        if status != "Ready":
            raise RuntimeError(status)
        after = cmds.getAttr(node + ".volumeBuilds")
        assert after == before + (label == "full"), "Cache did not match the edit"
        samples[label].append(elapsed)
report = {
    "maya": cmds.about(majorVersion=True),
    "backend": cmds.getAttr(node + ".backend"),
    "resolution": options.resolution,
    "sourceFaces": cmds.polyEvaluate(source, face=True) + cmds.polyEvaluate(second, face=True),
    "outputFaces": cmds.polyEvaluate(mesh, face=True),
    "creationMilliseconds": creation,
    "samplesMilliseconds": samples,
    "medianMilliseconds": {name: statistics.median(values) for name, values in samples.items()},
    "limits": "Standalone synthetic geometry. Excludes first compilation and viewport drawing.",
}
print(json.dumps(report, indent=2))
maya.standalone.uninitialize()
