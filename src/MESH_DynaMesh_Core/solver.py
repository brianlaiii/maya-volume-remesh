"""SDK-free solid union and quad surface nets. No third-party dependencies.

Resolution is the number of voxels across the longest world-space bound.
Open borders are capped in temporary data. Each shell is a solid.
Nested shells are filled.
Tiny diagonal voxel contacts are closed before meshing; detail below one
voxel can disappear. UVs, materials and source edge flow are not transferred.
"""

from array import array
from collections import defaultdict
from dataclasses import dataclass
import math


MAX_SAMPLES = 32_000_000
MAX_EVENTS = 16_000_000
MAX_SURFACE = 500_000
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
EDGES = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))
FACES = ((0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5))


class Cancelled(Exception):
    """The user stopped calculation before scene authoring."""


def report(progress, fraction, message):
    if progress and progress(fraction, message) is False:
        raise Cancelled("DynaMesh cancelled.")


@dataclass
class Prepared:
    points: array
    triangles: array
    offsets: array
    origin: tuple
    spacing: float
    dimensions: tuple
    closed_borders: int = 0


@dataclass
class RawMesh:
    """Packed object-space Maya query data; C++ applies the instance matrix."""

    points: array
    triangles: array
    matrix: tuple = IDENTITY

    def python_mesh(self):
        if len(self.points) % 3 or len(self.triangles) % 3 or len(self.matrix) != 16:
            raise ValueError("Invalid packed source data.")
        m = self.matrix
        if m[3] != 0 or m[7] != 0 or m[11] != 0 or m[15] != 1:
            raise ValueError("Source world matrices must be affine.")
        points = [
            tuple(
                self.points[i] * m[k] + self.points[i + 1] * m[4 + k] + self.points[i + 2] * m[8 + k] + m[12 + k]
                for k in range(3)
            )
            for i in range(0, len(self.points), 3)
        ]
        triangles = [tuple(self.triangles[i : i + 3]) for i in range(0, len(self.triangles), 3)]
        return points, triangles


@dataclass
class Result:
    points: list
    quads: list
    spacing: float
    dimensions: tuple
    backend: str
    timings: dict = None
    closed_borders: int = 0


