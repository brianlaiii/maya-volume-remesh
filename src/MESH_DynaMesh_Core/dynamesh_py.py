"""Self-contained, installable API 2.0 Python fallback for btDynaMesh."""

from array import array
import hashlib
import importlib.util
import math
from pathlib import Path
import sys
import time
import types

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


_source = Path(maya_useNewAPI.__code__.co_filename).resolve()
_root = _source.parent
_paths = [_source] + [
    next((p for p in (_root / ("bt_dynamesh_" + name + ".py"), _root / (name + ".py")) if p.is_file()))
    for name in ("schema", "solver", "surface", "boundary")
]
_digest = hashlib.sha256(b"".join(p.read_bytes() for p in _paths)).hexdigest()
_package_name = "_bt_dynamesh_" + _digest
_package = types.ModuleType(_package_name)
_package.__path__ = [str(_root)]
sys.modules[_package_name] = _package
for _name in ("schema", "boundary", "surface", "solver"):
    _path = next(p for p in _paths if p.stem in (_name, "bt_dynamesh_" + _name))
    _spec = importlib.util.spec_from_file_location(_package_name + "." + _name, str(_path))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    setattr(_package, _name, _module)
schema, solver = _package.schema, _package.solver
VERSION = schema.VERSION + "+py." + _digest


def shifted(points, quads, offset):
    if not offset:
        return points
    normals = [[0.0, 0.0, 0.0] for _ in points]
    for face in quads:
        normal = [0.0, 0.0, 0.0]
        for a, b in zip(face, face[1:] + face[:1]):
            p, q = points[a], points[b]
            for k in range(3):
                normal[k] += p[(k + 1) % 3] * q[(k + 2) % 3] - p[(k + 2) % 3] * q[(k + 1) % 3]
        for vertex in face:
            for k in range(3):
                normals[vertex][k] += normal[k]
    result = []
    for p, n in zip(points, normals):
        length = math.sqrt(sum(v * v for v in n))
        result.append(tuple(p[k] + (offset * n[k] / length if length > 1e-20 else 0.0) for k in range(3)))
    if any(not math.isfinite(v) for p in result for v in p):
        raise ValueError("Offset produced nonfinite points")
    return result


class DynaMesh(om.MPxNode):
    attrs = {}

    def __init__(self):
        super().__init__()
        self._key = self._polish_key = None
        self._cache = {}
        self._result = None
        self._builds = 0

    def schedulingType(self):
        return om.MPxNode.kSerial

    def compute(self, plug, block):
        if plug.attribute() not in [self.attrs[n] for n in schema.OUTPUTS]:
            return om.kUnknownParameter
        started = time.perf_counter()
        owner = om.MFnMeshData().create()
        output = owner
        status, width, borders, backend = "Ready", 0.0, 0, "Python"
        meshes = []
        first = None
        try:
            enabled = block.inputValue(self.attrs["enabled"]).asBool()
            preview = block.inputValue(self.attrs["useSmoothPreview"]).asBool()
            inputs = block.inputArrayValue(self.attrs["input"])
            for physical in range(len(inputs)):
                inputs.jumpToPhysicalElement(physical)
                value = inputs.inputValue()
                mesh = value.child(self.attrs["inputMesh"]).asMesh()
                matrix = value.child(self.attrs["inputMatrix"]).asMatrix()
                if first is None and not mesh.isNull():
                    first = (mesh, matrix)
                if not enabled and first is not None:
                    break
                use_preview = enabled and preview and value.child(self.attrs["inputPreview"]).asInt() != 0
                fn = om.MFnMesh(mesh)
                if use_preview:
                    level = value.child(self.attrs["inputSmoothLevel"]).asInt()
                    if not 0 <= level <= 7 or fn.numPolygons * 4**level > 2_000_000:
                        raise ValueError("Smooth preview exceeds two million faces. Lower the source preview level")
                    mesh = value.child(self.attrs["inputSmoothMesh"]).asMesh()
                    fn = om.MFnMesh(mesh)
                if fn.numPolygons > 2_000_000:
                    raise ValueError("Input exceeds two million faces")
                if enabled:
                    points = array("d", (v for p in fn.getPoints() for v in (p.x, p.y, p.z)))
                    _counts, triangles = fn.getTriangles()
                    meshes.append(solver.RawMesh(points, array("i", triangles), tuple(matrix)))
            if first is None:
                raise ValueError("Connect at least one polygon mesh")
            if not enabled:
                status, backend = "Disabled; first input passed through", "Pass through"
                output = self.pass_through(first)
            else:
                resolution = block.inputValue(self.attrs["resolution"]).asInt()
                polish = block.inputValue(self.attrs["polish"]).asInt()
                if not 0 <= polish <= 10:
                    raise ValueError("Polish must be from 0 to 10")
                # Compare exact packed input; no collision-prone topology hash.
                key = (resolution, tuple((m.points.tobytes(), m.triangles.tobytes(), m.matrix) for m in meshes))
                if key != self._key:
                    cache = {}
                    solver.validate_result(solver.python_solve(solver.prepare(meshes, resolution), 0, cache=cache))
                    self._cache, self._key = cache, key
                    self._polish_key = None
                    self._builds += 1
                if polish != self._polish_key:
                    self._result = solver.polish_cached(self._cache, polish)
                    self._polish_key = polish
                result = self._result
                offset = block.inputValue(self.attrs["offset"]).asDistance().asCentimeters()
                if not math.isfinite(offset):
                    raise ValueError("Offset must be finite")
                points = shifted(result.points, result.quads, offset)
                fn = om.MFnMesh()
                fn.create(
                    om.MPointArray(points),
                    [4] * len(result.quads),
                    [i for face in result.quads for i in face],
                    parent=owner,
                )
                soft = block.inputValue(self.attrs["smoothNormals"]).asBool()
                fn.setEdgeSmoothings(list(range(fn.numEdges)), [soft] * fn.numEdges)
                fn.cleanupEdgeSmoothing()
                width, borders = result.spacing, result.closed_borders
        except (RuntimeError, ValueError, TypeError, OverflowError, IndexError) as exc:
            status, backend = "Invalid: " + str(exc) + ". First input passed through.", "Pass through"
            if first is not None:
                try:
                    output = self.pass_through(first)
                except RuntimeError:
                    output = om.MFnMeshData().create()
        try:
            fn = om.MFnMesh(output)
        except RuntimeError:
            fn = None
        values = (
            ("outputMesh", output, "setMObject"),
            ("status", status, "setString"),
            ("vertexCount", fn.numVertices if fn else 0, "setInt"),
            ("faceCount", fn.numPolygons if fn else 0, "setInt"),
            ("voxelWidth", om.MDistance(width), "setMDistance"),
            ("closedBorders", borders, "setInt"),
            ("backend", backend, "setString"),
            ("backendReason", "Python runtime" if backend == "Python" else status, "setString"),
            ("computeMilliseconds", (time.perf_counter() - started) * 1000, "setDouble"),
            ("volumeBuilds", self._builds, "setInt"),
        )
        for name, value, method in values:
            handle = block.outputValue(self.attrs[name])
            getattr(handle, method)(value)
            handle.setClean()

    @staticmethod
    def pass_through(first):
        source, matrix = first
        owner = om.MFnMeshData().create()
        fn = om.MFnMesh(om.MFnMesh().copy(source, owner))
        fn.setPoints(om.MPointArray([p * matrix for p in fn.getPoints()]))
        return owner


