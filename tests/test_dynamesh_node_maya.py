"""Run explicitly with mayapy. Both backends use the same lifecycle tests.

BT_DM_NODE_BACKEND=cpp or python chooses the implementation before registration.
All fixtures and saved scenes live outside the repository in temporary folders.
"""

# ruff: noqa: E402
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if __name__ != "__main__":
    raise unittest.SkipTest("Run explicitly with mayapy")

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds
import maya.api.OpenMaya as om

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from MESH_DynaMesh_Core import scene, schema, runtime, solver

BACKEND = os.environ.get("BT_DM_NODE_BACKEND", "cpp")
DEPLOYMENT = tempfile.mkdtemp(prefix="bt-dynamesh-node-runtime-")
os.environ[runtime.PLUGIN_DIR_ENV] = DEPLOYMENT


def mesh_fn(obj):
    selection = om.MSelectionList()
    selection.add(obj)
    path = selection.getDagPath(0)
    if path.node().hasFn(om.MFn.kTransform):
        path.extendToShape()
    return om.MFnMesh(path)


def signature(obj):
    fn = mesh_fn(obj)
    counts, connects = fn.getVertices()
    return ([tuple(p)[:3] for p in fn.getPoints(om.MSpace.kWorld)], list(counts), list(connects))


def assert_points(test, actual, expected, tolerance=1e-6):
    # Maya mesh storage rounds authored points to float precision. The SDK-free
    # solver suite checks the independent double-precision calculations separately.
    test.assertEqual(len(actual), len(expected))
    test.assertLess(max(abs(a - b) for p, q in zip(actual, expected) for a, b in zip(p, q)), tolerance)


class DynaMeshNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime.ensure_loaded(BACKEND)

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear="cm")
        cmds.undoInfo(state=True)

    def create(self, **settings):
        return scene.create_node_from_selection(resolution=24, backend=BACKEND, **settings)

    def test_schema_has_native_names_units_and_read_only_outputs(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, _mesh = self.create()
        fn = om.MFnDependencyNode(om.MSelectionList().add(node).getDependNode(0))
        self.assertEqual(fn.typeId.id(), schema.TYPE_ID)
        attr_order = [fn.attribute(i) for i in range(fn.attributeCount())]
        positions = [attr_order.index(fn.attribute(name)) for name in schema.DEFAULTS]
        self.assertEqual(positions, sorted(positions))
        for name, kind, _default, lo, hi, _tip in schema.PARAMETERS:
            self.assertTrue(cmds.getAttr(node + "." + name, keyable=True))
            self.assertEqual(
                cmds.getAttr(node + "." + name, type=True),
                "bool" if kind == "bool" else "long" if kind == "int" else "doubleLinear",
            )
            if lo is not None:
                self.assertEqual(cmds.attributeQuery(name, node=node, minimum=True), [float(lo)])
                self.assertEqual(cmds.attributeQuery(name, node=node, maximum=True), [float(hi)])
        for name in schema.OUTPUTS:
            self.assertFalse(cmds.attributeQuery(name, node=node, writable=True))
        for name in ("input", "backend", "computeMilliseconds", "volumeBuilds"):
            self.assertTrue(cmds.attributeQuery(name, node=node, hidden=True))
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertIn(cmds.getAttr(node + ".backend"), ("C++", "Python", "CUDA + C++"))

    def test_world_union_and_independent_python_solver_parity(self):
        a = cmds.polyCube(constructionHistory=False)[0]
        b = cmds.polySphere(subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0]
        group = cmds.group(a, b)
        cmds.setAttr(group + ".translate", 3, -2, 5)
        cmds.setAttr(group + ".rotate", 8, 22, 17)
        cmds.setAttr(group + ".scale", 1.2, 0.8, 1.6)
        cmds.setAttr(b + ".translateX", 0.6)
        before = {x: signature(x) for x in (a, b)}
        cmds.select(group)
        data = [scene.capture_mesh(p, packed=True) for p in scene.selected_mesh_paths()]
        node, mesh = self.create(polish=2)
        expected = solver.remesh(data, 24, 2, "python")
        actual = signature(mesh)
        assert_points(self, actual[0], expected.points)
        self.assertEqual(actual[1], [4] * len(expected.quads))
        self.assertEqual(actual[2], [v for q in expected.quads for v in q])
        self.assertEqual(cmds.getAttr(node + ".closedBorders"), expected.closed_borders)
        self.assertEqual({x: signature(x) for x in (a, b)}, before)
        edges = om.MItMeshEdge(mesh_fn(mesh).object())
        while not edges.isDone():
            self.assertFalse(edges.onBoundary())
            edges.next()

    def test_single_creation_undo_redo_and_node_selection(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source + ".f[0]")
        before, selection, geometry = set(cmds.ls()), cmds.ls(selection=True, long=True), signature(source)
        node, mesh = self.create()
        expected = signature(mesh)
        self.assertEqual(cmds.ls(selection=True), [node])
        self.assertTrue(cmds.listConnections(mesh_fn(mesh).fullPathName(), type="shadingEngine"))
        self.assertEqual(signature(source), geometry)
        cmds.undo()
        self.assertEqual(set(cmds.ls()), before)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        cmds.redo()
        self.assertEqual(signature(mesh), expected)
        self.assertEqual(cmds.ls(selection=True), [node])

    def test_controls_reuse_volume_and_source_changes_rebuild(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create()
        before = signature(mesh)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 1)
        cmds.setAttr(node + ".polish", 3)
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 1)
        cmds.setAttr(node + ".offset", 0.1)
        moved = signature(mesh)
        self.assertNotEqual(moved[0], before[0])
        self.assertEqual(moved[1:], before[1:])
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 1)
        cmds.setAttr(node + ".smoothNormals", False)
        self.assertEqual(signature(mesh), moved)
        fn = mesh_fn(mesh)
        self.assertTrue(all(not fn.isEdgeSmooth(i) for i in range(fn.numEdges)))
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 1)
        cmds.setAttr(source + ".translateX", 2)
        translated = signature(mesh)
        assert_points(self, translated[0], [(p[0] + 2, p[1], p[2]) for p in moved[0]])
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 2)
        old_faces = mesh_fn(mesh).numPolygons
        cmds.setAttr(node + ".resolution", 32)
        self.assertGreater(mesh_fn(mesh).numPolygons, old_faces)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 3)
        cmds.move(0.25, 0, 0, source + ".vtx[0]", relative=True, objectSpace=True)
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 4)

    def test_active_preview_and_creases_follow_connected_source(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        shape = mesh_fn(source).fullPathName()
        cmds.setAttr(shape + ".displaySmoothMesh", 2)
        cmds.setAttr(shape + ".smoothLevel", 1)
        cmds.polyCrease(source + ".e[0:3]", value=1)
        cmds.select(source)
        data = [scene.capture_mesh(p, packed=True) for p in scene.selected_mesh_paths()]
        node, mesh = self.create()
        expected = solver.remesh(data, 24, 1, "python")
        assert_points(self, signature(mesh)[0], expected.points)
        before = signature(mesh)
        cmds.setAttr(shape + ".smoothLevel", 2)
        self.assertNotEqual(signature(mesh)[0], before[0])
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 2)
        cmds.setAttr(node + ".useSmoothPreview", False)
        cage = signature(mesh)
        self.assertNotEqual(cage[0], before[0])
        cmds.setAttr(shape + ".smoothLevel", 1)
        self.assertEqual(signature(mesh), cage)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 3)

    def test_open_borders_close_and_topology_can_change_live(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        lid_normal = mesh_fn(source).getPolygonNormal(0)
        cmds.delete(source + ".f[0]")
        cmds.select(source)
        before = signature(source)
        node, mesh = self.create()
        self.assertEqual(cmds.getAttr(node + ".closedBorders"), 1)
        self.assertEqual(signature(source), before)
        fn = mesh_fn(source)
        opposite = next(i for i in range(fn.numPolygons) if fn.getPolygonNormal(i) * lid_normal < -0.9)
        cmds.delete(source + ".f[{}]".format(opposite))
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 2)
        self.assertEqual(set(signature(mesh)[1]), {4})

    def test_instances_and_sparse_inputs_use_connections(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        instance = cmds.instance(source)[0]
        cmds.setAttr(instance + ".translateX", 3)
        cmds.select([source, instance])
        node, mesh = self.create(polish=0)
        points = signature(mesh)[0]
        self.assertLess(min(p[0] for p in points), 0)
        self.assertGreater(max(p[0] for p in points), 3)
        cmds.removeMultiInstance(node + ".input[0]", b=True)
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 2)
        renamed = cmds.rename(instance, "renamedInput")
        cmds.setAttr(renamed + ".translateY", 1)
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), 3)

    def test_disabled_and_invalid_evaluation_preserve_selection_and_pass_through(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.setAttr(source + ".translate", 4, 2, -3)
        cmds.select(source)
        node, mesh = self.create()
        cmds.select(source)
        before = signature(source)
        cmds.setAttr(node + ".enabled", False)
        assert_points(self, signature(mesh)[0], before[0])
        self.assertEqual(signature(mesh)[1:], before[1:])
        self.assertTrue(cmds.getAttr(node + ".status").startswith("Disabled"))
        cmds.setAttr(node + ".enabled", True)
        plane = cmds.polyPlane(constructionHistory=False)[0]
        plane_shape = mesh_fn(plane).fullPathName()
        cmds.connectAttr(plane_shape + ".outMesh", node + ".input[0].inputMesh", force=True)
        cmds.connectAttr(plane_shape + ".worldMatrix[0]", node + ".input[0].inputMatrix", force=True)
        cmds.select(source)
        nodes = set(cmds.ls())
        self.assertTrue(cmds.getAttr(node + ".status").startswith("Invalid"))
        self.assertEqual(signature(mesh), signature(plane))
        self.assertEqual(cmds.ls(selection=True), [source])
        self.assertEqual(set(cmds.ls()), nodes)
        cmds.disconnectAttr(plane_shape + ".outMesh", node + ".input[0].inputMesh")
        self.assertTrue(cmds.getAttr(node + ".status").startswith("Invalid"))

    def test_creation_failure_rolls_back_all_scene_edits(self):
        source = cmds.polyPlane(constructionHistory=False)[0]
        cmds.select(source)
        before = set(cmds.ls())
        selection = cmds.ls(selection=True, long=True)
        with self.assertRaisesRegex(RuntimeError, "volume"):
            self.create()
        self.assertEqual(set(cmds.ls()), before)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        cmds.delete(source)
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        before = set(cmds.ls())
        selection = cmds.ls(selection=True, long=True)
        original = om.MDGModifier
        for fail_index in (1, 2):
            count = [0]

            class FailingModifier:
                def __init__(self):
                    count[0] += 1
                    self.index = count[0]
                    self.actual = original()

                def __getattr__(self, key):
                    return getattr(self.actual, key)

                def doIt(self):
                    self.actual.doIt()
                    if self.index == fail_index:
                        raise RuntimeError("simulated modifier failure")

            with patch.object(om, "MDGModifier", FailingModifier):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    self.create()
            self.assertEqual(set(cmds.ls()), before)
            self.assertEqual(cmds.ls(selection=True, long=True), selection)

    def test_delete_history_bakes_the_visible_output(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create(offset=0.03)
        before = signature(mesh)
        cmds.delete(mesh, constructionHistory=True)
        self.assertFalse(cmds.objExists(node))
        self.assertEqual(signature(mesh), before)
        cmds.setAttr(source + ".translateX", 2)
        self.assertEqual(signature(mesh), before)

    def test_empty_selection_undo_disabled_and_codehive_have_no_tool_window(self):
        cmds.select(clear=True)
        with self.assertRaisesRegex(ValueError, "Select"):
            self.create()
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        cmds.undoInfo(state=False)
        with self.assertRaisesRegex(ValueError, "Undo"):
            self.create()
        cmds.undoInfo(state=True)
        entry = ROOT / "src" / "MESH_Create_DynaMesh.py"
        namespace = {"__file__": "/incorrect/codehive.py", "__name__": "shared_codehive"}
        actual_create = scene.create_node_from_selection
        with (
            patch.object(
                scene, "create_node_from_selection", side_effect=lambda: actual_create(resolution=24, backend=BACKEND)
            ) as action,
            patch.object(cmds, "confirmDialog", side_effect=AssertionError("dialog opened")),
        ):
            exec(compile(entry.read_bytes(), str(entry), "exec"), namespace)
            action.assert_called_once_with()
        self.assertEqual(Path(namespace["_dynamesh_entry"]), entry)
        self.assertEqual(len(cmds.ls(type=schema.NODE_NAME)), 1)

    def test_distance_attributes_respect_maya_units(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create(offset=0.1)
        before = signature(mesh)
        width = cmds.getAttr(node + ".voxelWidth")
        cmds.currentUnit(linear="m")
        self.assertAlmostEqual(cmds.getAttr(node + ".offset"), 0.001)
        self.assertAlmostEqual(cmds.getAttr(node + ".voxelWidth"), width / 100)
        self.assertEqual(signature(mesh), before)

    def test_saved_runtime_reopens_in_fresh_process_without_repository(self):
        source = cmds.polySphere(subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create(polish=2, offset=0.05)
        expected = signature(mesh)
        with tempfile.TemporaryDirectory(prefix="bt-dynamesh-node-scene-") as folder:
            target = Path(folder) / "node.ma"
            report = Path(folder) / "reopen.json"
            cmds.file(rename=str(target))
            cmds.file(save=True, type="mayaAscii", force=True)
            code = """import json,sys
import maya.standalone
maya.standalone.initialize(name="python")
from maya import cmds
import maya.api.OpenMaya as om
assert not any("maya-volume-remesh-stage-" in p or "maya-volume-remesh/src" in p for p in sys.path)
cmds.file(sys.argv[1],open=True,force=True,executeScriptNodes=False)
node="btDynaMesh1"
path=om.MSelectionList().add("|dynamesh1").getDagPath(0);path.extendToShape()
fn=om.MFnMesh(path);counts,indices=fn.getVertices()
with open(sys.argv[2],"w") as stream:
    json.dump({"status":cmds.getAttr(node+".status"),"backend":cmds.getAttr(node+".backend"),
               "points":[tuple(p)[:3] for p in fn.getPoints(om.MSpace.kWorld)],
               "counts":list(counts),"indices":list(indices)},stream)
maya.standalone.uninitialize()
"""
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            env["MAYA_PLUG_IN_PATH"] = DEPLOYMENT + os.pathsep + env.get("MAYA_PLUG_IN_PATH", "")
            process = subprocess.run(
                [sys.executable, "-c", code, str(target), str(report)],
                env=env,
                cwd=folder,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertTrue(report.is_file(), process.stdout + process.stderr)
            actual = json.loads(report.read_text())
            self.assertEqual(actual["status"], "Ready")
            self.assertEqual(actual["backend"], cmds.getAttr(node + ".backend"))
            assert_points(self, actual["points"], expected[0], 1e-6)
            self.assertEqual((actual["counts"], actual["indices"]), (expected[1], expected[2]))

    @unittest.skipUnless(BACKEND == "cpp", "Fresh fallback is checked from the native test run")
    def test_native_failure_automatically_uses_python_without_dialog(self):
        with tempfile.TemporaryDirectory(prefix="bt-dynamesh-auto-fallback-") as folder:
            report = Path(folder) / "fallback.json"
            code = """import json,sys,os
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
os.environ["MAYA_VOLUME_REMESH_PLUGIN_DIR"]=sys.argv[2]
os.environ.pop("MAYA_VOLUME_REMESH_BACKEND",None)
os.environ.pop("MAYA_VOLUME_REMESH_FORCE_PY",None)
import maya.standalone
maya.standalone.initialize(name="python")
from maya import cmds
from MESH_DynaMesh_Core.scene import create_node_from_selection
source=cmds.polyCube(constructionHistory=False)[0];cmds.select(source)
with patch("maya_volume_remesh._native.dual_backend.load_native_node",side_effect=RuntimeError("simulated no compiler")), \\
     patch("maya_volume_remesh._native.fallback.require_python_fallback",side_effect=AssertionError("dialog opened")):
    node,mesh=create_node_from_selection(resolution=16)
    with open(sys.argv[3],"w") as stream:
        json.dump({"backend":cmds.getAttr(node+".backend"),"status":cmds.getAttr(node+".status"),
                   "faces":cmds.polyEvaluate(mesh,face=True)},stream)
maya.standalone.uninitialize()
"""
            process = subprocess.run(
                [sys.executable, "-c", code, str(ROOT / "src"), folder, str(report)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertTrue(report.is_file(), process.stdout + process.stderr)
            actual = json.loads(report.read_text())
            self.assertEqual(actual["backend"], "Python")
            self.assertEqual(actual["status"], "Ready")
            self.assertGreater(actual["faces"], 0)

    @unittest.skipUnless(os.environ.get("BT_DM_TEST_CUDA") == "1", "Requires an NVIDIA CUDA host")
    def test_real_cuda_matches_cpu_solver(self):
        source = cmds.polySphere(subdivisionsX=64, subdivisionsY=32, constructionHistory=False)[0]
        cmds.select(source)
        data = [scene.capture_mesh(p, packed=True) for p in scene.selected_mesh_paths()]
        node, mesh = scene.create_node_from_selection(resolution=64, backend="cpp")
        self.assertEqual(cmds.getAttr(node + ".backend"), "CUDA + C++")
        expected = solver.remesh(data, 64, 1, "cpp")
        assert_points(self, signature(mesh)[0], expected.points, 1e-6)
        self.assertEqual(signature(mesh)[2], [i for face in expected.quads for i in face])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DynaMeshNodeTests)
    )
    report = os.environ.get("BT_DM_TEST_REPORT")
    if report:
        Path(report).write_text(
            json.dumps(
                {
                    "backend": BACKEND,
                    "tests": result.testsRun,
                    "failures": len(result.failures),
                    "errors": len(result.errors),
                    "skipped": len(result.skipped),
                }
            )
        )
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
