"""Capture evaluated world geometry, then author one undoable native Maya mesh."""

from array import array
from pathlib import Path
import time
import uuid

from maya import cmds
import maya.api.OpenMaya as om

from .solver import RawMesh, remesh, report


_PENDING = {}
_LIVE_PENDING = {}


def selected_mesh_paths():
    selection = om.MGlobal.getActiveSelectionList()
    pending, found, seen = [], {}, set()
    for i in range(selection.length()):
        try:
            pending.append(selection.getDagPath(i))
        except (RuntimeError, TypeError):
            continue
    while pending:
        path = pending.pop()
        name = path.fullPathName()
        if name in seen:
            continue
        seen.add(name)
        if path.node().hasFn(om.MFn.kMesh):
            if not om.MFnDagNode(path).isIntermediateObject:
                found[name] = path
        elif path.node().hasFn(om.MFn.kTransform):
            for i in range(path.childCount()):
                child = om.MDagPath(path)
                child.push(path.child(i))
                pending.append(child)
    return [found[name] for name in sorted(found)]


def capture_mesh(path, progress=None, packed=False):
    """Read the visible smooth preview into temporary data, never scene nodes."""
    fn = om.MFnMesh(path)
    preview = om.MFnDependencyNode(path.node()).findPlug("displaySmoothMesh", False).asInt()
    if preview:
        options = fn.getSmoothMeshDisplayOptions()
        if fn.numPolygons * 4**options.divisions > 2_000_000:
            raise ValueError("Smooth preview exceeds two million faces. Lower the source preview level.")
        report(progress, 0, "Reading Smooth Mesh Preview")
        owner = om.MFnMeshData().create()
        smooth = fn.generateSmoothMesh(parent=owner, options=options)
        fn = om.MFnMesh(smooth)
    matrix = path.inclusiveMatrix()
    source_points = fn.getPoints(om.MSpace.kObject)
    _counts, indices = fn.getTriangles()
    if packed:
        # Avoid Python matrix multiplication, triangle tuples, and connectivity
        # dictionaries on dense preview meshes. Native preparation owns them.
        points = array("d", (v for p in source_points for v in (p.x, p.y, p.z)))
        return RawMesh(points, array("i", indices), tuple(matrix))
    points = [tuple(p * matrix)[:3] for p in source_points]
    triangles = [tuple(indices[j : j + 3]) for j in range(0, len(indices), 3)]
    return points, triangles


def create_from_selection(resolution=128, smoothing=1, backend="cpp", hide_sources=False, progress=None):
    """Legacy static-output API. The CodeHive entry creates a live node instead."""
    started = time.perf_counter()
    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Enable Maya Undo before creating DynaMesh.")
    paths = selected_mesh_paths()
    if not paths:
        raise ValueError("Select polygon meshes or groups first.")
    meshes, sources = [], []
    for i, path in enumerate(paths):
        report(progress, 0, "Reading mesh {}/{}".format(i + 1, len(paths)))
        if hide_sources:
            if path.isInstanced():
                raise ValueError("Turn off Hide sources for instanced meshes.")
            visibility = om.MFnDependencyNode(path.node()).findPlug("visibility", False)
            if visibility.isLocked or visibility.isDestination:
                raise ValueError("Turn off Hide sources for locked or connected visibility.")
        meshes.append(capture_mesh(path, progress, packed=True))
        sources.append(om.MObjectHandle(path.node()))
    captured = time.perf_counter()
    result = remesh(meshes, resolution, smoothing, backend, progress)
    solved = time.perf_counter()
    report(progress, 0.97, result.backend + ": creating DynaMesh")
    if any(not handle.isValid() or not handle.isAlive() for handle in sources):
        raise RuntimeError("A source mesh was removed during calculation.")
    plugin = str(Path(__file__).with_name("authoring_command.py"))
    if not cmds.pluginInfo(plugin, query=True, loaded=True):
        cmds.loadPlugin(plugin, quiet=True)
    token = uuid.uuid4().hex
    _PENDING[token] = (result, sources, bool(hide_sources))
    try:
        name = cmds.btCreateDynaMesh(token)
        if isinstance(name, (list, tuple)):
            name = name[0]
    finally:
        _PENDING.pop(token, None)
    # No cancellable callback after authoring: a late Cancel cannot report an
    # aborted action after the successful command entered Maya's undo queue.
    result.timings = result.timings or {}
    result.timings.update(
        source_read=captured - started, solve=solved - captured, maya_create=time.perf_counter() - solved
    )
    return name, result


def create_node_from_selection(
    resolution=128, polish=1, offset=0.0, useSmoothPreview=True, smoothNormals=True, enabled=True, backend="auto"
):
    """Create one connected generator and output mesh in one undoable command."""
    import math
    from .runtime import ensure_loaded

    if not cmds.undoInfo(query=True, state=True):
        raise ValueError("Enable Maya Undo before creating DynaMesh")
    paths = selected_mesh_paths()
    if not paths:
        raise ValueError("Select polygon meshes or groups first")
    if isinstance(resolution, bool) or int(resolution) != resolution or not 16 <= resolution <= 512:
        raise ValueError("Resolution must be a whole number from 16 to 512")
    if isinstance(polish, bool) or int(polish) != polish or not 0 <= polish <= 10:
        raise ValueError("Polish must be a whole number from 0 to 10")
    if not math.isfinite(offset):
        raise ValueError("Offset must be finite")
    # Exclude disconnected or empty mesh shapes before loading any runtime.
    for path in paths:
        fn = om.MFnMesh(path)
        if not fn.numVertices or not fn.numPolygons:
            raise ValueError("DynaMesh needs nonempty polygon meshes")
    ensure_loaded(backend)
    plugin = str(Path(__file__).with_name("authoring_command.py"))
    if not cmds.pluginInfo(plugin, query=True, loaded=True):
        cmds.loadPlugin(plugin, quiet=True)
    if "btCreateDynaMeshNode" not in (cmds.pluginInfo(plugin, query=True, command=True) or []):
        raise RuntimeError("DynaMesh authoring runtime changed. Save and restart Maya")
    token = uuid.uuid4().hex
    settings = dict(
        resolution=int(resolution),
        polish=int(polish),
        offset=float(offset),
        useSmoothPreview=bool(useSmoothPreview),
        smoothNormals=bool(smoothNormals),
        enabled=bool(enabled),
    )
    _LIVE_PENDING[token] = ([om.MDagPath(path) for path in paths], settings)
    try:
        result = cmds.btCreateDynaMeshNode(token)
    finally:
        _LIVE_PENDING.pop(token, None)
    node, mesh = result
    print(
        "[DynaMesh] {} -> {}: {} faces, {}".format(
            node, mesh, cmds.getAttr(node + ".faceCount"), cmds.getAttr(node + ".backend")
        )
    )
    return node, mesh
