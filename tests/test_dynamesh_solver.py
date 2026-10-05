"""Run with normal Python. Geometry, native parity, cancellation and fallback."""

from array import array
import math
import os
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from MESH_DynaMesh_Core import native, solver
from MESH_DynaMesh_Core.surface import TriangleBVH, triangle_distance_squared


def cube(offset=(0, 0, 0), scale=(2, 2, 2), angle=0):
    points = []
    for z in (-0.5, 0.5):
        for y in (-0.5, 0.5):
            for x in (-0.5, 0.5):
                px, py, pz = x * scale[0], y * scale[1], z * scale[2]
                px, py = math.cos(angle) * px - math.sin(angle) * py, math.sin(angle) * px + math.cos(angle) * py
                points.append((px + offset[0], py + offset[1], pz + offset[2]))
    faces = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))
    triangles = [t for a, b, c, d in faces for t in ((a, b, c), (a, c, d))]
    return points, triangles


def sphere(level=3):
    points = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    for _ in range(level):
        cache = {}

        def midpoint(a, b):
            key = tuple(sorted((a, b)))
            if key not in cache:
                p = tuple((points[a][k] + points[b][k]) * 0.5 for k in range(3))
                length = math.sqrt(sum(v * v for v in p))
                cache[key] = len(points)
                points.append(tuple(v / length for v in p))
            return cache[key]

        new = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new.extend(((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)))
        faces = new
    return points, faces


def components(result):
    neighbors = [set() for _ in result.points]
    for face in result.quads:
        for a, b in zip(face, face[1:] + face[:1]):
            neighbors[a].add(b)
            neighbors[b].add(a)
    pending, count = set(range(len(neighbors))), 0
    while pending:
        count += 1
        stack = [pending.pop()]
        while stack:
            for i in neighbors[stack.pop()]:
                if i in pending:
                    pending.remove(i)
                    stack.append(i)
    return count


def volume(result):
    value = 0
    for a, b, c, d in result.quads:
        for i, j, k in ((a, b, c), (a, c, d)):
            p, q, r = result.points[i], result.points[j], result.points[k]
            value += (
                p[0] * (q[1] * r[2] - q[2] * r[1])
                + p[1] * (q[2] * r[0] - q[0] * r[2])
                + p[2] * (q[0] * r[1] - q[1] * r[0])
            ) / 6
    return value


