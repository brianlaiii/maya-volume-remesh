"""C++ input checks, solid solver, output checks, and explicit Python fallback."""

from array import array
import ctypes as ct
from pathlib import Path
import time

from maya_volume_remesh._native.standalone import Kernel
from maya_volume_remesh._native.fallback import failure_details, require_python_fallback
from .solver import Cancelled, IDENTITY, RawMesh, Result, prepare, python_solve, report, validate_result


D = ct.POINTER(ct.c_double)
INT_POINTER = ct.POINTER(ct.c_int)
CALLBACK = ct.CFUNCTYPE(ct.c_int, ct.c_double, ct.c_char_p)


def _configure(lib):
    lib.bt_dynamesh_version.restype = ct.c_int
    if lib.bt_dynamesh_version() != 4:
        raise RuntimeError("DynaMesh C++ ABI mismatch.")
    lib.bt_dynamesh_remesh.argtypes = [
        D,
        ct.c_int,
        INT_POINTER,
        ct.c_int,
        INT_POINTER,
        INT_POINTER,
        ct.c_int,
        D,
        ct.c_int,
        ct.c_int,
        CALLBACK,
        ct.POINTER(ct.c_void_p),
        ct.c_char_p,
        ct.c_int,
    ]
    lib.bt_dynamesh_remesh.restype = ct.c_int
    lib.bt_dynamesh_info.argtypes = [ct.c_void_p, D, INT_POINTER, INT_POINTER]
    lib.bt_dynamesh_info.restype = ct.c_int
    lib.bt_dynamesh_count.argtypes = [ct.c_void_p, ct.c_int]
    lib.bt_dynamesh_count.restype = ct.c_int
    lib.bt_dynamesh_copy.argtypes = [ct.c_void_p, D, ct.c_int, INT_POINTER, ct.c_int]
    lib.bt_dynamesh_copy.restype = ct.c_int
    lib.bt_dynamesh_free.argtypes = [ct.c_void_p]
    lib.bt_dynamesh_free.restype = None


kernel = Kernel(
    Path(__file__).resolve().parents[1] / "MESH_DynaMesh_CPP" / "dynamesh.cpp", "MAYA_VOLUME_REMESH_BACKEND", _configure
)