def initialize():
    cls = DynaMesh
    for name, kind, default, lo, hi, _tip in schema.PARAMETERS:
        fn = om.MFnUnitAttribute() if kind == "distance" else om.MFnNumericAttribute()
        data_type = (
            om.MFnUnitAttribute.kDistance
            if kind == "distance"
            else om.MFnNumericData.kBoolean
            if kind == "bool"
            else om.MFnNumericData.kInt
        )
        cls.attrs[name] = fn.create(name, name, data_type, default)
        if lo is not None:
            fn.setMin(lo)
        if hi is not None:
            fn.setMax(hi)
        fn.keyable = True
        cls.addAttribute(cls.attrs[name])
    for name in ("inputMesh", "inputSmoothMesh"):
        fn = om.MFnTypedAttribute()
        cls.attrs[name] = fn.create(name, name, om.MFnData.kMesh)
        fn.hidden = True
    fn = om.MFnMatrixAttribute()
    cls.attrs["inputMatrix"] = fn.create("inputMatrix", "inputMatrix")
    fn.hidden = True
    for name in ("inputPreview", "inputSmoothLevel"):
        fn = om.MFnNumericAttribute()
        cls.attrs[name] = fn.create(name, name, om.MFnNumericData.kInt, 0)
        fn.hidden = True
    fn = om.MFnCompoundAttribute()
    cls.attrs["input"] = fn.create("input", "input")
    for name in schema.INPUT_CHILDREN:
        fn.addChild(cls.attrs[name])
    fn.array = True
    fn.usesArrayDataBuilder = True
    fn.hidden = True
    cls.addAttribute(cls.attrs["input"])
    for name in schema.OUTPUTS:
        if name in ("outputMesh", "status", "backend", "backendReason"):
            fn = om.MFnTypedAttribute()
            cls.attrs[name] = fn.create(name, name, om.MFnData.kMesh if name == "outputMesh" else om.MFnData.kString)
        elif name == "voxelWidth":
            fn = om.MFnUnitAttribute()
            cls.attrs[name] = fn.create(name, name, om.MFnUnitAttribute.kDistance, 0)
        else:
            fn = om.MFnNumericAttribute()
            cls.attrs[name] = fn.create(
                name, name, om.MFnNumericData.kDouble if name == "computeMilliseconds" else om.MFnNumericData.kInt, 0
            )
        fn.writable = fn.storable = False
        fn.hidden = name in ("outputMesh", "backend", "backendReason", "computeMilliseconds", "volumeBuilds")
        fn.channelBox = not fn.hidden
        cls.addAttribute(cls.attrs[name])
    for name in tuple(schema.DEFAULTS) + ("input",) + schema.INPUT_CHILDREN:
        for output in schema.OUTPUTS:
            cls.attributeAffects(cls.attrs[name], cls.attrs[output])


def initializePlugin(plugin):
    import maya.mel as mel

    om.MFnPlugin(plugin, "Brian Lai", VERSION, "Any").registerNode(
        schema.NODE_NAME, om.MTypeId(schema.TYPE_ID), DynaMesh, initialize
    )
    mel.eval(schema.ae_template())


def uninitializePlugin(plugin):
    om.MFnPlugin(plugin).deregisterNode(om.MTypeId(schema.TYPE_ID))
