"""Optional OpenVDB behavior and the real C++/independent Python recovery paths."""
# ruff: noqa: E402

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from MESH_DynaMesh_Core import native, openvdb, solver, vdb_build
from test_dynamesh_solver import cube, volume


class RoutingTests(unittest.TestCase):
    def setUp(self):
        settings = patch.dict(os.environ, MAYA_VOLUME_REMESH_OPENVDB="auto", MAYA_VOLUME_REMESH_BACKEND="auto")
        settings.start()
        self.addCleanup(settings.stop)

    def test_missing_openvdb_uses_real_builtin_cpp(self):
        with (
            patch.object(vdb_build.kernel, "preload", side_effect=RuntimeError("SDK missing")),
            self.assertWarnsRegex(RuntimeWarning, "SDK missing"),
        ):
            result = solver.remesh([cube()], 16, 0)
        self.assertEqual(result.backend, "C++")
        self.assertGreater(volume(result), 0)

    def test_missing_both_native_backends_uses_real_python(self):
        with (
            patch.object(vdb_build.kernel, "preload", side_effect=RuntimeError("SDK missing")),
            patch.object(native.kernel, "loaded", None),
            patch.object(native.kernel, "error", None),
            patch.object(native.kernel, "preload", side_effect=RuntimeError("Compiler missing")),
            self.assertWarns(RuntimeWarning),
        ):
            result = solver.remesh([cube()], 16, 0)
        self.assertEqual(result.backend, "Python")
        self.assertGreater(volume(result), 0)

    def test_explicit_python_skips_openvdb_and_cpp(self):
        with (
            patch.object(vdb_build.kernel, "preload") as vdb,
            patch.object(native.kernel, "preload") as cpp,
            patch.dict(os.environ, MAYA_VOLUME_REMESH_BACKEND="python"),
        ):
            self.assertEqual(solver.remesh([cube()], 16, 0).backend, "Python")
        vdb.assert_not_called()
        cpp.assert_not_called()

    def test_strict_openvdb_missing_dependency_stops(self):
        with (
            patch.object(vdb_build.kernel, "preload", side_effect=RuntimeError("SDK missing")),
            patch.object(native, "solve_builtin") as builtin,
        ):
            with self.assertRaisesRegex(openvdb.Unavailable, "SDK missing"):
                solver.remesh([cube()], 16, 0, "openvdb")
        builtin.assert_not_called()

    def test_geometry_cancel_memory_and_callback_errors_do_not_fallback(self):
        for error in (
            ValueError("Invalid mesh"),
            solver.Cancelled("Cancelled"),
            MemoryError("Out of memory"),
            RuntimeError("Callback failed"),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(openvdb, "solve", side_effect=error),
                patch.object(native, "solve_builtin") as builtin,
            ):
                with self.assertRaises(type(error)):
                    solver.remesh([cube()], 16, 0)
                builtin.assert_not_called()

    def test_recoverable_openvdb_runtime_failure_uses_cpp(self):
        with (
            patch.object(openvdb, "solve", side_effect=openvdb.Unavailable("Output copy failed")),
            self.assertWarnsRegex(RuntimeWarning, "Output copy failed"),
        ):
            self.assertEqual(solver.remesh([cube()], 16, 0).backend, "C++")

    def test_fallback_never_clamps_resolution(self):
        with (
            patch.object(vdb_build.kernel, "preload", side_effect=RuntimeError("SDK missing")),
            self.assertWarns(RuntimeWarning),
        ):
            with self.assertRaisesRegex(ValueError, "OpenVDB is required"):
                solver.remesh([cube()], 600, 0)

    def test_bad_sdk_and_prebuilt_library_fail_cleanly(self):
        with (
            tempfile.TemporaryDirectory(prefix="mvr-no-sdk-") as folder,
            patch.dict(os.environ, {vdb_build.ROOT_ENV: folder}),
        ):
            with self.assertRaisesRegex(RuntimeError, "SDK not found"):
                vdb_build.dependency_flags("x86_64")
        with patch.dict(os.environ, {vdb_build.LIBRARY_ENV: "/nonexistent/openvdb.so"}):
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                vdb_build.build()


class OpenVdbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.library = vdb_build.kernel.preload()
        except RuntimeError as exc:
            if os.environ.get("MVR_REQUIRE_OPENVDB") == "1":
                raise
            raise unittest.SkipTest("Optional OpenVDB SDK unavailable: " + str(exc))

    def test_real_union_closed_quads_and_outward_normals(self):
        for meshes in ([cube()], [cube(), cube((0.7, 0.3, 0.5))], [cube(), cube(scale=(1, 1, 1))]):
            with self.subTest(parts=len(meshes)):
                result = solver.remesh(meshes, 24, 2, "openvdb")
                self.assertEqual(result.backend, "OpenVDB")
                self.assertTrue(all(len(q) == 4 for q in result.quads))
                solver.validate_result(result)
                self.assertGreater(volume(result), 0)

    def test_caps_close_without_modifying_source(self):
        points, faces = cube()
        opened = faces[2:]
        before = (list(points), list(opened))
        result = solver.remesh([(points, opened)], 24, 1, "openvdb")
        self.assertEqual(result.closed_borders, 1)
        self.assertEqual((points, opened), before)
        solver.validate_result(result)

    def test_high_resolution_and_sparse_distant_solids(self):
        for meshes, resolution in (
            ([cube(scale=(10, 0.05, 0.05))], 600),
            ([cube((-100, -100, -100)), cube((100, 100, 100))], 1200),
        ):
            result = solver.remesh(meshes, resolution, 0, "openvdb")
            self.assertTrue(result.quads)
            self.assertAlmostEqual(
                result.spacing,
                max(
                    max(p[k] for points, _ in meshes for p in points)
                    - min(p[k] for points, _ in meshes for p in points)
                    for k in range(3)
                )
                / resolution,
            )

    def test_real_cancel_and_callback_failure_do_not_start_cpp(self):
        with patch.object(native, "solve_builtin") as builtin:
            for threshold in (0.02, 0.4, 0.55, 0.8, 0.96):
                with self.assertRaises(solver.Cancelled):
                    solver.remesh([cube()], 24, 2, "openvdb", lambda fraction, message: fraction < threshold)

            def broken(fraction, message):
                if fraction >= 0.4:
                    raise RuntimeError("Progress callback failed")

            with self.assertRaisesRegex(RuntimeError, "Progress callback failed"):
                solver.remesh([cube()], 24, 2, "auto", broken)
        builtin.assert_not_called()

    def test_native_loader_cache_and_missing_sidecar(self):
        compiler = os.environ.get("CXX", "c++")
        with tempfile.TemporaryDirectory(prefix="mvr-vdb-loader-") as folder:
            output = Path(folder) / "loader"
            command = [compiler, "-std=c++17", "-O2", str(ROOT / "tests/test_openvdb_loader.cpp"), "-o", str(output)]
            if sys.platform != "darwin":
                command.append("-ldl")
            subprocess.run(command, check=True, capture_output=True, text=True)
            env = dict(os.environ, MAYA_VOLUME_REMESH_OPENVDB="auto")
            env.pop(vdb_build.LIBRARY_ENV, None)
            for library in (str(vdb_build.build()), "/nonexistent/openvdb.so"):
                result = subprocess.run([str(output), library], env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
