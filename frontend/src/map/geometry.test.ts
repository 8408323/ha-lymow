import { describe, expect, it } from "vitest";
import { area, centre, expand, isSimplePolygon, labelPoint, niceLength, pathD, pointInPolygon, polylineLength, rotate, simplify } from "./geometry";

const square = [
  { x: 0, y: 0 },
  { x: 10, y: 0 },
  { x: 10, y: 10 },
  { x: 0, y: 10 },
];

describe("geometry", () => {
  it("area and containment", () => {
    expect(area(square)).toBe(100);
    expect(pointInPolygon({ x: 5, y: 5 }, square)).toBe(true);
    expect(pointInPolygon({ x: 15, y: 5 }, square)).toBe(false);
  });

  it("label point sits inside an L-shape, away from the notch", () => {
    const l = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 2 },
      { x: 2, y: 2 },
      { x: 2, y: 10 },
      { x: 0, y: 10 },
    ];
    expect(pointInPolygon(labelPoint(l), l)).toBe(true);
  });

  it("simplify keeps corners of a densely sampled square", () => {
    const dense = square.flatMap((p, i) => {
      const q = square[(i + 1) % 4];
      return Array.from({ length: 10 }, (_, k) => ({ x: p.x + ((q.x - p.x) * k) / 10, y: p.y + ((q.y - p.y) * k) / 10 }));
    });
    const s = simplify(dense, 4);
    expect(s).toHaveLength(4);
    expect(area(s)).toBeCloseTo(100);
  });

  it("rotate, length, scale bar, path", () => {
    const r = rotate({ x: 1, y: 0 }, { x: 0, y: 0 }, 90);
    expect(r.x).toBeCloseTo(0);
    expect(r.y).toBeCloseTo(1);
    expect(polylineLength([{ x: 0, y: 0 }, { x: 3, y: 4 }])).toBe(5);
    expect(niceLength(3.2)).toBe(5);
    expect(niceLength(12)).toBe(20);
    expect(pathD([{ x: 1, y: 2 }], false)).toBe("M1.000,-2.000");
  });

  it("expand keeps original detail between untouched handles", () => {
    const dense = Array.from({ length: 12 }, (_, i) => ({ x: Math.cos(i / 2), y: Math.sin(i / 2) + (i % 2) * 0.01 }));
    const handles = simplify(dense, 4);
    expect(expand(handles, dense, handles)).toEqual(dense.map((p) => ({ x: p.x, y: p.y })));
    // Moving one handle straightens only its two edges.
    const moved = handles.map((h, i) => (i === 0 ? { x: 9, y: 9 } : h));
    const out = expand(moved, dense, handles);
    expect(out[0]).toEqual({ x: 9, y: 9 });
    expect(out.length).toBeGreaterThan(4);
    expect(out.length).toBeLessThan(dense.length);
    // Deleting a handle leaves the straight edge the editor showed.
    const del = handles.filter((_, i) => i !== 1);
    expect(expand(del, dense, handles).length).toBeLessThan(dense.length - 1);
  });

  it("isSimplePolygon rejects self-intersections and zero area", () => {
    expect(isSimplePolygon(square)).toBe(true);
    expect(isSimplePolygon([{ x: 0, y: 0 }, { x: 10, y: 10 }, { x: 10, y: 0 }, { x: 0, y: 10 }])).toBe(false); // bow tie
    expect(isSimplePolygon([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }])).toBe(false); // collinear
    expect(isSimplePolygon([{ x: 0, y: 0 }, { x: 1, y: 1 }])).toBe(false);
    expect(isSimplePolygon([...square, square[0]])).toBe(true); // closing duplicate
    // vertex touching a non-adjacent edge
    expect(isSimplePolygon([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }, { x: 5, y: 0 }, { x: 0, y: 10 }])).toBe(false);
  });

  it("simplify ignores a closing duplicate", () => {
    expect(simplify([...square, square[0]], 40)).toHaveLength(4);
  });

  it("centre is the area centroid, independent of point density", () => {
    const dense = [...square.slice(0, 1), { x: 1, y: 0 }, { x: 2, y: 0 }, { x: 3, y: 0 }, ...square.slice(1)];
    expect(centre(square)).toEqual({ x: 5, y: 5 });
    expect(centre(dense).x).toBeCloseTo(5);
    expect(centre(dense).y).toBeCloseTo(5);
  });

  it("expand doesn't re-insert the closing duplicate when the first corner moves", () => {
    const closed = [...square, square[0]];
    const h = simplify(closed, 40);
    const moved = h.map((p, i) => (i === 0 ? { x: 1, y: 1 } : p));
    expect(expand(moved, closed, h)).toEqual([{ x: 1, y: 1 }, ...square.slice(1)]);
  });
});
