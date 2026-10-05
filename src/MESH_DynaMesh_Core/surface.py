"""Closest-triangle BVH and a continuous field around the sampled solid union."""

import math


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def triangle_distance_squared(p, a, b, c):
    """Exact closest-point regions, including edge and vertex regions."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return _dot(ap, ap)
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return _dot(bp, bp)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        delta = tuple(ap[k] - v * ab[k] for k in range(3))
        return _dot(delta, delta)
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return _dot(cp, cp)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        delta = tuple(ap[k] - w * ac[k] for k in range(3))
        return _dot(delta, delta)
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        delta = tuple(bp[k] - w * (c[k] - b[k]) for k in range(3))
        return _dot(delta, delta)
    denominator = va + vb + vc
    v, w = vb / denominator, vc / denominator
    delta = tuple(ap[k] - v * ab[k] - w * ac[k] for k in range(3))
    return _dot(delta, delta)


def _box_distance(p, lower, upper):
    return sum(max(lower[k] - p[k], 0, p[k] - upper[k]) ** 2 for k in range(3))


class TriangleBVH:
    def __init__(self, points, indices, progress=None):
        self.triangles = []
        self.bounds = []
        for index in range(0, len(indices), 3):
            if progress and index % 8192 == 0:
                progress()
            triangle = tuple(tuple(points[3 * v : 3 * v + 3]) for v in indices[index : index + 3])
            self.triangles.append(triangle)
            self.bounds.append(
                (
                    tuple(min(p[k] for p in triangle) for k in range(3)),
                    tuple(max(p[k] for p in triangle) for k in range(3)),
                )
            )
        self.nodes = []

        def build(ids):
            if progress:
                progress()
            lower = tuple(min(self.bounds[i][0][k] for i in ids) for k in range(3))
            upper = tuple(max(self.bounds[i][1][k] for i in ids) for k in range(3))
            node = len(self.nodes)
            self.nodes.append(None)
            if len(ids) <= 8:
                self.nodes[node] = (lower, upper, -1, -1, ids)
            else:
                axis = max(range(3), key=lambda k: upper[k] - lower[k])
                ids.sort(key=lambda i: (self.bounds[i][0][axis] + self.bounds[i][1][axis], i))
                middle = len(ids) // 2
                left, right = build(ids[:middle]), build(ids[middle:])
                self.nodes[node] = (lower, upper, left, right, None)
            return node

        build(list(range(len(self.triangles))))

    def distance(self, point):
        best = math.inf
        stack = [(0, 0.0)]
        while stack:
            index, distance = stack.pop()
            if distance > best:
                continue
            lower, upper, left, right, ids = self.nodes[index]
            if left == -1:
                for i in ids:
                    best = min(best, triangle_distance_squared(point, *self.triangles[i]))
            else:
                a = _box_distance(point, self.nodes[left][0], self.nodes[left][1])
                b = _box_distance(point, self.nodes[right][0], self.nodes[right][1])
                near, far = ((left, a), (right, b)) if a <= b else ((right, b), (left, a))
                if far[1] <= best:
                    stack.append(far)
                if near[1] <= best:
                    stack.append(near)
        return math.sqrt(max(0.0, best))


class SurfaceField:
    def __init__(self, data, occupied, progress=None):
        self.dimensions = data.dimensions
        self.occupied = occupied
        self.bvh = TriangleBVH(data.points, data.triangles, progress)
        self.cache = {}

    def sample(self, index):
        if index not in self.cache:
            nx, ny, nz = self.dimensions
            x, remainder = divmod(index, ny * nz)
            y, z = divmod(remainder, nz)
            distance = max(self.bvh.distance((x, y, z)), 1e-8)
            self.cache[index] = -distance if self.occupied[index] else distance
        return self.cache[index]

    def value_gradient(self, point):
        nx, ny, nz = self.dimensions
        x, y, z = (min(size - 2, max(0, math.floor(point[k]))) for k, size in enumerate(self.dimensions))
        t = (point[0] - x, point[1] - y, point[2] - z)
        value, gradient = 0.0, [0.0, 0.0, 0.0]
        for bit in range(8):
            corner = ((bit & 1), ((bit >> 1) & 1), ((bit >> 2) & 1))
            weights = [t[k] if corner[k] else 1 - t[k] for k in range(3)]
            sample = self.sample((x + corner[0]) * ny * nz + (y + corner[1]) * nz + z + corner[2])
            value += sample * weights[0] * weights[1] * weights[2]
            for k in range(3):
                gradient[k] += sample * (1 if corner[k] else -1) * weights[(k + 1) % 3] * weights[(k + 2) % 3]
        return value, gradient

    def project(self, point, cell):
        point = tuple(point)
        for _ in range(4):
            value, gradient = self.value_gradient(point)
            norm = _dot(gradient, gradient)
            if norm < 1e-16 or abs(value) < 1e-8:
                break
            weight = value / norm
            step = abs(weight) * math.sqrt(norm)
            if step > 0.5:
                weight *= 0.5 / step
            point = tuple(
                min(cell[k] + 1 - 1e-4, max(cell[k] + 1e-4, point[k] - weight * gradient[k])) for k in range(3)
            )
        return point
