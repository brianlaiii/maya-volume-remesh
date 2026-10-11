"""Optional native OpenVDB. The independent Python solver does not import this module."""

import ctypes as ct
import os
import time

from .solver import Cancelled, Result, report
from .vdb_build import kernel


D = ct.POINTER(ct.c_double)
INT_POINTER = ct.POINTER(ct.c_int)
CALLBACK = ct.CFUNCTYPE(ct.c_int, ct.c_double, ct.c_char_p)


class Unavailable(RuntimeError):
    """An unavailable runtime can be retried with the built-in solver."""


def configure(lib):
    lib.mvr_vdb_version.restype = ct.c_int
    if lib.mvr_vdb_version() != 5:
        raise Unavailable("OpenVDB bridge ABI mismatch. Restart after rebuilding.")
    output = [ct.POINTER(ct.c_void_p), ct.c_char_p, ct.c_int]
    lib.mvr_vdb_remesh.argtypes = [
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
    ] + output
    lib.mvr_vdb_remesh.restype = ct.c_int
    lib.mvr_vdb_info.argtypes = [ct.c_void_p, D, INT_POINTER, INT_POINTER]
    lib.mvr_vdb_info.restype = ct.c_int
    lib.mvr_vdb_count.argtypes = [ct.c_void_p, ct.c_int]
    lib.mvr_vdb_count.restype = ct.c_int
    lib.mvr_vdb_copy.argtypes = [ct.c_void_p, D, ct.c_int, INT_POINTER, ct.c_int]
    lib.mvr_vdb_copy.restype = ct.c_int
    lib.mvr_vdb_free.argtypes = [ct.c_void_p]
    lib.mvr_vdb_free.restype = None


def enabled():
    value = os.environ.get("MAYA_VOLUME_REMESH_OPENVDB", "auto").lower()
    if value not in ("auto", "on", "off", "1", "0"):
        raise ValueError("MAYA_VOLUME_REMESH_OPENVDB must be auto, on, or off.")
    return value not in ("off", "0")


def solve(meshes, resolution, polish, progress=None):
    from .native import _pack

    report(progress, 0, "OpenVDB: preparing optional runtime")
    start = time.perf_counter()
    try:
        lib = kernel.preload()
    except (OSError, RuntimeError, AttributeError) as exc:
        raise Unavailable(str(exc)) from exc
    timings = {"backend_load": time.perf_counter() - start}
    start = time.perf_counter()
    points, triangles, vertices, faces, matrices = _pack(meshes, progress)
    timings["buffer_copy"] = time.perf_counter() - start
    pending = []

    @CALLBACK
    def callback(fraction, message):
        try:
            report(progress, fraction, message.decode("utf-8", "replace"))
            return 0
        except BaseException as exc:
            pending.append(exc)
            return 1

    buffers = [
        (ct.c_double * len(points)).from_buffer(points),
        (ct.c_int * len(triangles)).from_buffer(triangles),
        (ct.c_int * len(vertices)).from_buffer(vertices),
        (ct.c_int * len(faces)).from_buffer(faces),
        (ct.c_double * len(matrices)).from_buffer(matrices),
    ]
    handle, error = ct.c_void_p(), ct.create_string_buffer(4096)
    try:
        start = time.perf_counter()
        code = lib.mvr_vdb_remesh(
            buffers[0],
            len(points) // 3,
            buffers[1],
            len(triangles) // 3,
            buffers[2],
            buffers[3],
            len(meshes),
            buffers[4],
            resolution,
            polish,
            callback,
            ct.byref(handle),
            error,
            len(error),
        )
        timings["native_compute"] = time.perf_counter() - start
        if pending:
            raise pending[0]
        if code == 2:
            raise Cancelled("Remesh cancelled.")
        message = error.value.decode("utf-8", "replace")
        if code == 3:
            raise Unavailable(message)
        if code == 4:
            raise MemoryError(message)
        if code:
            raise ValueError(message)
        report(progress, 0.96, "OpenVDB: reading validated output")
        nv, nq = lib.mvr_vdb_count(handle, 0), lib.mvr_vdb_count(handle, 1)
        packed_points, packed_quads = (ct.c_double * (3 * nv))(), (ct.c_int * (4 * nq))()
        width, dims, closed = ct.c_double(), (ct.c_int * 3)(), ct.c_int()
        if lib.mvr_vdb_info(handle, ct.byref(width), dims, ct.byref(closed)) or lib.mvr_vdb_copy(
            handle, packed_points, nv, packed_quads, nq
        ):
            raise Unavailable("OpenVDB output copy failed.")
        return Result(
            [tuple(packed_points[3 * i : 3 * i + 3]) for i in range(nv)],
            [tuple(packed_quads[4 * i : 4 * i + 4]) for i in range(nq)],
            width.value,
            tuple(dims),
            "OpenVDB",
            timings,
            closed.value,
        )
    finally:
        if handle:
            lib.mvr_vdb_free(handle)
