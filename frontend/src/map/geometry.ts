// Geometry for the map. Map data is ENU metres (x = east, y = north); the SVG
// uses the same metres with y flipped (svgY = -north) so north points up.

import type { Point } from "../hass";

export interface Box {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export const toSvg = (p: Point): Point => ({ x: p.x, y: -p.y });
export const fromSvg = (p: Point): Point => ({ x: p.x, y: -p.y });

export function rotate(p: Point, c: Point, deg: number): Point {
  const r = (deg * Math.PI) / 180;
  const cos = Math.cos(r);
  const sin = Math.sin(r);
  const dx = p.x - c.x;
  const dy = p.y - c.y;
  return { x: c.x + dx * cos - dy * sin, y: c.y + dx * sin + dy * cos };
}

export function bbox(points: Point[]): Box | undefined {
  if (!points.length) return undefined;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  return { minX, minY, maxX, maxY };
}

/** Shoelace area in m² (absolute). */
export function area(poly: Point[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i];
    const q = poly[(i + 1) % poly.length];
    a += p.x * q.y - q.x * p.y;
  }
  return Math.abs(a) / 2;
}

export function polylineLength(pts: Point[]): number {
  let l = 0;
  for (let i = 1; i < pts.length; i++) l += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return l;
}

export function pointInPolygon(p: Point, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i];
    const b = poly[j];
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

/**
 * A point well inside the polygon for its label: the grid cell centre farthest
 * from the boundary (cheap pole-of-inaccessibility). Falls back to the bbox centre.
 */
export function labelPoint(poly: Point[]): Point {
  const b = bbox(poly);
  if (!b) return { x: 0, y: 0 };
  let best = { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 };
  let bestD = -1;
  const n = 16;
  for (let i = 0; i <= n; i++) {
    for (let j = 0; j <= n; j++) {
      const p = { x: b.minX + ((b.maxX - b.minX) * i) / n, y: b.minY + ((b.maxY - b.minY) * j) / n };
      if (!pointInPolygon(p, poly)) continue;
      const d = distToEdges(p, poly);
      if (d > bestD) {
        bestD = d;
        best = p;
      }
    }
  }
  return best;
}

function distToEdges(p: Point, poly: Point[]): number {
  let d = Infinity;
  for (let i = 0; i < poly.length; i++) d = Math.min(d, distToSegment(p, poly[i], poly[(i + 1) % poly.length]));
  return d;
}

export function distToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/** Andrew's monotone chain convex hull. */
export function convexHull(points: Point[]): Point[] {
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (pts.length < 3) return pts;
  const cross = (o: Point, a: Point, b: Point) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: Point[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Point[] = [];
  for (const p of pts.reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

/**
 * Reduce a polygon to at most `max` vertices, keeping the most significant ones
 * (Visvalingam: repeatedly drop the vertex spanning the smallest triangle).
 * Robot outlines carry hundreds of points; editing needs a handful of handles.
 */
export function simplify(poly: Point[], max: number): Point[] {
  const pts = [...poly];
  const tri = (i: number) => {
    const a = pts[(i - 1 + pts.length) % pts.length];
    const b = pts[i];
    const c = pts[(i + 1) % pts.length];
    return Math.abs((a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y)) / 2);
  };
  while (pts.length > Math.max(3, max)) {
    let minI = 0;
    let minA = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const a = tri(i);
      if (a < minA) {
        minA = a;
        minI = i;
      }
    }
    pts.splice(minI, 1);
  }
  return pts;
}

/** A "nice" scale-bar length (1/2/5 × 10ⁿ metres) close to `target` metres. */
export function niceLength(target: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(target)));
  for (const m of [1, 2, 5, 10]) if (m * p >= target) return m * p;
  return 10 * p;
}

export const pathD = (pts: Point[], close = true) =>
  pts.length ? `M${pts.map((p) => `${p.x.toFixed(3)},${(-p.y).toFixed(3)}`).join("L")}${close ? "Z" : ""}` : "";
