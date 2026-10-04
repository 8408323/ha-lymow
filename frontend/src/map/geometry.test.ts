import { describe, expect, it } from "vitest";
import { area, labelPoint, niceLength, pathD, pointInPolygon, polylineLength, rotate, simplify } from "./geometry";

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
});
