"""Pure geometry validation and distance calculations for stored Query Core data."""

from __future__ import annotations

import math
from typing import Any

Point3 = tuple[float, float, float]
Primitive = tuple[str, Point3, Point3 | None]


def _point(value: Any) -> Point3 | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    point = tuple(float(item) for item in value)
    return point if all(math.isfinite(item) for item in point) else None  # type: ignore[return-value]


def decode_location_primitive(geometry_type: str, geometry: Any) -> Primitive | None:
    """Validate a supported stored primitive without repairing or transforming it."""
    if not isinstance(geometry, dict):
        return None
    if geometry_type == "point":
        point = _point(geometry.get("point", geometry.get("coordinates")))
        return ("point", point, None) if point is not None else None
    if geometry_type == "line":
        start, end = _point(geometry.get("start")), _point(geometry.get("end"))
        return ("line", start, end) if start is not None and end is not None else None
    return None


def primitive_bounds(primitive: Primitive) -> tuple[float, float, float, float, float, float]:
    _, start, end = primitive
    end = end or start
    return tuple(  # type: ignore[return-value]
        value
        for axis in range(3)
        for value in (min(start[axis], end[axis]), max(start[axis], end[axis]))
    )


def _sub(a: Point3, b: Point3) -> Point3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add_scaled(a: Point3, vector: Point3, scale: float) -> Point3:
    return tuple(a[index] + vector[index] * scale for index in range(3))  # type: ignore[return-value]


def _dot(a: Point3, b: Point3) -> float:
    return sum(a[index] * b[index] for index in range(3))


def point_distance(a: Point3, b: Point3) -> float:
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def point_segment_distance(point: Point3, start: Point3, end: Point3) -> float:
    direction = _sub(end, start)
    length_squared = _dot(direction, direction)
    if length_squared == 0.0:
        return point_distance(point, start)
    parameter = max(0.0, min(1.0, _dot(_sub(point, start), direction) / length_squared))
    return point_distance(point, _add_scaled(start, direction, parameter))


def segment_distance(p1: Point3, q1: Point3, p2: Point3, q2: Point3) -> float:
    """Distance between finite 3-D segments, including parallel/degenerate cases."""
    u, v, w = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, b, c, d, e = _dot(u, u), _dot(u, v), _dot(v, v), _dot(u, w), _dot(v, w)
    if a == 0.0:
        return point_segment_distance(p1, p2, q2)
    if c == 0.0:
        return point_segment_distance(p2, p1, q1)
    denominator = a * c - b * b
    if denominator <= 1e-15 * a * c:
        s_numerator, s_denominator = 0.0, 1.0
        t_numerator, t_denominator = e, c
    else:
        s_numerator, s_denominator = b * e - c * d, denominator
        t_numerator, t_denominator = a * e - b * d, denominator
        if s_numerator < 0.0:
            s_numerator, t_numerator, t_denominator = 0.0, e, c
        elif s_numerator > s_denominator:
            s_numerator, t_numerator, t_denominator = s_denominator, e + b, c
    if t_numerator < 0.0:
        t_numerator = 0.0
        if -d < 0.0:
            s_numerator, s_denominator = 0.0, 1.0
        elif -d > a:
            s_numerator, s_denominator = 1.0, 1.0
        else:
            s_numerator, s_denominator = -d, a
    elif t_numerator > t_denominator:
        t_numerator = t_denominator
        if -d + b < 0.0:
            s_numerator, s_denominator = 0.0, 1.0
        elif -d + b > a:
            s_numerator, s_denominator = 1.0, 1.0
        else:
            s_numerator, s_denominator = -d + b, a
    sc = 0.0 if s_numerator == 0.0 else s_numerator / s_denominator
    tc = 0.0 if t_numerator == 0.0 else t_numerator / t_denominator
    return point_distance(_add_scaled(p1, u, sc), _add_scaled(p2, v, tc))


def primitive_distance(a: Primitive, b: Primitive) -> float:
    if a[0] == "point" and b[0] == "point":
        return point_distance(a[1], b[1])
    if a[0] == "point":
        return point_segment_distance(a[1], b[1], b[2] or b[1])
    if b[0] == "point":
        return point_segment_distance(b[1], a[1], a[2] or a[1])
    return segment_distance(a[1], a[2] or a[1], b[1], b[2] or b[1])
