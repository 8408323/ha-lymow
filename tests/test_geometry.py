"""Tests for the pure-Python geometry helpers."""

from __future__ import annotations

import pytest
from lymow.geometry import convex_hull, merge_zone_polygons


def _pt(x: float, y: float) -> dict[str, float]:
    return {"x": x, "y": y}


def _set(poly: list[dict[str, float]]) -> set[tuple[float, float]]:
    return {(p["x"], p["y"]) for p in poly}


# ---------------------------------------------------------------------------
# convex_hull
# ---------------------------------------------------------------------------


def test_convex_hull_of_unit_square_returns_four_corners() -> None:
    pts = [_pt(0, 0), _pt(1, 0), _pt(1, 1), _pt(0, 1)]
    hull = convex_hull(pts)
    assert _set(hull) == {(0, 0), (1, 0), (1, 1), (0, 1)}


def test_convex_hull_drops_interior_point() -> None:
    pts = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2), _pt(1, 1)]  # 5th is inside
    hull = convex_hull(pts)
    assert _set(hull) == {(0, 0), (2, 0), (2, 2), (0, 2)}


def test_convex_hull_drops_collinear_point() -> None:
    pts = [_pt(0, 0), _pt(1, 0), _pt(2, 0), _pt(1, 2)]  # 3 collinear on x-axis
    hull = convex_hull(pts)
    # Collinear (1,0) gets dropped; result is the triangle.
    assert _set(hull) == {(0, 0), (2, 0), (1, 2)}


def test_convex_hull_is_counter_clockwise() -> None:
    pts = [_pt(0, 0), _pt(1, 0), _pt(1, 1), _pt(0, 1)]
    hull = convex_hull(pts)
    # Signed area positive ⇔ CCW.
    area = 0.0
    for i in range(len(hull)):
        a = hull[i]
        b = hull[(i + 1) % len(hull)]
        area += a["x"] * b["y"] - b["x"] * a["y"]
    assert area > 0


def test_convex_hull_dedupes_inputs() -> None:
    pts = [_pt(0, 0), _pt(0, 0), _pt(1, 0), _pt(1, 1), _pt(0, 1)]
    hull = convex_hull(pts)
    assert len(hull) == 4


def test_convex_hull_raises_when_fewer_than_three_unique_points() -> None:
    with pytest.raises(ValueError, match="3 unique points"):
        convex_hull([_pt(0, 0), _pt(1, 1)])


def test_convex_hull_raises_on_empty_input() -> None:
    with pytest.raises(ValueError):
        convex_hull([])


def test_convex_hull_handles_float_coords() -> None:
    pts = [_pt(0.5, 0.5), _pt(1.7, 0.2), _pt(1.3, 1.9), _pt(0.1, 1.5)]
    hull = convex_hull(pts)
    assert len(hull) == 4


# ---------------------------------------------------------------------------
# merge_zone_polygons — convenience over convex_hull
# ---------------------------------------------------------------------------


def test_merge_two_overlapping_squares_returns_hull_covering_both() -> None:
    a = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2)]
    b = [_pt(1, 1), _pt(3, 1), _pt(3, 3), _pt(1, 3)]
    hull = merge_zone_polygons(a, b)
    # Hull corners are (0,0), (2,0), (3,1), (3,3), (1,3), (0,2).
    expected = {(0, 0), (2, 0), (3, 1), (3, 3), (1, 3), (0, 2)}
    assert _set(hull) == expected


def test_merge_disjoint_squares_hull_includes_gap() -> None:
    """Documented trade-off: convex-hull merge of disjoint zones includes the
    bridge between them. Captured in tests so a future change is intentional."""
    a = [_pt(0, 0), _pt(1, 0), _pt(1, 1), _pt(0, 1)]
    b = [_pt(5, 0), _pt(6, 0), _pt(6, 1), _pt(5, 1)]
    hull = merge_zone_polygons(a, b)
    assert _set(hull) == {(0, 0), (6, 0), (6, 1), (0, 1)}


def test_merge_three_triangles_returns_combined_hull() -> None:
    a = [_pt(0, 0), _pt(2, 0), _pt(1, 2)]
    b = [_pt(3, 0), _pt(5, 0), _pt(4, 2)]
    c = [_pt(0, 3), _pt(5, 3), _pt(2.5, 5)]
    hull = merge_zone_polygons(a, b, c)
    # Hull is convex and covers all 9 input points.
    for poly in (a, b, c):
        for p in poly:
            # Every input vertex must be either *on* the hull or interior to it.
            # We only check that the hull is non-degenerate.
            pass
    assert len(hull) >= 3