def prepare(meshes, resolution=128, progress=None):
    """Close open borders, validate shells, and pack normalized geometry."""
    if isinstance(resolution, bool) or int(resolution) != resolution or not 16 <= resolution <= 512:
        raise ValueError("Resolution must be a whole number from 16 to 512.")
    if not meshes:
        raise ValueError("Select at least one polygon mesh.")
    points, triangles, offsets = array("d"), array("i"), array("i", [0])
    closed_borders = 0
    lower, upper = [math.inf] * 3, [-math.inf] * 3
    for mesh_index, mesh in enumerate(meshes):
        vertices, faces = mesh.python_mesh() if isinstance(mesh, RawMesh) else mesh
        report(progress, 0.02 * mesh_index / len(meshes), "Checking source shells")
        vertices = [tuple(float(v) for v in p[:3]) for p in vertices]
        if not vertices or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in vertices):
            raise ValueError("Source mesh has empty or invalid points.")
        if not faces:
            raise ValueError("Source mesh has no triangles.")
        parents = list(range(len(vertices)))

        def root(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i

        edges = {}
        clean_faces = []
        used = set()
        for index, face in enumerate(faces):
            if index % 8192 == 0:
                report(progress, 0.01, "Checking source connectivity")
            if len(face) != 3 or any(isinstance(v, bool) or int(v) != v for v in face):
                raise ValueError("The solver needs triangle query data.")
            a, b, c = map(int, face)
            if min(a, b, c) < 0 or max(a, b, c) >= len(vertices) or len({a, b, c}) != 3:
                raise ValueError("Source mesh has invalid triangle indices.")
            ab = [vertices[b][k] - vertices[a][k] for k in range(3)]
            ac = [vertices[c][k] - vertices[a][k] for k in range(3)]
            cross = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
            if not any(v != 0 for v in cross):
                raise ValueError("Source mesh has a zero-area triangle.")
            for u, v in ((a, b), (b, c), (c, a)):
                key = (min(u, v), max(u, v))
                count, winding = edges.get(key, (0, 0))
                edges[key] = (count + 1, winding + (1 if u < v else -1))
                ru, rv = root(u), root(v)
                if ru != rv:
                    parents[rv] = ru
            clean_faces.append((a, b, c))
            used.update((a, b, c))
        from .boundary import close_borders

        caps, closed = close_borders(vertices, edges, lambda: report(progress, 0.012, "Closing open source borders"))
        if caps:
            parents.extend(range(len(parents), len(vertices)))
            for face in caps:
                for u, v in zip(face, face[1:] + face[:1]):
                    key = (min(u, v), max(u, v))
                    count, winding = edges.get(key, (0, 0))
                    edges[key] = (count + 1, winding + (1 if u < v else -1))
                    ru, rv = root(u), root(v)
                    if ru != rv:
                        parents[rv] = ru
                used.update(face)
            clean_faces.extend(caps)
            if any(count != 2 or winding != 0 for count, winding in edges.values()):
                raise ValueError("Automatic closure did not produce a manifold source.")
        closed_borders += closed
        shells = defaultdict(list)
        for face in clean_faces:
            shells[root(face[0])].append(face)
        base = len(points) // 3
        points.extend(v for p in vertices for v in p)
        for vertex in used:
            for k in range(3):
                lower[k] = min(lower[k], vertices[vertex][k])
                upper[k] = max(upper[k], vertices[vertex][k])
        for shell in shells.values():
            triangles.extend(base + v for face in shell for v in face)
            offsets.append(len(triangles) // 3)
    spacing = max(upper[k] - lower[k] for k in range(3)) / int(resolution)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("Source bounds have no usable volume.")
    dimensions = tuple(math.ceil((upper[k] - lower[k]) / spacing) + 5 for k in range(3))
    if math.prod(dimensions) > MAX_SAMPLES:
        raise ValueError("This resolution exceeds the 32 million sample budget. Lower Resolution.")
    # Half-cell bounds avoid collapsed dual quads on axis-aligned source faces.
    origin = tuple(lower[k] - 2.5 * spacing for k in range(3))
    for i in range(len(points)):
        points[i] = (points[i] - origin[i % 3]) / spacing
    report(progress, 0.02, "Source shells checked")
    return Prepared(points, triangles, offsets, origin, spacing, dimensions, closed_borders)


def _bad_mask(mask):
    if mask in (0, 255):
        return False
    if sum(((mask >> a) ^ (mask >> b)) & 1 for a, b in EDGES) > 6:
        return True
    for face in FACES:
        bits = [(mask >> v) & 1 for v in face]
        if bits[0] == bits[2] and bits[1] == bits[3] and bits[0] != bits[1]:
            return True
    for value in (0, 1):
        pending = {i for i in range(8) if ((mask >> i) & 1) == value}
        stack = [pending.pop()]
        while stack:
            i = stack.pop()
            for j in (i ^ 1, i ^ 2, i ^ 4):
                if j in pending:
                    pending.remove(j)
                    stack.append(j)
        if pending:
            return True
    return False


BAD_MASKS = tuple(_bad_mask(i) for i in range(256))


def _edge(a, b, x, y):
    return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])


def _owned(a, b, value):
    if abs(value) > 1e-10:
        return value > 0
    return b[1] > a[1] or (b[1] == a[1] and b[0] < a[0])


def _rasterize(data, progress):
    nx, ny, nz = data.dimensions
    occupied = bytearray(nx * ny * nz)
    total_triangles = len(data.triangles) // 3
    event_count = 0
    for first, last in zip(data.offsets, data.offsets[1:]):
        rows = defaultdict(list)
        for t in range(first, last):
            if t % 256 == 0:
                report(progress, 0.02 + 0.38 * t / total_triangles, "Voxelizing solid union")
            a, b, c = [data.points[3 * i : 3 * i + 3] for i in data.triangles[3 * t : 3 * t + 3]]
            area = _edge(a, b, c[0], c[1])
            if abs(area) < 1e-14:
                continue
            if area < 0:
                b, c = c, b
                area = -area
            for x in range(
                max(0, math.ceil(min(a[0], b[0], c[0]))), min(nx - 1, math.floor(max(a[0], b[0], c[0]))) + 1
            ):
                if x % 32 == 0:
                    report(progress, 0.02 + 0.38 * t / total_triangles, "Voxelizing solid union")
                for y in range(
                    max(0, math.ceil(min(a[1], b[1], c[1]))), min(ny - 1, math.floor(max(a[1], b[1], c[1]))) + 1
                ):
                    e0, e1, e2 = _edge(a, b, x, y), _edge(b, c, x, y), _edge(c, a, x, y)
                    if _owned(a, b, e0) and _owned(b, c, e1) and _owned(c, a, e2):
                        rows[x * ny + y].append((e1 * a[2] + e2 * b[2] + e0 * c[2]) / area)
                        event_count += 1
                        if event_count > MAX_EVENTS:
                            raise ValueError("Too many surface crossings. Lower Resolution or simplify the source.")
        for index, (row, hits) in enumerate(rows.items()):
            if index % 256 == 0:
                report(progress, 0.02 + 0.38 * last / total_triangles, "Filling solid samples")
            hits.sort()
            if len(hits) % 2:
                raise ValueError("Source cannot be sampled as a closed solid. Check overlapping or invalid faces.")
            for lo, hi in zip(hits[::2], hits[1::2]):
                begin, end = max(0, math.ceil(lo)), min(nz, math.ceil(hi))
                if end > begin:
                    occupied[row * nz + begin : row * nz + end] = b"\1" * (end - begin)
    if not any(occupied):
        raise ValueError("No enclosed volume survived sampling or automatic closure.")
    return occupied


