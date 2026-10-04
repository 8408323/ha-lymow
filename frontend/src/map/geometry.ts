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

/** Vertex average; enough to tell whether two outlines sit in the same place. */
export function centre(poly: Point[]): Point {
  if (!poly.length) return { x: 0, y: 0 };
  return { x: poly.reduce((s, p) => s + p.x, 0) / poly.length, y: poly.reduce((s, p) => s + p.y, 0) / poly.length };
}

/** A usable boundary: non-zero area and no edge crossing a non-adjacent edge. */
export function isSimplePolygon(poly: Point[]): boolean {
  const n = poly.length;
  if (n < 3 || area(poly) < 0.01) return false;
  const cross = (o: Point, a: Point, b: Point) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  for (let i = 0; i < n; i++) {
    const a = poly[i], b = poly[(i + 1) % n];
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue; // shares a vertex with edge i
      const c = poly[j], d = poly[(j + 1) % n];
      if (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) return false;
    }
  }
  return true;
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

/** An edit handle; `src` is its index in the original outline while it hasn't moved. */
export type Handle = Point & { src?: number };

/**
 * Reduce a polygon to at most `max` handles, keeping the most significant vertices
 * (Visvalingam: repeatedly drop the vertex spanning the smallest triangle).
 * Robot outlines carry hundreds of points; editing needs a handful of handles.
 */
export function simplify(poly: Point[], max: number): Handle[] {
  const pts: Handle[] = poly.map((p, src) => ({ x: p.x, y: p.y, src }));
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

/**
 * The outline to save: between two neighbouring handles that are both untouched
 * the original vertices come back, so editing one corner doesn't flatten the rest
 * of the boundary. `initial` is the handle set the editor started from: a span
 * that skips one of those had a handle deleted, and stays the straight edge shown.
 */
export function expand(handles: Handle[], orig: Point[], initial: Handle[]): Point[] {
  const starts = new Set(initial.map((h) => h.src));
  const out: Point[] = [];
  handles.forEach((h, i) => {
    out.push({ x: h.x, y: h.y });
    const n = handles[(i + 1) % handles.length];
    if (h.src === undefined || n.src === undefined) return;
    const span: Point[] = [];
    for (let k = (h.src + 1) % orig.length; k !== n.src; k = (k + 1) % orig.length) {
      if (starts.has(k)) return;
      span.push(orig[k]);
    }
    out.push(...span);
  });
  return out;
}

/** A "nice" scale-bar length (1/2/5 × 10ⁿ metres) close to `target` metres. */
export function niceLength(target: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(target)));
  for (const m of [1, 2, 5, 10]) if (m * p >= target) return m * p;
  return 10 * p;
}

export const pathD = (pts: Point[], close = true) =>
  pts.length ? `M${pts.map((p) => `${p.x.toFixed(3)},${(-p.y).toFixed(3)}`).join("L")}${close ? "Z" : ""}` : "";
