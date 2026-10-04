// Interactive SVG map. Units are metres; see geometry.ts for the axis convention.
//
// Gestures: wheel / pinch zoom around the pointer, drag to pan, right-drag (or the
// rotate buttons) to rotate. Taps on zones call onPick; taps on empty ground call
// onBackground. In edit mode, vertex handles drag, edge "+" handles insert a
// vertex, and the dock can be dragged when stationMovable.

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { MapData, Point, Zone } from "../hass";
import { zoneLabel } from "../mower";
import { Icon, cx } from "../ui";
import { area, bbox, convexHull, fromSvg, labelPoint, niceLength, pathD, polylineLength, rotate, toSvg } from "./geometry";

export type Kind = "go" | "nogo" | "ch";
export type LabelMode = "name" | "area" | "both" | "none";

interface Props {
  map: MapData;
  interactive?: boolean;
  selected?: Set<string>;
  focused?: string | null;
  edit?: Point[] | null;
  activeVertex?: number | null;
  onVertex?: (i: number | null) => void;
  onEditChange?: (pts: Point[]) => void;
  onPick?: (kind: Kind, hashId: string) => void;
  onBackground?: () => void;
  stationMovable?: boolean;
  onStationMoved?: (p: Point) => void;
  trail?: Point[];
  labels?: LabelMode;
  showTrail?: boolean;
  rotation?: number;
  onRotation?: (deg: number) => void;
  overlay?: ReactNode;
  className?: string;
}

interface VB {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Drag =
  | { kind: "pan"; sx: number; sy: number; vb: VB }
  | { kind: "rotate"; sx: number; start: number }
  | { kind: "vertex"; i: number }
  | { kind: "station" }
  | { kind: "pinch"; d0: number; vb: VB; mid: Point };

const TAP_PX = 5;

export function MapCanvas(props: Props) {
  const { map, interactive = true, selected, focused, edit, labels = "name", rotation = 0 } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const groupRef = useRef<SVGGElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [vb, setVb] = useState<VB | null>(null);
  const [stationDrag, setStationDrag] = useState<Point | null>(null);
  const drag = useRef<Drag | null>(null);
  const pointers = useRef(new Map<number, Point>());
  const down = useRef<{ x: number; y: number; pick?: { kind: Kind; id: string }; moved: boolean } | null>(null);
  const clipId = `ly-clip-${useId().replace(/[^a-z0-9]/gi, "")}`;

  const go = map.go_zones ?? [];
  const nogo = map.nogo_zones ?? [];
  const channels = map.channels ?? [];
  const station = map.charging_station;
  const robot = map.poseEastM !== undefined && map.poseNorthM !== undefined ? { x: map.poseEastM, y: map.poseNorthM } : undefined;

  // All geometry in SVG coordinates, used for fitting and the rotation pivot.
  const allSvg = useMemo(() => {
    const pts: Point[] = [];
    for (const z of [...go, ...nogo, ...channels]) for (const p of z.polygon ?? []) pts.push(toSvg(p));
    if (station) pts.push(toSvg(station));
    if (robot) pts.push(toSvg(robot));
    return pts;
  }, [map]);
  const center = useMemo(() => {
    const b = bbox(allSvg) ?? { minX: -5, minY: -5, maxX: 5, maxY: 5 };
    return { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 };
  }, [allSvg.length, go.length]);

  const fit = () => {
    const b = bbox(allSvg.map((p) => rotate(p, center, rotation))) ?? { minX: -5, minY: -5, maxX: 5, maxY: 5 };
    const w = Math.max(b.maxX - b.minX, 4);
    const h = Math.max(b.maxY - b.minY, 4);
    const pad = Math.max(1.5, (w + h) * 0.05);
    setVb({ x: b.minX - pad, y: b.minY - pad, w: w + 2 * pad, h: h + 2 * pad });
  };
  const fitKey = `${go.length}|${nogo.length}|${channels.length}|${allSvg.length > 0}`;
  useEffect(fit, [fitKey, rotation]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Pixels per metre (preserveAspectRatio="meet" → the smaller axis scale wins).
  const k = vb && size.w ? Math.min(size.w / vb.w, size.h / vb.h) : 4;
  const px = (n: number) => n / k;

  const toViewBox = (cx: number, cy: number): Point => {
    const m = svgRef.current?.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const p = new DOMPoint(cx, cy).matrixTransform(m.inverse());
    return { x: p.x, y: p.y };
  };
  const toMap = (cx: number, cy: number): Point => {
    const m = groupRef.current?.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const p = new DOMPoint(cx, cy).matrixTransform(m.inverse());
    return fromSvg({ x: p.x, y: p.y });
  };

  const zoomAt = (pt: Point, factor: number, base?: VB) => {
    setVb((cur) => {
      const v = base ?? cur;
      if (!v) return v;
      const full = Math.max(...[bbox(allSvg)].map((b) => (b ? b.maxX - b.minX + b.maxY - b.minY : 20)), 10);
      const w = Math.min(Math.max(v.w * factor, full / 40), full * 3);
      const f = w / v.w;
      return { x: pt.x - (pt.x - v.x) * f, y: pt.y - (pt.y - v.y) * f, w, h: v.h * f };
    });
  };

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !interactive) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomAt(toViewBox(e.clientX, e.clientY), e.deltaY > 0 ? 1.15 : 1 / 1.15);
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [interactive, allSvg]);