class DynaMeshTests(unittest.TestCase):
    def test_cpp_owns_prepare_and_validation_for_packed_maya_data(self):
        p, t = cube()
        t = t[:2] + t[4:]
        packed = solver.RawMesh(
            array("d", (v for point in p for v in point)),
            array("i", (i for face in t for i in face)),
            (2.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 0.0, 4.0, 0.0, 7.0, -2.0, 5.0, 1.0),
        )
        with (
            patch.object(solver, "prepare", side_effect=AssertionError("Python preparation")),
            patch.object(native, "prepare", side_effect=AssertionError("Python preparation")),
            patch.object(solver, "validate_result", side_effect=AssertionError("Python validation")),
            patch.object(native, "validate_result", side_effect=AssertionError("Python validation")),
        ):
            result = solver.remesh([packed], 24, 1)
        reference = solver.remesh([packed.python_mesh()], 24, 1, "python")
        self.assertEqual(result.backend, "C++")
        self.assertEqual(result.quads, reference.quads)
        # Python 3.12+ uses more accurate float sums. Native and scalar
        # calculations must agree numerically across supported interpreters.
        self.assertEqual(len(result.points), len(reference.points))
        self.assertLess(max(abs(a - b) for p, q in zip(result.points, reference.points) for a, b in zip(p, q)), 1e-12)
        self.assertIs(solver.validate_result(result), result)
        self.assertIn("native_compute", result.timings)
        self.assertEqual(result.closed_borders, 1)

    def test_open_cube_and_two_open_ends_are_closed_without_input_edits(self):
        p, t = cube()
        for faces, expected in ((t[:2] + t[4:], 1), (t[4:], 2), (t[:-1], 1)):
            before = (list(p), list(faces))
            results = [solver.remesh([(p, faces)], 24, 1, mode) for mode in ("cpp", "python")]
            self.assertEqual((p, faces), before)
            for result in results:
                self.assertEqual(result.closed_borders, expected)
                self.assertEqual(components(result), 1)
                self.assertAlmostEqual(volume(result), 8, delta=0.2)
                solver.validate_result(result)
            self.assertEqual(results[0].quads, results[1].quads)
            self.assertLess(
                max(abs(a - b) for p, q in zip(results[0].points, results[1].points) for a, b in zip(p, q)), 1e-12
            )
        q, u = cube((4, 0, 0), angle=0.3)
        parts = [(p, t[:2] + t[4:]), (q, [tuple(reversed(f)) for f in u[:2] + u[4:]])]
        combined = (p + q, parts[0][1] + [tuple(i + len(p) for i in f) for f in parts[1][1]])
        results = [solver.remesh(meshes, 24, 1, mode) for meshes in (parts, [combined]) for mode in ("cpp", "python")]
        for result in results:
            self.assertEqual(result.closed_borders, 2)
            self.assertEqual(components(result), 2)
            self.assertAlmostEqual(volume(result), 16, delta=1)
            solver.validate_result(result)
            self.assertEqual(result.quads, results[0].quads)
            self.assertLess(
                max(abs(a - b) for p, q in zip(result.points, results[0].points) for a, b in zip(p, q)), 1e-12
            )

    def test_concave_and_dense_circular_mouths(self):
        outlines = (
            [(-1, -1), (1, -1), (1, 0), (0, 0), (0, 1), (-1, 1)],
            [(math.cos(i * 2 * math.pi / 96), math.sin(i * 2 * math.pi / 96)) for i in range(96)],
        )
        for outline, expected in zip(outlines, (6, 2 * math.pi)):
            n = len(outline)
            points = [(x, y, z) for z in (-1, 1) for x, y in outline]
            faces = [
                face for i in range(n) for face in ((i, (i + 1) % n, (i + 1) % n + n), (i, (i + 1) % n + n, i + n))
            ]
            results = [solver.remesh([(points, faces)], 24, 1, mode) for mode in ("cpp", "python")]
            for result in results:
                self.assertEqual(result.closed_borders, 2)
                self.assertAlmostEqual(volume(result), expected, delta=0.25)
                solver.validate_result(result)
            self.assertEqual(results[0].quads, results[1].quads)
            self.assertLess(
                max(abs(a - b) for p, q in zip(results[0].points, results[1].points) for a, b in zip(p, q)), 1e-12
            )

    def test_curved_open_border_and_cancellation(self):
        p, t = sphere(3)
        faces = [f for f in t if sum(p[i][2] for i in f) / 3 < 0.65]
        for backend in ("cpp", "python"):
            result = solver.remesh([(p, faces)], 24, 1, backend)
            self.assertEqual(result.closed_borders, 1)
            self.assertGreater(volume(result), 3)
            self.assertLess(volume(result), 4.25)
            solver.validate_result(result)
            with self.assertRaises(solver.Cancelled):
                solver.remesh([(p, faces)], 24, 1, backend, lambda fraction, message: "Closing open" not in message)

    def test_open_annular_rim_preserves_the_tunnel(self):
        n = 48
        points = [
            (r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
            for z, r in ((-1, 1), (1, 1), (-1, 0.65), (1, 0.65))
            for i in range(n)
        ]
        faces = []
        for i in range(n):
            j = (i + 1) % n
            faces.extend(
                (
                    (i, j, n + j),
                    (i, n + j, n + i),
                    (2 * n + i, 3 * n + j, 2 * n + j),
                    (2 * n + i, 3 * n + i, 3 * n + j),
                    (i, 2 * n + i, 2 * n + j),
                    (i, 2 * n + j, j),
                )
            )
        results = [solver.remesh([(points, faces)], 24, 1, mode) for mode in ("cpp", "python")]
        for result in results:
            self.assertEqual(result.closed_borders, 2)
            self.assertGreater(min(math.hypot(p[0], p[1]) for p in result.points), 0.6)
            solver.validate_result(result)
        self.assertEqual(results[0].quads, results[1].quads)
        self.assertEqual(len(results[0].points), len(results[1].points))
        self.assertLess(
            max(abs(a - b) for p, q in zip(results[0].points, results[1].points) for a, b in zip(p, q)), 1e-12
        )

    def test_triangle_distance_regions_and_bvh_search(self):
        triangle = ((0, 0, 0), (2, 0, 0), (0, 2, 0))
        for p, expected in (((0.5, 0.5, 3), 9), ((-1, -1, 1), 3), ((2, 2, 0), 2), ((3, 0, 0), 1)):
            self.assertAlmostEqual(triangle_distance_squared(p, *triangle), expected)
        p, t = sphere(2)
        tree = TriangleBVH(array("d", (v for point in p for v in point)), array("i", (i for face in t for i in face)))
        randomizer = random.Random(46)
        for _ in range(30):
            query = tuple(randomizer.uniform(-2, 2) for k in range(3))
            expected = math.sqrt(min(triangle_distance_squared(query, *(p[i] for i in face)) for face in t))
            self.assertAlmostEqual(tree.distance(query), expected, places=12)

    def test_smooth_surface_has_no_voxel_step_offset(self):
        mesh = sphere(4)
        for backend in ("python", "cpp"):
            for polish in (0, 1, 5):
                result = solver.remesh([mesh], 24, polish, backend)
                radial = [abs(math.sqrt(sum(v * v for v in p)) - 1) for p in result.points]
                self.assertLess(math.sqrt(sum(v * v for v in radial) / len(radial)), result.spacing * 0.085)
                self.assertLess(max(radial), result.spacing * 0.18)
                self.assertEqual(components(result), 1)
        for backend in ("cpp", "python"):
            result = solver.remesh([cube()], 24, 1, backend)
            broad_face = [p for p in result.points if abs(p[0]) < 0.6 and abs(p[1]) < 0.6 and p[2] > 0.8]
            self.assertTrue(broad_face)
            self.assertLess(max(abs(p[2] - 1) for p in broad_face), 1e-7)

    def test_cube_manifold_quads_outward_and_resolution(self):
        coarse = solver.remesh([cube()], 16, 1, "python")
        fine = solver.remesh([cube()], 32, 1, "python")
        self.assertTrue(all(len(f) == 4 for f in fine.quads))
        self.assertEqual(components(fine), 1)
        self.assertGreater(len(fine.quads), len(coarse.quads) * 3)
        self.assertAlmostEqual(volume(fine), 8, delta=0.15)
        self.assertEqual(fine.spacing, coarse.spacing / 2)

    def test_overlapping_separate_and_nested_solid_union(self):
        overlap = solver.remesh([cube(), cube((0.7, 0.3, 0.5))], 24, 0, "python")
        self.assertEqual(components(overlap), 1)
        self.assertGreater(volume(overlap), 8)
        separated = solver.remesh([cube((-2, 0, 0)), cube((2, 0, 0))], 24, 0, "python")
        self.assertEqual(components(separated), 2)
        nested = solver.remesh([cube(), cube(scale=(1, 1, 1))], 16, 0, "python")
        plain = solver.remesh([cube()], 16, 0, "python")
        self.assertEqual(nested.points, plain.points)
        self.assertEqual(nested.quads, plain.quads)

    def test_shells_in_one_mesh_union_even_when_winding_is_reversed(self):
        p, t = cube()
        q, u = cube((0.6, 0.4, 0.2))
        combined = (p + q, t + [tuple(v + len(p) for v in reversed(f)) for f in u])
        result = solver.remesh([combined], 24, 1, "python")
        reference = solver.remesh([(p, t), (q, u)], 24, 1, "python")
        self.assertEqual(result.points, reference.points)
        self.assertEqual(result.quads, reference.quads)

    def test_sphere_surface_error_and_polish(self):
        mesh = sphere()
        result = solver.remesh([mesh], 32, 2, "python")
        error = max(abs(math.sqrt(sum(v * v for v in p)) - 1) for p in result.points)
        self.assertLess(error, result.spacing * 1.6)
        self.assertAlmostEqual(volume(result), 4 * math.pi / 3, delta=0.25)
        self.assertEqual(components(result), 1)

    def test_cpp_parity_on_random_solids_and_every_polish_setting(self):
        randomizer = random.Random(734)
        for i in range(6):
            meshes = [
                cube(angle=randomizer.uniform(0, 1)),
                cube(
                    tuple(randomizer.uniform(-1.5, 1.5) for _ in range(3)),
                    tuple(randomizer.uniform(0.6, 2) for _ in range(3)),
                    randomizer.uniform(0, 1),
                ),
            ]
            python = solver.remesh(meshes, 16, i, "python")
            cpp = solver.remesh(meshes, 16, i, "cpp")
            self.assertEqual(python.quads, cpp.quads)
            for p, q in zip(python.points, cpp.points):
                for a, b in zip(p, q):
                    self.assertAlmostEqual(a, b, places=12)
            self.assertEqual(cpp.backend, "C++")
        python = solver.remesh([sphere(2)], 24, 10, "python")
        cpp = solver.remesh([sphere(2)], 24, 10, "cpp")
        self.assertEqual(python.quads, cpp.quads)
        self.assertLess(max(abs(a - b) for p, q in zip(python.points, cpp.points) for a, b in zip(p, q)), 1e-12)

    def test_translation_and_scale_invariance(self):
        reference = solver.remesh([cube()], 16, 1, "cpp")
        translated = solver.remesh([cube((123, -42, 72), (10, 10, 10))], 16, 1, "cpp")
        self.assertEqual(reference.quads, translated.quads)
        for p, q in zip(reference.points, translated.points):
            for k in range(3):
                self.assertAlmostEqual(q[k], p[k] * 5 + (123, -42, 72)[k], places=10)

    def test_invalid_inputs_and_memory_budget(self):
        p, t = cube()
        fixtures = (
            [],
            [(p, t + [t[0]])],
            [(p, t + [(0, 0, 1)])],
            [(p, [(0, 1, 99)])],
            [([(math.nan, 0, 0)] + p[1:], t)],
            [(p, [tuple(reversed(t[0]))] + t[1:])],
        )
        for meshes in fixtures:
            for backend in ("cpp", "python"):
                with self.assertRaises(ValueError):
                    solver.remesh(meshes, 16, 0, backend)
        for value in (0, 15, 513, 32.5, True):
            with self.assertRaises(ValueError):
                solver.remesh([cube()], value, 0, "python")
        with self.assertRaisesRegex(ValueError, "budget"):
            solver.remesh([cube()], 512, 0, "python")
        with self.assertRaises(ValueError):
            solver.remesh([cube()], 16, -1, "python")
        with self.assertRaisesRegex(ValueError, "budget"):
            solver.remesh([cube()], 512, 0, "cpp")

    def test_cancel_and_callback_failure_cpp_and_python(self):
        for backend in ("cpp", "python"):
            for stop_at in (0.02, 0.4, 0.55, 0.8, 0.92):
                with self.assertRaises(solver.Cancelled):
                    solver.remesh([cube()], 16, 1, backend, lambda fraction, message: fraction < stop_at)
        for backend in ("cpp", "auto"):

            def broken_progress(fraction, message):
                if fraction >= 0.96:
                    raise RuntimeError("Progress callback failed")

            with (
                patch.object(native, "require_python_fallback") as gate,
                patch.object(native, "python_solve") as python,
            ):
                with self.assertRaisesRegex(RuntimeError, "Progress callback failed"):
                    solver.remesh([cube()], 16, 1, backend, broken_progress)
                gate.assert_not_called()
                python.assert_not_called()

    def test_missing_backend_reports_automatic_fallback_and_strict_cpp_stops(self):
        kernel = native.kernel
        with (
            patch.object(kernel, "loaded", None),
            patch.object(kernel, "error", None),
            patch.object(kernel, "preload", side_effect=RuntimeError("Compiler unavailable")),
            patch("maya_volume_remesh._native.standalone.require_python_fallback") as gate,
        ):
            result = solver.remesh([cube()], 16, 0, "auto")
            self.assertEqual(result.backend, "Python")
            gate.assert_called_once()
            self.assertIn("Compiler unavailable", gate.call_args[0][1])
            with self.assertRaisesRegex(RuntimeError, "Compiler unavailable"):
                solver.remesh([cube()], 16, 0, "cpp")
            self.assertEqual(gate.call_count, 1)
        from maya_volume_remesh._native.fallback import FallbackCancelled

        with (
            patch.object(kernel, "loaded", None),
            patch.object(kernel, "error", "No compiler"),
            patch("maya_volume_remesh._native.standalone.require_python_fallback", side_effect=FallbackCancelled()),
            patch.object(native, "python_solve") as python,
        ):
            with self.assertRaises(FallbackCancelled):
                solver.remesh([cube()], 16, 0, "auto")
            python.assert_not_called()

    def test_explicit_python_override_skips_cpp_and_gate(self):
        with (
            patch.dict(os.environ, MAYA_VOLUME_REMESH_BACKEND="python"),
            patch.object(native.kernel, "preload") as preload,
            patch("maya_volume_remesh._native.standalone.require_python_fallback") as gate,
        ):
            result = solver.remesh([cube()], 16, 0, "auto")
            self.assertEqual(result.backend, "Python")
            preload.assert_not_called()
            gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
