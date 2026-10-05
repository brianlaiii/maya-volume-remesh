"""Run explicitly with mayapy. Generated fixtures and external temporary files."""

# ruff: noqa: E402
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if __name__ != "__main__":
    raise unittest.SkipTest("Run explicitly with mayapy.")

import maya.standalone

maya.standalone.initialize(name="python")
from maya import cmds
import maya.api.OpenMaya as om

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from MESH_DynaMesh_Core import scene
from MESH_DynaMesh_Core.solver import Cancelled


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
    return ([tuple(p) for p in fn.getPoints(om.MSpace.kWorld)], list(counts), list(connects))


class DynaMeshMayaTests(unittest.TestCase):
    def setUp(self):
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        self.backend = os.environ.get("MAYA_VOLUME_REMESH_BACKEND", "cpp")

    def create(self, **kwargs):
        return scene.create_from_selection(resolution=24, backend=self.backend, **kwargs)

    def test_open_borders_close_from_selection_with_preview_and_undo(self):
        for preview in (0, 2):
            cmds.file(new=True, force=True)
            source = cmds.polyCube(constructionHistory=False)[0]
            fn = mesh_fn(source)
            lids = [i for i in range(fn.numPolygons) if abs(fn.getPolygonNormal(i, om.MSpace.kObject).y) > 0.9]
            cmds.delete([source + ".f[{}]".format(i) for i in lids])
            cmds.setAttr(source + ".translate", 4, -2, 3)
            cmds.setAttr(source + ".rotate", 15, 20, 30)
            cmds.setAttr(source + ".scale", 1.2, 0.8, 1.5)
            shape = mesh_fn(source).fullPathName()
            cmds.setAttr(shape + ".displaySmoothMesh", preview)
            cmds.setAttr(shape + ".smoothLevel", 1)
            cmds.select(source)
            before, nodes = signature(source), set(cmds.ls())
            name, result = self.create(hide_sources=True)
            self.assertEqual(result.closed_borders, 2)
            self.assertEqual(signature(source), before)
            self.assertEqual(set(signature(name)[1]), {4})
            self.assertFalse(cmds.getAttr(shape + ".visibility"))
            from MESH_DynaMesh_Core.solver import validate_result

            validate_result(result)
            edges = om.MItMeshEdge(mesh_fn(name).object())
            while not edges.isDone():
                self.assertFalse(edges.onBoundary())
                edges.next()
            cmds.undo()
            self.assertEqual(set(cmds.ls()), nodes)
            self.assertEqual(signature(source), before)
            self.assertTrue(cmds.getAttr(shape + ".visibility"))
            cmds.redo()
            self.assertTrue(cmds.objExists(name))
            self.assertEqual(signature(source), before)
            cmds.undo()
            cmds.select(source)
            with self.assertRaises(Cancelled):
                self.create(progress=lambda fraction, message: "Closing open" not in message)
            self.assertEqual(set(cmds.ls()), nodes)
            self.assertEqual(signature(source), before)

    def test_smooth_preview_is_sampled_without_changing_the_cage_or_scene(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.setAttr(source + ".translate", 3, -2, 4)
        cmds.setAttr(source + ".rotate", 10, 20, 30)
        cmds.setAttr(source + ".scale", 1.5, 0.8, 1.2)
        shape = mesh_fn(source).fullPathName()
        cmds.setAttr(shape + ".displaySmoothMesh", 2)
        cmds.setAttr(shape + ".smoothLevel", 2)
        cmds.select(source)
        path = scene.selected_mesh_paths()[0]
        before, nodes = signature(source), set(cmds.ls())
        points, triangles = scene.capture_mesh(path)
        packed = scene.capture_mesh(path, packed=True)
        packed_points, packed_triangles = packed.python_mesh()
        self.assertEqual(packed_triangles, triangles)
        self.assertLess(max(abs(a - b) for p, q in zip(packed_points, points) for a, b in zip(p, q)), 1e-12)
        self.assertGreater(len(points), len(before[0]))
        self.assertEqual(set(cmds.ls()), nodes)
        self.assertEqual(signature(source), before)
        fn = mesh_fn(source)
        owner = om.MFnMeshData().create()
        preview = om.MFnMesh(fn.generateSmoothMesh(parent=owner))
        reference = [tuple(p * path.inclusiveMatrix())[:3] for p in preview.getPoints(om.MSpace.kObject)]
        self.assertEqual(points, reference)
        name, result = self.create()
        self.assertEqual(signature(source), before)
        self.assertEqual(cmds.getAttr(shape + ".displaySmoothMesh"), 2)
        self.assertEqual(set(signature(name)[1]), {4})
        from MESH_DynaMesh_Core.solver import remesh

        expected = remesh([(points, triangles)], 24, 1, self.backend)
        self.assertEqual(result.points, expected.points)
        self.assertEqual(result.quads, expected.quads)

    def test_create_world_union_preserves_sources_and_single_undo_redo(self):
        a = cmds.polyCube(name="sourceA", constructionHistory=False)[0]
        b = cmds.polySphere(name="sourceB", subdivisionsX=16, subdivisionsY=12, constructionHistory=False)[0]
        parent = cmds.group(a, b, name="sources")
        cmds.setAttr(parent + ".translate", 7, 2, -4)
        cmds.setAttr(parent + ".rotate", 13, 25, 31)
        cmds.setAttr(parent + ".scale", 1.5, 0.8, 2)
        cmds.setAttr(b + ".translateX", 0.8)
        before = {x: signature(x) for x in (a, b)}
        cmds.select(parent)
        original_selection = cmds.ls(selection=True, long=True)
        name, result = self.create(hide_sources=True)
        created = signature(name)
        self.assertEqual(set(created[1]), {4})
        self.assertEqual(mesh_fn(name).numPolygons, len(result.quads))
        self.assertEqual(cmds.ls(selection=True, long=True), [name])
        self.assertEqual(cmds.getAttr(name + ".translate"), [(0.0, 0.0, 0.0)])
        self.assertTrue(cmds.listConnections(mesh_fn(name).fullPathName(), type="shadingEngine"))
        for obj in (a, b):
            self.assertEqual(signature(obj), before[obj])
            self.assertFalse(cmds.getAttr(mesh_fn(obj).fullPathName() + ".visibility"))
        cmds.undo()
        self.assertFalse(cmds.objExists(name))
        self.assertEqual(cmds.ls(selection=True, long=True), original_selection)
        for obj in (a, b):
            self.assertTrue(cmds.getAttr(mesh_fn(obj).fullPathName() + ".visibility"))
        cmds.redo()
        self.assertEqual(signature(name), created)
        self.assertEqual(cmds.ls(selection=True, long=True), [name])

    def test_torus_hole_and_native_mesh_only_saved_scene(self):
        source = cmds.polyTorus(
            radius=2, sectionRadius=0.65, subdivisionsX=24, subdivisionsY=12, constructionHistory=False
        )[0]
        cmds.select(source)
        name, result = self.create()
        points = signature(name)[0]
        self.assertEqual(result.closed_borders, 0)
        self.assertGreater(min((p[0] ** 2 + p[2] ** 2) ** 0.5 for p in points), 1)
        with tempfile.TemporaryDirectory(prefix="bt-dynamesh-scene-") as folder:
            target = str(Path(folder) / "result.ma")
            cmds.file(rename=target)
            cmds.file(save=True, type="mayaAscii", force=True)
            self.assertNotIn('requires "authoring_command"', Path(target).read_text())
            cmds.file(new=True, force=True)
            cmds.file(target, open=True, force=True)
            reopened = signature(name)[0]
            self.assertEqual(len(reopened), len(points))
            self.assertLess(max(abs(a - b) for p, q in zip(reopened, points) for a, b in zip(p, q)), 1e-6)
            self.assertEqual(set(signature(name)[1]), {4})

    def test_authoring_failure_rolls_back_material_visibility_and_nodes(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        # Load once so plugin registration is outside the failure fixture.
        plugin = str(ROOT / "src" / "MESH_DynaMesh_Core" / "authoring_command.py")
        if not cmds.pluginInfo(plugin, query=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=True)
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
                        raise RuntimeError("simulated authoring failure")

            nodes = set(cmds.ls())
            selection = cmds.ls(selection=True, long=True)
            with patch.object(om, "MDGModifier", FailingModifier):
                with self.assertRaisesRegex(RuntimeError, "simulated authoring"):
                    self.create(hide_sources=True)
            self.assertEqual(set(cmds.ls()), nodes)
            self.assertEqual(cmds.ls(selection=True, long=True), selection)
            self.assertTrue(cmds.getAttr(mesh_fn(source).fullPathName() + ".visibility"))

    def test_components_groups_hidden_intermediate_and_instance_paths(self):
        a = cmds.polyCube(constructionHistory=False)[0]
        b = cmds.instance(a)[0]
        cmds.setAttr(b + ".translateX", 3)
        cmds.select(a + ".f[0]", b)
        self.assertEqual(len(scene.selected_mesh_paths()), 2)
        name, _ = self.create()
        self.assertTrue(cmds.objExists(name))
        cmds.select(a)
        with self.assertRaisesRegex(ValueError, "instanced"):
            self.create(hide_sources=True)

    def test_empty_zero_volume_and_locked_visibility_leave_scene_unchanged(self):
        cmds.select(clear=True)
        with self.assertRaises(ValueError):
            self.create()
        source = cmds.polyPlane(constructionHistory=False)[0]
        cmds.select(source)
        nodes, selection = set(cmds.ls()), cmds.ls(selection=True, long=True)
        with self.assertRaisesRegex(ValueError, "volume"):
            self.create()
        self.assertEqual(set(cmds.ls()), nodes)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.setAttr(mesh_fn(source).fullPathName() + ".visibility", lock=True)
        cmds.select(source)
        with self.assertRaisesRegex(ValueError, "locked"):
            self.create(hide_sources=True)

    def test_cancel_and_error_do_not_edit_or_change_selection(self):
        source = cmds.polySphere(constructionHistory=False)[0]
        cmds.select(source)
        nodes, selection, before = set(cmds.ls()), cmds.ls(selection=True, long=True), signature(source)
        with self.assertRaises(Cancelled):
            self.create(progress=lambda fraction, message: fraction < 0.55)
        self.assertEqual(set(cmds.ls()), nodes)
        self.assertEqual(cmds.ls(selection=True, long=True), selection)
        self.assertEqual(signature(source), before)
        with patch.object(scene, "remesh", side_effect=RuntimeError("simulated failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.create()
        self.assertEqual(set(cmds.ls()), nodes)

    def test_undo_disabled_and_shared_codehive_entry(self):
        source = cmds.polyCube(constructionHistory=False)[0]
        cmds.select(source)
        cmds.undoInfo(state=False)
        with self.assertRaisesRegex(ValueError, "Undo"):
            self.create()
        cmds.undoInfo(state=True)
        entry = ROOT / "src" / "MESH_Create_DynaMesh.py"
        shared = {"__file__": "/incorrect/codehive.py", "__name__": "shared_codehive"}
        with patch.object(scene, "create_node_from_selection", return_value=("node", "mesh")) as create:
            exec(compile(entry.read_bytes(), str(entry), "exec"), shared)
            create.assert_called_once_with()
        self.assertEqual(Path(shared["_dynamesh_entry"]), entry)


if __name__ == "__main__":
    unittest.main()