  const onPointerDown = (e: React.PointerEvent) => {
    const target = e.target as Element;
    const handle = target.closest("[data-handle]") as HTMLElement | null;
    const pickEl = target.closest("[data-pick]") as HTMLElement | null;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    down.current = {
      x: e.clientX,
      y: e.clientY,
      moved: false,
      pick: pickEl ? { kind: pickEl.dataset.kind as Kind, id: pickEl.dataset.pick! } : undefined,
    };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);

    if (handle && edit && props.onEditChange) {
      const [type, idx] = (handle.dataset.handle ?? "").split(":");
      if (type === "v") {
        drag.current = { kind: "vertex", i: Number(idx) };
        props.onVertex?.(Number(idx));
        return;
      }
      if (type === "m") {
        const i = Number(idx);
        const a = edit[i];
        const b = edit[(i + 1) % edit.length];
        const next = [...edit];
        next.splice(i + 1, 0, { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
        props.onEditChange(next);
        props.onVertex?.(i + 1);
        drag.current = { kind: "vertex", i: i + 1 };
        return;
      }
    }
    if (handle?.dataset.handle === "station" && props.stationMovable) {
      drag.current = { kind: "station" };
      return;
    }
    if (!interactive || !vb) return;
    if (pointers.current.size === 2) {
      const [p1, p2] = [...pointers.current.values()];
      const mid = toViewBox((p1.x + p2.x) / 2, (p1.y + p2.y) / 2);
      drag.current = { kind: "pinch", d0: Math.hypot(p1.x - p2.x, p1.y - p2.y), vb, mid };
      return;
    }
    drag.current = e.button === 2 ? { kind: "rotate", sx: e.clientX, start: rotation } : { kind: "pan", sx: e.clientX, sy: e.clientY, vb };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const d = down.current;
    if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > TAP_PX) d.moved = true;
    const g = drag.current;
    if (!g || !d?.moved) return;
    if (g.kind === "vertex" && edit && props.onEditChange) {
      const next = [...edit];
      next[g.i] = toMap(e.clientX, e.clientY);
      props.onEditChange(next);
    } else if (g.kind === "station") {
      setStationDrag(toMap(e.clientX, e.clientY));
    } else if (g.kind === "pan") {
      setVb({ ...g.vb, x: g.vb.x - (e.clientX - g.sx) / k, y: g.vb.y - (e.clientY - g.sy) / k });
    } else if (g.kind === "rotate") {
      props.onRotation?.(Math.round((g.start + (e.clientX - g.sx) * 0.4) % 360));
    } else if (g.kind === "pinch" && pointers.current.size === 2) {
      const [p1, p2] = [...pointers.current.values()];
      zoomAt(g.mid, g.d0 / Math.max(1, Math.hypot(p1.x - p2.x, p1.y - p2.y)), g.vb);
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    const d = down.current;
    const g = drag.current;
    if (pointers.current.size > 0) return; // still pinching
    drag.current = null;
    down.current = null;
    if (g?.kind === "station" && stationDrag && d?.moved) {
      props.onStationMoved?.(stationDrag);
      setStationDrag(null);
      return;
    }
    setStationDrag(null);
    if (!d || d.moved || g?.kind === "vertex") return;
    if (d.pick) props.onPick?.(d.pick.kind, d.pick.id);
    else props.onBackground?.();
  };

  // Mowed area: hull of the server-side mow path, clipped to the go-zones.
  const mowed = useMemo(() => {
    const pts = (map.mow_path?.segments ?? []).flat();
    return pts.length >= 3 ? convexHull(pts) : [];
  }, [map.mow_path]);

  const label = (z: Zone, i: number, kind: Kind): string | null => {
    if (labels === "none") return null;
    const name = kind === "go" ? zoneLabel(z, i) : z.name?.trim() || (kind === "nogo" ? "No-go" : z.isDockingChannel ? "Dock channel" : "Channel");
    const poly = z.polygon ?? [];
    const metric = kind === "ch" ? `${polylineLength(poly).toFixed(0)} m` : `${Math.round(z.area ?? area(poly))} m²`;
    if (labels === "area") return metric;
    if (labels === "both") return `${name} · ${metric}`;
    return name;
  };

  const rot = `rotate(${rotation} ${center.x} ${center.y})`;
  const upright = (p: Point) => `rotate(${-rotation} ${p.x} ${-p.y})`;
  const stationPos = stationDrag ?? station;
  const scaleM = niceLength(80 / k);

  return (
    <div ref={wrapRef} className={cx("ly-map", props.className, interactive && "ly-map--interactive")} onContextMenu={(e) => e.preventDefault()}>
      {vb && (
        <svg
          ref={svgRef}
          viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
          preserveAspectRatio="xMidYMid meet"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          role="img"
          aria-label="Lawn map"
        >
          <defs>
            <clipPath id={clipId}>
              {go.map((z) => (
                <path key={z.hashId} d={pathD(z.polygon ?? [])} />
              ))}
            </clipPath>
          </defs>
          <g ref={groupRef} transform={rot}>
            {channels.map((c) => (
              <path
                key={c.hashId}
                d={pathD(c.polygon ?? [], false)}
                className={cx("m-ch", c.isDockingChannel && "m-ch--dock", focused === c.hashId && "m-focus")}
                data-pick={c.hashId}
                data-kind="ch"
                style={{ strokeWidth: px(focused === c.hashId ? 9 : 6) }}
              />
            ))}
            {go.map((z) => (
              <path
                key={z.hashId}
                d={pathD(z.polygon ?? [])}
                className={cx("m-go", z.isEnabled === false && "m-go--off", selected?.has(z.hashId) && "m-go--sel", focused === z.hashId && "m-focus")}
                data-pick={z.hashId}
                data-kind="go"
              />
            ))}
            {mowed.length > 0 && <path d={pathD(mowed)} className="m-mowed" clipPath={`url(#${clipId})`} />}
            {props.showTrail !== false &&
              (map.mow_path?.segments ?? []).map((s, i) => <path key={i} d={pathD(s, false)} className="m-trail" />)}
            {props.showTrail !== false && props.trail && props.trail.length > 1 && <path d={pathD(props.trail, false)} className="m-trail m-trail--live" />}
            {nogo.map((z) => (
              <path
                key={z.hashId}
                d={pathD(z.polygon ?? [])}
                className={cx("m-nogo", focused === z.hashId && "m-focus")}
                data-pick={z.hashId}
                data-kind="nogo"
              />
            ))}
            {[...go.map((z, i) => [z, i, "go"] as const), ...nogo.map((z, i) => [z, i, "nogo"] as const), ...channels.map((z, i) => [z, i, "ch"] as const)].map(([z, i, kind]) => {
              const text = label(z, i, kind);
              const poly = z.polygon ?? [];
              if (!text || poly.length < 2) return null;
              if (kind !== "go" && labels === "name" && focused !== z.hashId) return null;
              const p = kind === "ch" ? poly[Math.floor(poly.length / 2)] : labelPoint(poly);
              return (
                <text key={`l-${z.hashId}`} x={p.x} y={-p.y} transform={upright(p)} className={cx("m-label", `m-label--${kind}`)} style={{ fontSize: px(kind === "go" ? 13 : 11), strokeWidth: px(3) }}>
                  {text}
                </text>
              );
            })}
            {stationPos && (
              <g
                transform={`translate(${stationPos.x} ${-stationPos.y}) rotate(${-rotation}) scale(${px(1)})`}
                className={cx("m-station", props.stationMovable && "m-station--movable")}
                data-handle="station"
              >
                {props.stationMovable && <circle r={18} className="m-station__ring" />}
                <circle r={11} />
                <path d="M1.5,-7 L-4,1 L-0.5,1 L-1.5,7 L4,-1 L0.5,-1 Z" className="m-station__bolt" />
              </g>
            )}
            {robot && (
              <g transform={`translate(${robot.x} ${-robot.y}) scale(${px(1)})`} className="m-robot">
                <circle r={13} className="m-robot__halo" />
                <line x1={0} y1={0} x2={Math.cos(map.poseThetaRad ?? 0) * 18} y2={-Math.sin(map.poseThetaRad ?? 0) * 18} />
                <circle r={7} />
              </g>
            )}
            {edit && edit.length > 1 && (
              <g className="m-edit">
                <path d={pathD(edit)} className="m-edit__poly" />
                {edit.map((p, i) => {
                  const q = edit[(i + 1) % edit.length];
                  return <circle key={`m${i}`} cx={(p.x + q.x) / 2} cy={-(p.y + q.y) / 2} r={px(5)} className="m-edit__mid" data-handle={`m:${i}`} />;
                })}
                {edit.map((p, i) => (
                  <circle key={`v${i}`} cx={p.x} cy={-p.y} r={px(i === props.activeVertex ? 9 : 7)} className={cx("m-edit__v", i === props.activeVertex && "m-edit__v--on")} data-handle={`v:${i}`} />
                ))}
              </g>
            )}
          </g>
        </svg>
      )}
      {props.overlay}
      {interactive && vb && (
        <>
          <div className="ly-map__scale" aria-hidden>
            <span style={{ width: scaleM * k }} />
            {scaleM >= 1000 ? `${scaleM / 1000} km` : `${scaleM} m`}
          </div>
          <div className="ly-map__ctrl">
            <button type="button" title="North up" aria-label="Reset rotation" className="ly-map__compass" onClick={() => props.onRotation?.(0)}>
              <svg viewBox="-12 -12 24 24" style={{ transform: `rotate(${rotation}deg)` }}>
                <path d="M0,-10 L5,4 L0,1 L-5,4 Z" className="c-n" />
                <text y={10.5}>N</text>
              </svg>
            </button>
            {props.onRotation && (
              <>
                <button type="button" className="ly-map__rot" title="Rotate left" aria-label="Rotate left" onClick={() => props.onRotation!((rotation - 15 + 360) % 360)}>
                  <Icon name="mdi:rotate-left" size={18} />
                </button>
                <button type="button" className="ly-map__rot" title="Rotate right" aria-label="Rotate right" onClick={() => props.onRotation!((rotation + 15) % 360)}>
                  <Icon name="mdi:rotate-right" size={18} />
                </button>
              </>
            )}
            <button type="button" title="Zoom in" aria-label="Zoom in" onClick={() => vb && zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 1 / 1.4)}>
              <Icon name="mdi:plus" size={18} />
            </button>
            <button type="button" title="Zoom out" aria-label="Zoom out" onClick={() => vb && zoomAt({ x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 }, 1.4)}>
              <Icon name="mdi:minus" size={18} />
            </button>
            <button type="button" title="Fit lawn" aria-label="Fit lawn" onClick={fit}>
              <Icon name="mdi:fit-to-screen-outline" size={18} />
            </button>
          </div>
        </>
      )}
    </div>
  );
}