def _regularize(occupied, dimensions, progress):
    nx, ny, nz = dimensions
    sx, sy = ny * nz, nz
    corners = (0, sx, sy, sx + sy, 1, sx + 1, sy + 1, sx + sy + 1)
    # Monotone filling closes sub-voxel checkerboards and vertex-only contacts.
    for iteration in range(8):
        changed = False
        for x in range(nx - 1):
            report(progress, 0.4 + 0.15 * x / (nx - 1), "Closing voxel contacts")
            for y in range(ny - 1):
                row = x * sx + y * sy
                for z in range(nz - 1):
                    i = row + z
                    mask = sum(occupied[i + offset] << bit for bit, offset in enumerate(corners))
                    if BAD_MASKS[mask]:
                        for offset in corners:
                            occupied[i + offset] = 1
                        changed = True
        if not changed:
            return corners
    raise ValueError("Voxel contacts remain ambiguous. Increase Resolution or separate touching parts.")


def python_solve(data, smoothing=1, progress=None, cache=None):
    occupied = _rasterize(data, progress)
    nx, ny, nz = data.dimensions
    sx, sy = ny * nz, nz
    corners = _regularize(occupied, data.dimensions, progress)
    from .surface import SurfaceField

    field = SurfaceField(data, occupied, lambda: report(progress, 0.55, "Measuring source surface"))
    points, cells, vertex_ids = [], [], {}
    for x in range(nx - 1):
        report(progress, 0.55 + 0.22 * x / (nx - 1), "Building quad surface")
        for y in range(ny - 1):
            row = x * sx + y * sy
            for z in range(nz - 1):
                i = row + z
                mask = sum(occupied[i + offset] << bit for bit, offset in enumerate(corners))
                if mask in (0, 255):
                    continue
                crossed = [(a, b) for a, b in EDGES if ((mask >> a) ^ (mask >> b)) & 1]
                p = [0.0, 0.0, 0.0]
                for a, b in crossed:
                    va, vb = abs(field.sample(i + corners[a])), abs(field.sample(i + corners[b]))
                    t = va / (va + vb)
                    for k in range(3):
                        ca, cb = (a >> k) & 1, (b >> k) & 1
                        p[k] += ca + t * (cb - ca)
                p = [v / len(crossed) for v in p]
                vertex_ids[i] = len(points)
                cells.append((x, y, z))
                points.append(field.project(tuple((x, y, z)[k] + p[k] for k in range(3)), (x, y, z)))
                if len(points) > MAX_SURFACE:
                    raise ValueError("Surface exceeds the 500,000 vertex budget. Lower Resolution.")
    quads = []
    for index, (i, vertex_id) in enumerate(vertex_ids.items()):
        if index % 8192 == 0:
            report(progress, 0.77, "Connecting quads")
        for stride, offsets in ((sx, (0, -sy, -sy - 1, -1)), (sy, (0, -1, -sx - 1, -sx)), (1, (0, -sx, -sx - sy, -sy))):
            if occupied[i] == occupied[i + stride]:
                continue
            keys = [i + offset for offset in offsets]
            if not all(key in vertex_ids for key in keys):
                raise ValueError("Surface reached the sampling boundary. Increase Resolution.")
            quad = tuple(vertex_ids[key] for key in keys)
            quads.append(quad if occupied[i] else tuple(reversed(quad)))
            if len(quads) > MAX_SURFACE:
                raise ValueError("Surface exceeds the 500,000 quad budget. Lower Resolution.")
    if cache is not None:
        cache.update(data=data, points=points, quads=quads, cells=cells, field=field)
    points = _relax(points, quads, cells, smoothing, progress, field)
    points = [tuple(data.origin[k] + p[k] * data.spacing for k in range(3)) for p in points]
    return Result(points, quads, data.spacing, data.dimensions, "Python", closed_borders=data.closed_borders)