def _pack(meshes, progress):
    """Copy query buffers; leave connectivity, transforms, and bounds to C++."""
    if not meshes:
        raise ValueError("Select at least one polygon mesh.")
    points, triangles, matrices = array("d"), array("i"), array("d")
    vertex_offsets, triangle_offsets = array("i", [0]), array("i", [0])
    for mesh in meshes:
        report(progress, 0, "C++: copying source buffers")
        if isinstance(mesh, RawMesh):
            if (
                mesh.points.typecode != "d"
                or mesh.triangles.typecode != "i"
                or len(mesh.points) % 3
                or len(mesh.triangles) % 3
                or len(mesh.matrix) != 16
            ):
                raise ValueError("Invalid packed source data.")
            points.extend(mesh.points)
            triangles.extend(mesh.triangles)
            matrices.extend(mesh.matrix)
        else:
            vertices, faces = mesh
            # Only the Python input protocol is checked here. Maya supplies
            # typed RawMesh arrays and does not run these per-face Python loops.
            start = len(points)
            for i, point in enumerate(vertices):
                if i % 8192 == 0:
                    report(progress, 0, "C++: copying source points")
                if len(point) < 3:
                    raise ValueError("Source mesh has empty or invalid points.")
                points.extend(point[:3])
            if len(points) == start:
                raise ValueError("Source mesh has empty or invalid points.")
            start = len(triangles)
            for i, face in enumerate(faces):
                if i % 8192 == 0:
                    report(progress, 0, "C++: copying source triangles")
                if len(face) != 3 or any(isinstance(v, bool) or int(v) != v for v in face):
                    raise ValueError("The solver needs triangle query data.")
                try:
                    triangles.extend(int(v) for v in face)
                except OverflowError as exc:
                    raise ValueError("Source mesh has invalid triangle indices.") from exc
            if len(triangles) == start:
                raise ValueError("Source mesh has no triangles.")
            matrices.extend(IDENTITY)
        vertex_offsets.append(len(points) // 3)
        triangle_offsets.append(len(triangles) // 3)
    return points, triangles, vertex_offsets, triangle_offsets, matrices


def _python(meshes, resolution, smoothing, progress):
    report(progress, 0, "Python: checking source shells")
    return validate_result(python_solve(prepare(meshes, resolution, progress), smoothing, progress), progress)


def solve(meshes, resolution, smoothing, backend, progress):
    # Explicit C++ is strict regardless of environment diagnostic overrides.
    report(progress, 0, "C++: preparing backend (first build can take a moment)")
    start = time.perf_counter()
    lib = kernel.preload() if backend == "cpp" else kernel.library()
    timings = {"backend_load": time.perf_counter() - start}
    if lib is None:
        return _python(meshes, resolution, smoothing, progress)
    start = time.perf_counter()
    points, triangles, vertex_offsets, triangle_offsets, matrices = _pack(meshes, progress)
    timings["buffer_copy"] = time.perf_counter() - start
    pending = []

    @CALLBACK
    def callback(fraction, message):
        try:
            report(progress, fraction, "C++: " + message.decode("utf-8", "replace"))
            return 0
        except BaseException as exc:
            pending.append(exc)
            return 1

    point_buffer = (ct.c_double * len(points)).from_buffer(points)
    triangle_buffer = (ct.c_int * len(triangles)).from_buffer(triangles)
    vertex_buffer = (ct.c_int * len(vertex_offsets)).from_buffer(vertex_offsets)
    offset_buffer = (ct.c_int * len(triangle_offsets)).from_buffer(triangle_offsets)
    matrix_buffer = (ct.c_double * len(matrices)).from_buffer(matrices)
    handle, error = ct.c_void_p(), ct.create_string_buffer(4096)
    try:
        start = time.perf_counter()
        status = lib.bt_dynamesh_remesh(
            point_buffer,
            len(points) // 3,
            triangle_buffer,
            len(triangles) // 3,
            vertex_buffer,
            offset_buffer,
            len(meshes),
            matrix_buffer,
            resolution,
            smoothing,
            callback,
            ct.byref(handle),
            error,
            len(error),
        )
        timings["native_compute"] = time.perf_counter() - start
        if pending:
            raise pending[0]
        if status == 2:
            raise Cancelled("DynaMesh cancelled.")
        if status:
            # Geometry/budget errors are solver failures, not missing backends.
            raise ValueError(error.value.decode("utf-8", "replace"))
        callback(0.96, b"Reading validated output")
        if pending:
            raise pending[0]
        start = time.perf_counter()
        nv, nq = lib.bt_dynamesh_count(handle, 0), lib.bt_dynamesh_count(handle, 1)
        raw_points, raw_quads = (ct.c_double * (3 * nv))(), (ct.c_int * (4 * nq))()
        spacing, dimensions = ct.c_double(), (ct.c_int * 3)()
        closed_borders = ct.c_int()
        if lib.bt_dynamesh_info(
            handle, ct.byref(spacing), dimensions, ct.byref(closed_borders)
        ) or lib.bt_dynamesh_copy(handle, raw_points, nv, raw_quads, nq):
            raise RuntimeError("DynaMesh C++ output copy failed.")
        output_points = [tuple(raw_points[3 * i : 3 * i + 3]) for i in range(nv)]
        quads = [tuple(raw_quads[4 * i : 4 * i + 4]) for i in range(nq)]
        timings["output_copy"] = time.perf_counter() - start
        return Result(output_points, quads, spacing.value, tuple(dimensions), "C++", timings, closed_borders.value)
    except (OSError, RuntimeError) as exc:
        if backend == "cpp" or pending:
            raise
        require_python_fallback("DynaMesh", failure_details(exc))
        return _python(meshes, resolution, smoothing, progress)
    finally:
        if handle:
            lib.bt_dynamesh_free(handle)


if __name__ == "__main__":
    print(kernel.build())
