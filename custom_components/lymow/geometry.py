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
