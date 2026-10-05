"""Automatic temporary caps for open source borders. No scene operations."""

import math


MAX_CAP_WORK = 16_000_000


def _turn(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _triangulate(vertices, loop, update):
    # Oppose the source boundary direction so every original edge has two
    # opposite uses. The smallest vertex remains the deterministic start.
    loop = [loop[0]] + list(reversed(loop[1:]))
    origin = vertices[loop[0]]
    span = max(max(vertices[i][k] for i in loop) - min(vertices[i][k] for i in loop) for k in range(3))
    if span <= 0:
        raise ValueError("An open border has no usable area.")
    local = [tuple((vertices[i][k] - origin[k]) / span for k in range(3)) for i in loop]
    normal = [
        sum(
            a[(k + 1) % 3] * b[(k + 2) % 3] - a[(k + 2) % 3] * b[(k + 1) % 3]
            for a, b in zip(local, local[1:] + local[:1])
        )
        for k in range(3)
    ]
    axis = max(range(3), key=lambda k: abs(normal[k]))
    if abs(normal[axis]) <= 1e-14:
        raise ValueError("An open border crosses itself or has no usable area.")
    points = [(p[(axis + 1) % 3], p[(axis + 2) % 3]) for p in local]
    sign = 1 if normal[axis] > 0 else -1
    count = len(loop)
    convex = True
    turning = 0.0
    for i, b in enumerate(points):
        a, c = points[i - 1], points[(i + 1) % count]
        cross = _turn(a, b, c)
        if sign * cross < -1e-12:
            convex = False
        turning += math.atan2(cross, (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]))
    convex = convex and abs(turning - sign * 2 * math.pi) < 1e-6
    if convex:
        # Linear work for dense circular mouths. The center also preserves
        # every collinear boundary segment without zero-area fan triangles.
        center = tuple(sum(vertices[i][k] for i in loop) / count for k in range(3))
        index = len(vertices)
        vertices.append(center)
        return [(loop[i], loop[(i + 1) % count], index) for i in range(count)]

    work = 0

    def check():
        nonlocal work
        work += 1
        if work % 8192 == 0:
            update()
        if work > MAX_CAP_WORK:
            raise ValueError("Automatic closure exceeds the complex-border budget.")

    # Ear clipping assumes a simple projected polygon. Check complex borders
    # before accepting a cap that would cross itself.
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 2, count):
            if i == 0 and j == count - 1:
                continue
            check()
            c, d = points[j], points[(j + 1) % count]
            ab_c, ab_d, cd_a, cd_b = _turn(a, b, c), _turn(a, b, d), _turn(c, d, a), _turn(c, d, b)
            if ab_c * ab_d < -1e-24 and cd_a * cd_b < -1e-24:
                raise ValueError("An open border crosses itself in its cap projection.")
    pending = list(range(count))
    result = []
    while len(pending) > 3:
        update()
        clipped = False
        for index, b in enumerate(pending):
            a, c = pending[index - 1], pending[(index + 1) % len(pending)]
            if sign * _turn(points[a], points[b], points[c]) <= 1e-14:
                continue
            occupied = False
            for p in pending:
                if p in (a, b, c):
                    continue
                check()
                if (
                    sign * _turn(points[a], points[b], points[p]) >= -1e-14
                    and sign * _turn(points[b], points[c], points[p]) >= -1e-14
                    and sign * _turn(points[c], points[a], points[p]) >= -1e-14
                ):
                    occupied = True
                    break
            if not occupied:
                result.append((loop[a], loop[b], loop[c]))
                pending.pop(index)
                clipped = True
                break
        if not clipped:
            raise ValueError("Automatic closure could not triangulate this border.")
    a, b, c = pending
    if sign * _turn(points[a], points[b], points[c]) <= 1e-14:
        raise ValueError("Automatic closure produced a collapsed cap.")
    result.append((loop[a], loop[b], loop[c]))
    return result


def close_borders(vertices, edges, update):
    """Append cap points to a private copy and return triangles plus loop count."""
    next_vertex, incoming = {}, set()
    for (a, b), (count, winding) in edges.items():
        if count == 2 and winding == 0:
            continue
        if count != 1:
            raise ValueError("Source has nonmanifold edges or inconsistent face normals.")
        if winding < 0:
            a, b = b, a
        if a in next_vertex or b in incoming:
            raise ValueError("Source has a branching open border.")
        next_vertex[a] = b
        incoming.add(b)
    if set(next_vertex) != incoming:
        raise ValueError("Source has an incomplete open border.")
    caps, visited, closed = [], set(), 0
    for start in sorted(next_vertex):
        if start in visited:
            continue
        update()
        loop, vertex = [], start
        while vertex not in visited:
            if len(loop) % 8192 == 0:
                update()
            loop.append(vertex)
            visited.add(vertex)
            vertex = next_vertex[vertex]
        if vertex != start or len(loop) < 3:
            raise ValueError("Source has an invalid open border.")
        caps.extend(_triangulate(vertices, loop, update))
        closed += 1
    return caps, closed