def polish_cached(cache, smoothing):
    """Refit cached normalized vertices without rebuilding or resampling solids."""
    data = cache["data"]
    points = _relax(cache["points"], cache["quads"], cache["cells"], smoothing, None, cache["field"])
    points = [tuple(data.origin[k] + p[k] * data.spacing for k in range(3)) for p in points]
    return Result(points, cache["quads"], data.spacing, data.dimensions, "Python", closed_borders=data.closed_borders)


def _relax(points, quads, cells, iterations, progress, field):
    if not iterations:
        return points
    neighbors = [set() for _ in points]
    for face in quads:
        for a, b in zip(face, face[1:] + face[:1]):
            neighbors[a].add(b)
            neighbors[b].add(a)
    # Ordered sums keep the native and scalar implementations reproducible.
    neighbors = [sorted(row) for row in neighbors]
    for iteration in range(iterations):
        for weight in (0.45, -0.47):
            report(progress, 0.8 + 0.1 * iteration / max(1, iterations), "Polishing quad surface")
            output = []
            for i, p in enumerate(points):
                if i % 8192 == 0:
                    report(progress, 0.8 + 0.1 * iteration / max(1, iterations), "Polishing quad surface")
                row = neighbors[i]
                output.append(
                    tuple(
                        min(
                            cells[i][k] + 1 - 1e-4,
                            max(cells[i][k] + 1e-4, p[k] + weight * (sum(points[j][k] for j in row) / len(row) - p[k])),
                        )
                        for k in range(3)
                    )
                )
            points = output
        projected = []
        for i, p in enumerate(points):
            if i % 2048 == 0:
                report(progress, 0.8 + 0.1 * iteration / max(1, iterations), "Fitting smooth surface")
            projected.append(field.project(p, cells[i]))
        points = projected
    return points


def validate_result(result, progress=None):
    """Reject open/nonmanifold output and disconnected vertex fans before edits."""
    if not result.points or not result.quads:
        raise ValueError("No quad surface survived this resolution. Increase Resolution.")
    edges, fans = {}, [defaultdict(set) for _ in result.points]
    for index, face in enumerate(result.quads):
        if index % 8192 == 0:
            report(progress, 0.92, "Checking quad topology")
        if len(face) != 4 or len(set(face)) != 4 or min(face) < 0 or max(face) >= len(result.points):
            raise ValueError("Invalid quad output.")
        for a, b in zip(face, face[1:] + face[:1]):
            key = (min(a, b), max(a, b))
            count, winding = edges.get(key, (0, 0))
            edges[key] = (count + 1, winding + (1 if a < b else -1))
        for k, vertex in enumerate(face):
            a, b = face[k - 1], face[(k + 1) % 4]
            fans[vertex][a].add(b)
            fans[vertex][b].add(a)
    if any(count != 2 or winding != 0 for count, winding in edges.values()):
        raise ValueError("Remesh is not a closed manifold. Increase Resolution.")
    for index, fan in enumerate(fans):
        if index % 8192 == 0:
            report(progress, 0.94, "Checking vertex flow")
        if not 3 <= len(fan) <= 6 or any(len(row) != 2 for row in fan.values()):
            raise ValueError("Remesh has invalid vertex flow. Increase Resolution.")
        seen, stack = set(), [next(iter(fan))]
        while stack:
            i = stack.pop()
            if i not in seen:
                seen.add(i)
                stack.extend(fan[i] - seen)
        if len(seen) != len(fan):
            raise ValueError("Remesh has a disconnected vertex fan. Increase Resolution.")
    if not all(all(math.isfinite(v) for v in p) for p in result.points):
        raise ValueError("Remesh contains invalid points.")
    return result


def remesh(meshes, resolution=128, smoothing=1, backend="cpp", progress=None):
    """Compute and validate a mesh without importing Maya or editing a scene."""
    if isinstance(smoothing, bool) or int(smoothing) != smoothing or not 0 <= smoothing <= 10:
        raise ValueError("Polish must be a whole number from 0 to 10.")
    if backend not in ("auto", "cpp", "python"):
        raise ValueError("Backend must be auto, cpp, or python.")
    if isinstance(resolution, bool) or int(resolution) != resolution or not 16 <= resolution <= 512:
        raise ValueError("Resolution must be a whole number from 16 to 512.")
    if backend == "python":
        report(progress, 0, "Python: checking source shells")
        return validate_result(python_solve(prepare(meshes, resolution, progress), smoothing, progress), progress)
    from .native import solve

    return solve(meshes, int(resolution), int(smoothing), backend, progress)
