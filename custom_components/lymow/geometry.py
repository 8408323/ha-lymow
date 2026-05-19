"""Pure-Python polygon helpers for Lymow zone editing services."""

from __future__ import annotations

from typing import Any


def _cross(o: dict[str, float], a: dict[str, float], b: dict[str, float]) -> float:
    """Cross-product of (a-o) and (b-o). Positive = CCW turn, negative = CW, zero = collinear."""
    return (a["x"] - o["x"]) * (b["y"] - o["y"]) - (a["y"] - o["y"]) * (b["x"] - o["x"])


def convex_hull(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Return the convex hull of *points* as a CCW polygon (Andrew's monotone chain).

    Input is a list of ``{"x": float, "y": float}`` dicts; output is the hull in
    the same shape, CCW, with the start vertex unrepeated. Raises ``ValueError``
    if fewer than 3 unique points are supplied (no polygon possible).
    """
    if not points:
        raise ValueError("convex_hull needs at least one point")
    # Deduplicate while preserving x/y; sort lexicographically by (x, y).
    unique: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for p in points:
        key = (float(p["x"]), float(p["y"]))
        if key not in seen:
            seen.add(key)
            unique.append({"x": key[0], "y": key[1]})
    if len(unique) < 3:
        raise ValueError(f"convex_hull needs at least 3 unique points, got {len(unique)}")
    unique.sort(key=lambda p: (p["x"], p["y"]))

    lower: list[dict[str, float]] = []
    for p in unique:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[dict[str, float]] = []
    for p in reversed(unique):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate, dropping the duplicate endpoints (last of each chain matches
    # the first of the other).
    return lower[:-1] + upper[:-1]


def merge_zone_polygons(*polygons: list[dict[str, float]]) -> list[dict[str, float]]:
    """Return the convex hull covering all input polygons' vertices.

    This is the simplest stable "combine zones" operation. For convex,
    nearly-touching input zones the result hugs the inputs tightly. For
    disjoint inputs the hull includes the gap between them — explicit and
    documented behaviour.
    """
    if not polygons:
        raise ValueError("merge_zone_polygons needs at least one polygon")
    all_points: list[dict[str, Any]] = []
    for poly in polygons:
        all_points.extend(poly)
    return convex_hull(all_points)


def _line_side(line_p1: dict[str, float], line_p2: dict[str, float], pt: dict[str, float]) -> float:
    """Signed side: > 0 if pt is left of the line p1→p2, < 0 if right, 0 on line."""
    return (line_p2["x"] - line_p1["x"]) * (pt["y"] - line_p1["y"]) - (line_p2["y"] - line_p1["y"]) * (
        pt["x"] - line_p1["x"]
    )


def _line_intersection(
    a: dict[str, float],
    b: dict[str, float],
    p: dict[str, float],
    q: dict[str, float],
) -> dict[str, float]:
    """Intersection of segment a→b with infinite line p→q. Caller ensures non-parallel."""
    dx_ab = b["x"] - a["x"]
    dy_ab = b["y"] - a["y"]
    dx_pq = q["x"] - p["x"]
    dy_pq = q["y"] - p["y"]
    denom = dx_ab * dy_pq - dy_ab * dx_pq
    if denom == 0:
        # Parallel — caller's contract precludes this but fall back to the
        # segment's midpoint for determinism rather than raising.
        return {"x": (a["x"] + b["x"]) / 2.0, "y": (a["y"] + b["y"]) / 2.0}
    t = ((p["x"] - a["x"]) * dy_pq - (p["y"] - a["y"]) * dx_pq) / denom
    return {"x": a["x"] + t * dx_ab, "y": a["y"] + t * dy_ab}


def split_polygon(
    polygon: list[dict[str, float]],
    cut_p1: dict[str, float],
    cut_p2: dict[str, float],
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Split a convex polygon by the infinite line through ``cut_p1``→``cut_p2``.

    Returns ``(left_polygon, right_polygon)``. "Left" is the side where
    ``_line_side`` is positive (CCW from the cut direction); "right" is the
    negative side. Each output is the polygon's intersection with that
    half-plane.

    Raises ``ValueError`` if the line doesn't actually divide the polygon —
    every vertex strictly on one side would produce a full piece and an
    empty piece, which isn't a meaningful operation.

    Non-convex inputs can yield self-intersecting outputs; convex inputs only
    for now.
    """
    if len(polygon) < 3:
        raise ValueError(f"split_polygon needs at least 3 vertices, got {len(polygon)}")
    if cut_p1["x"] == cut_p2["x"] and cut_p1["y"] == cut_p2["y"]:
        raise ValueError("Cut line endpoints are identical")

    sides = [_line_side(cut_p1, cut_p2, v) for v in polygon]
    if all(s >= 0 for s in sides) or all(s <= 0 for s in sides):
        raise ValueError("Cut line does not divide the polygon")

    left: list[dict[str, float]] = []
    right: list[dict[str, float]] = []
    n = len(polygon)
    for i in range(n):
        curr = polygon[i]
        nxt = polygon[(i + 1) % n]
        curr_side = sides[i]
        next_side = sides[(i + 1) % n]
        if curr_side >= 0:
            left.append(curr)
        if curr_side <= 0:
            right.append(curr)
        # Genuine crossing — append the intersection to both halves.
        if (curr_side > 0 and next_side < 0) or (curr_side < 0 and next_side > 0):
            ip = _line_intersection(curr, nxt, cut_p1, cut_p2)
            left.append(ip)
            right.append(ip)
    return left, right