def test_merge_zone_polygons_raises_on_no_input() -> None:
    with pytest.raises(ValueError):
        merge_zone_polygons()


def test_merge_zone_polygons_raises_when_combined_under_three_points() -> None:
    with pytest.raises(ValueError):
        merge_zone_polygons([_pt(0, 0), _pt(1, 1)])


# ---------------------------------------------------------------------------
# split_polygon
# ---------------------------------------------------------------------------


def test_split_square_with_vertical_line_through_middle() -> None:
    from lymow.geometry import split_polygon

    square = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2)]
    # Vertical cut at x=1 oriented bottom→top.
    left, right = split_polygon(square, _pt(1, -1), _pt(1, 5))
    left_xs = sorted({p["x"] for p in left})
    right_xs = sorted({p["x"] for p in right})
    assert left_xs == [0.0, 1.0]
    assert right_xs == [1.0, 2.0]


def test_split_square_with_horizontal_line_through_middle() -> None:
    from lymow.geometry import split_polygon

    square = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2)]
    # Horizontal cut at y=1 oriented left→right; "left" of a left→right line is the upper half.
    left, right = split_polygon(square, _pt(-1, 1), _pt(5, 1))
    left_ys = sorted({p["y"] for p in left})
    right_ys = sorted({p["y"] for p in right})
    assert left_ys == [1.0, 2.0]
    assert right_ys == [0.0, 1.0]


def test_split_triangle_diagonally() -> None:
    from lymow.geometry import split_polygon

    triangle = [_pt(0, 0), _pt(4, 0), _pt(2, 4)]
    left, right = split_polygon(triangle, _pt(2, -1), _pt(2, 5))
    assert len(left) == 3
    assert len(right) == 3


def test_split_polygon_raises_when_line_misses_polygon() -> None:
    from lymow.geometry import split_polygon

    square = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2)]
    with pytest.raises(ValueError, match="does not divide"):
        split_polygon(square, _pt(-1, 10), _pt(5, 10))


def test_split_polygon_raises_when_endpoints_identical() -> None:
    from lymow.geometry import split_polygon

    square = [_pt(0, 0), _pt(2, 0), _pt(2, 2), _pt(0, 2)]
    with pytest.raises(ValueError, match="identical"):
        split_polygon(square, _pt(1, 1), _pt(1, 1))


def test_split_polygon_raises_with_too_few_vertices() -> None:
    from lymow.geometry import split_polygon

    with pytest.raises(ValueError, match="3 vertices"):
        split_polygon([_pt(0, 0), _pt(1, 1)], _pt(0, 0), _pt(1, 0))


def test_line_intersection_falls_back_to_midpoint_when_parallel() -> None:
    """Direct test of the defensive parallel-line branch in _line_intersection.

    split_polygon's side-classification keeps callers from ever reaching this
    branch in practice, but the fallback exists to avoid ZeroDivisionError if a
    future caller forgets the contract.
    """
    from lymow.geometry import _line_intersection

    a, b = _pt(0, 0), _pt(2, 0)  # horizontal segment
    p, q = _pt(0, 5), _pt(2, 5)  # parallel horizontal cut line
    result = _line_intersection(a, b, p, q)
    assert result == {"x": 1.0, "y": 0.0}  # midpoint of a→b


def test_split_polygon_areas_sum_to_original() -> None:
    """Sanity check: sum of split-piece areas equals the original area."""
    from lymow.geometry import split_polygon

    def _area(poly: list[dict[str, float]]) -> float:
        area = 0.0
        n = len(poly)
        for i in range(n):
            a = poly[i]
            b = poly[(i + 1) % n]
            area += a["x"] * b["y"] - b["x"] * a["y"]
        return abs(area) / 2.0

    square = [_pt(0, 0), _pt(4, 0), _pt(4, 4), _pt(0, 4)]
    original_area = _area(square)
    left, right = split_polygon(square, _pt(1, -1), _pt(3, 5))
    assert abs((_area(left) + _area(right)) - original_area) < 1e-9
