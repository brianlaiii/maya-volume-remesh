"""Fresh Maya lifecycle checks for OpenVDB, missing OpenVDB, and missing native runtime."""
# ruff: noqa: E402

import json
import os
from pathlib import Path
import platform
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
from MESH_DynaMesh_Core import runtime, scene, solver

ENGINE = os.environ.get("MVR_NODE_ENGINE", "openvdb")
DEPLOYMENT = tempfile.mkdtemp(prefix="mvr-three-backends-runtime-")
os.environ[runtime.PLUGIN_DIR_ENV] = DEPLOYMENT
os.environ["MAYA_VOLUME_REMESH_OPENVDB"] = "auto"
os.environ.pop("MAYA_VOLUME_REMESH_BACKEND", None)
if ENGINE != "openvdb":
    os.environ["MAYA_VOLUME_REMESH_OPENVDB_LIBRARY"] = "/nonexistent/openvdb.so"
    os.environ["MAYA_VOLUME_REMESH_OPENVDB_ROOT"] = "/nonexistent/openvdb-sdk"


def signature(obj):
    path = om.MSelectionList().add(obj).getDagPath(0)
    if path.node().hasFn(om.MFn.kTransform):
        path.extendToShape()
    fn = om.MFnMesh(path)
    counts, indices = fn.getVertices()
    return [tuple(p)[:3] for p in fn.getPoints(om.MSpace.kWorld)], list(counts), list(indices)


class ThreeBackendNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ENGINE == "python":
            with (
                patch(
                    "maya_volume_remesh._native.dual_backend.load_native_node",
                    side_effect=RuntimeError("No native compiler"),
                ),
                patch(
                    "maya_volume_remesh._native.fallback.require_python_fallback",
                    side_effect=AssertionError("Dialog opened"),
                ),
            ):
                runtime.ensure_loaded()
        else:
            runtime.ensure_loaded()

    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.currentUnit(linear="cm")
        cmds.undoInfo(state=True)

    def create(self, resolution=24, **settings):
        return scene.create_node_from_selection(resolution=resolution, **settings)

    def test_selected_backend_and_geometry_match_its_solver(self):
        a = cmds.polyCube(constructionHistory=False)[0]
        b = cmds.polySphere(subdivisionsX=12, subdivisionsY=8, constructionHistory=False)[0]
        cmds.setAttr(b + ".translateX", 0.6)
        group = cmds.group(a, b)
        cmds.setAttr(group + ".translate", 3, -2, 5)
        before = [signature(x) for x in (a, b)]
        cmds.select(group)
        data = [scene.capture_mesh(p, packed=True) for p in scene.selected_mesh_paths()]
        node, mesh = self.create(polish=2)
        expected_backend = {"openvdb": "OpenVDB", "cpp": "C++", "python": "Python"}[ENGINE]
        self.assertEqual(cmds.getAttr(node + ".backend"), expected_backend)
        self.assertEqual(cmds.getAttr(node + ".status"), "Ready")
        expected = solver.remesh(data, 24, 2, ENGINE)
        points, counts, indices = signature(mesh)
        self.assertEqual(counts, [4] * len(expected.quads))
        self.assertEqual(indices, [v for q in expected.quads for v in q])
        self.assertLess(max(abs(a - b) for p, q in zip(points, expected.points) for a, b in zip(p, q)), 1e-6)
        self.assertEqual(before, [signature(x) for x in (a, b)])
        if ENGINE != "openvdb":
            self.assertTrue(cmds.getAttr(node + ".backendReason"))

    def test_cached_polish_offset_and_input_edits(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create()
        before = cmds.getAttr(node + ".volumeBuilds")
        cmds.setAttr(node + ".polish", 2)
        signature(mesh)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), before)
        cmds.setAttr(node + ".offset", 0.02)
        signature(mesh)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), before)
        cmds.setAttr(source + ".translateX", 0.3)
        signature(mesh)
        self.assertEqual(cmds.getAttr(node + ".volumeBuilds"), before + 1)
        cmds.setAttr(node + ".enabled", False)
        actual, expected = signature(mesh), signature(source)
        self.assertEqual(actual[1:], expected[1:])
        self.assertLess(max(abs(a - b) for p, q in zip(actual[0], expected[0]) for a, b in zip(p, q)), 1e-6)

    def test_undo_redo_and_baking(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create()
        before = signature(mesh)
        cmds.undo()
        self.assertFalse(cmds.objExists(node))
        cmds.redo()
        self.assertEqual(signature(mesh), before)
        cmds.delete(mesh, constructionHistory=True)
        self.assertFalse(cmds.objExists(node))
        self.assertEqual(signature(mesh), before)

    def test_resolution_is_not_clamped_on_fallback(self):
        source = cmds.polyCube(width=10, height=0.05, depth=0.05, constructionHistory=False)[0]
        cmds.select(source)
        if ENGINE == "openvdb":
            node, mesh = self.create(resolution=600, polish=0)
            self.assertEqual(cmds.getAttr(node + ".resolution"), 600)
            self.assertEqual(cmds.getAttr(node + ".backend"), "OpenVDB")
            self.assertTrue(signature(mesh)[1])
        else:
            with self.assertRaises(RuntimeError):
                self.create(resolution=600)
            self.assertEqual(cmds.ls(type="btDynaMesh"), [])
            self.assertTrue(cmds.objExists(source))

    def test_saved_scene_reopens_without_repository_and_can_lose_openvdb(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        node, mesh = self.create()
        with tempfile.TemporaryDirectory(prefix="mvr-three-backends-scene-") as folder:
            saved = Path(folder) / "saved.ma"
            cmds.file(rename=str(saved))
            cmds.file(save=True, type="mayaAscii", force=True)
            code = """import json,sys
import maya.standalone
maya.standalone.initialize(name='python')
from maya import cmds
cmds.file(sys.argv[1],open=True,force=True)
node=cmds.ls(type='btDynaMesh')[0]
assert cmds.getAttr(node+'.status')=='Ready'
print('MVR_REOPEN '+json.dumps({'backend':cmds.getAttr(node+'.backend'),'faces':cmds.getAttr(node+'.faceCount')}))
"""
            prefix = ["arch", "-" + platform.machine()] if sys.platform == "darwin" else []
            env = dict(os.environ, MAYA_PLUG_IN_PATH=DEPLOYMENT, MAYA_SKIP_USERSETUP_PY="1", PYTHONNOUSERSITE="1")
            env.pop("PYTHONPATH", None)
            env.pop("MAYA_MODULE_PATH", None)
            for missing in (False, True) if ENGINE == "openvdb" else (False,):
                if missing:
                    env["MAYA_VOLUME_REMESH_OPENVDB_LIBRARY"] = "/nonexistent/openvdb.so"
                result = subprocess.run(
                    prefix + [sys.executable, "-I", "-c", code, str(saved)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = next(
                    line.split("MVR_REOPEN ", 1)[1]
                    for line in result.stdout.splitlines()
                    if line.startswith("MVR_REOPEN ")
                )
                actual = json.loads(payload)
                expected = "C++" if missing else {"openvdb": "OpenVDB", "cpp": "C++", "python": "Python"}[ENGINE]
                self.assertEqual(actual["backend"], expected)
                self.assertGreater(actual["faces"], 0)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ThreeBackendNodeTests)
    )
    maya.standalone.uninitialize()
    sys.exit(0 if result.wasSuccessful() else 1)
