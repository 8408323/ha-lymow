import { useEffect, useRef, useState } from "react";
import { setLeaveGuard } from "../App";
import type { MapData, Point, Zone } from "../hass";
import { area, expand, simplify, type Handle } from "../map/geometry";
import { MapCanvas, type Kind, type LabelMode } from "../map/MapCanvas";
import { useT } from "../i18n";
import { useMower, useMowerEntity, zoneLabel } from "../mower";
import { MOWING_WORK_STATUS, RTK, WORK_STATUS, num } from "../status";
import { Badge, Button, Empty, Field, Icon, Segmented, Slider, Toggle, cx, useUi } from "../ui";

const MAX_HANDLES = 40;

function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  return [
    v,
    (n: T) => {
      setV(n);
      try {
        localStorage.setItem(key, JSON.stringify(n));
      } catch {
        // storage blocked or full: keep it for this session only
      }
    },
  ];
}

/** Client-side trail of the robot's pose during the current mow (complements the server trail). */
function useLiveTrail(map: MapData | undefined): Point[] {
  const [trail, setTrail] = useState<Point[]>([]);
  const mowing = map?.workStatus !== undefined && MOWING_WORK_STATUS.has(map.workStatus);
  useEffect(() => {
    if (!mowing) return;
    const x = map?.poseEastM;
    const y = map?.poseNorthM;
    if (x === undefined || y === undefined) return;
    setTrail((t) => {
      const last = t[t.length - 1];
      if (last && Math.hypot(last.x - x, last.y - y) < 0.05) return t;
      return [...t, { x, y }].slice(-2000);
    });
  }, [map?.poseEastM, map?.poseNorthM, mowing]);
  useEffect(() => {
    // A new mow starts a fresh breadcrumb; when it ends, drop it (the server trail stays).
    setTrail([]);
  }, [mowing]);
  return trail;
}

interface Focus {
  kind: Kind;
  id: string;
}

export function MapView() {
  const t = useT();
  const { snap, call } = useMower();
  const ui = useUi();
  const map = snap?.map;
  const [mode, setMode] = useState<"browse" | "edit">("browse");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<Focus | null>(null);
  const [editPts, setEditPts] = useState<Handle[] | null>(null);
  const [editBase, setEditBase] = useState<{ orig: Point[]; initial: Handle[] } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [vertex, setVertex] = useState<number | null>(null);
  const [rotation, setRotation] = useStored("lymow_rotation", 0);
  const [labels, setLabels] = useStored<LabelMode>("lymow_labels", "name");
  const [showTrail, setShowTrail] = useStored("lymow_trail", true);
  const [sheetOpen, setSheetOpen] = useState(true);
  const trail = useLiveTrail(map);

  const go = map?.go_zones ?? [];
  const nogo = map?.nogo_zones ?? [];
  const channels = map?.channels ?? [];
  const find = (f: Focus | null): Zone | undefined =>
    f ? (f.kind === "go" ? go : f.kind === "nogo" ? nogo : channels).find((z) => z.hashId === f.id) : undefined;
  const focused = find(focus);
  const outline = editPts && editBase ? expand(editPts, editBase.orig, editBase.initial) : editPts;

  const startEditShape = (f: Focus) => {
    setFocus(f);
    setVertex(null);
    setDirty(false);
    const z = find(f);
    const handles = f.kind !== "ch" && z?.polygon ? simplify(z.polygon, MAX_HANDLES) : null;
    setEditPts(handles);
    setEditBase(handles && z?.polygon ? { orig: z.polygon, initial: handles } : null);
    setSheetOpen(true);
  };
  const leaveFocus = () => {
    setFocus(null);
    setEditPts(null);
    setVertex(null);
    setDirty(false);
  };
  // Back / Escape must not silently throw away a reshaped polygon.
  const requestLeave = async () => {
    if (guarded && !(await ui.confirm({ title: t("Discard your changes to this shape?"), confirm: t("Discard"), danger: true }))) return;
    leaveFocus();
  };
  const exitEdit = () => {
    leaveFocus();
    setMode("browse");
  };

  const deleteVertex = () => {
    if (editPts && vertex !== null && editPts.length > 3) {
      setEditPts(editPts.filter((_, i) => i !== vertex));
      setVertex(null);
      setDirty(true);
    }
  };

  // After Save, wait for the mower's map reply before treating the shape as stored:
  // refresh the editor from the reported outline, or offer Save again if none comes.
  const [awaitShape, setAwaitShape] = useState<string | null>(null);
  // Until the mower confirms a saved shape the draft is the only copy, so keep it guarded.
  const guarded = dirty || awaitShape !== null;
  const reportedShape = JSON.stringify(find(focus)?.polygon ?? null);
  useEffect(() => {
    if (awaitShape !== null && focus && reportedShape !== awaitShape) {
      setAwaitShape(null);
      startEditShape(focus);
    }
  }, [reportedShape]);
  useEffect(() => {
    if (awaitShape === null) return;
    const id = window.setTimeout(() => {
      setAwaitShape(null);
      setDirty(true);
      ui.toast(t("The mower hasn't confirmed the new shape yet. Save again to retry."), "bad");
    }, 20000);
    return () => window.clearTimeout(id);
  }, [awaitShape]);

  // Switching tabs would unmount the editor; ask first while a shape is dirty.
  useEffect(() => {
    setLeaveGuard(guarded ? () => ui.confirm({ title: t("Discard your changes to this shape?"), confirm: t("Discard"), danger: true }) : null);
    return () => setLeaveGuard(null);
  }, [guarded]);

  // Keyboard: Esc steps back, Delete removes the selected vertex, E enters edit mode.
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    const t = e.composedPath()[0] as HTMLElement;
    if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;
    if (e.key === "Escape") focus ? requestLeave() : mode === "edit" ? exitEdit() : setSelected(new Set());
    else if ((e.key === "Delete" || e.key === "Backspace") && vertex !== null) deleteVertex();
    else if (e.key === "e" && mode === "browse") setMode("edit");
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (!map) {
    return (
      <div className="ly-loading">
        <span className="ly-spinner" /> {t("Loading map…")}
      </div>
    );
  }
  if (!go.length && !map.charging_station) {
    return (
      <Empty icon="mdi:map-search-outline" title={t("No map yet")}>
        {t("The mower hasn't sent its map yet. Use the refresh button at the top to ask for it.")}
      </Empty>
    );
  }

  const onPick = (kind: Kind, id: string) => {
    if (mode === "edit") {
      if (dirty && focus && focus.id !== id) return ui.toast(t("Save or discard the current shape first"), "bad");
      startEditShape({ kind, id });
      return;
    }
    if (kind !== "go") return;
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
    setSheetOpen(true);
  };

  return (
    <div className={cx("ly-mapview", !sheetOpen && "ly-mapview--collapsed")}>
      <MapCanvas
        map={map}
        selected={mode === "browse" ? selected : undefined}
        focused={focus?.id}
        edit={editPts}
        editOutline={outline}
        activeVertex={vertex}
        onVertex={setVertex}
        onEditChange={(p) => {
          setEditPts(p);
          setDirty(true);
        }}
        onPick={onPick}
        onBackground={() => (mode === "edit" ? !dirty && leaveFocus() : setSelected(new Set()))}
        stationMovable={mode === "edit" && !focus}
        onStationMoved={async (p) => {
          const ok = await ui.confirm({
            title: t("Move the charging station?"),
            body: t("The mower will use this spot as its dock from now on. Only do this if you physically moved the station."),
            confirm: t("Move station"),
          });
          if (ok) await call("lymow", "move_charging_station", { x: +p.x.toFixed(3), y: +p.y.toFixed(3) }, t("Charging station moved"));
        }}
        trail={trail}
        labels={labels}
        showTrail={showTrail}
        rotation={rotation}
        onRotation={setRotation}
        overlay={<MapStatus map={map} />}
        className="ly-mapview__canvas"
      />
      <aside className="ly-sheet" aria-label={mode === "edit" ? t("Map editor") : t("Zones")}>
        <button type="button" className="ly-sheet__grip" onClick={() => setSheetOpen(!sheetOpen)} aria-label={sheetOpen ? t("Collapse panel") : t("Expand panel")}>
          <span />
        </button>
        {mode === "browse" ? (
          <BrowsePanel
            map={map}
            selected={selected}
            setSelected={setSelected}
            onEdit={() => {
              setSelected(new Set());
              setMode("edit");
            }}
            labels={labels}
            setLabels={setLabels}
            showTrail={showTrail}
            setShowTrail={setShowTrail}
          />
        ) : focus && focused ? (
          <EditPanel
            focus={focus}
            zone={focused}
            index={(focus.kind === "go" ? go : focus.kind === "nogo" ? nogo : channels).indexOf(focused)}
            editPts={editPts}
            outline={outline}
            dirty={dirty}
            vertex={vertex}
            onDeleteVertex={deleteVertex}
            onReset={() => startEditShape(focus)}
            awaiting={awaitShape !== null}
            onSaved={() => {
              setAwaitShape(reportedShape);
              setDirty(false);
            }}
            onBack={requestLeave}
            onClose={leaveFocus}
          />
        ) : (
          <div className="ly-sheet__body">
            <h2 className="ly-sheet__title">
              <Icon name="mdi:pencil-ruler" /> {t("Edit map")}
            </h2>
            <ol className="ly-steps">
              <li>{t("Tap a zone, no-go area or channel to edit it.")}</li>
              <li>{t("Drag the white points to reshape, tap the small dots on an edge to add a point.")}</li>
              <li>{t("Drag the charging station ⚡ to move it.")}</li>
            </ol>
            <p className="ly-muted">{t("New zones are created by driving the mower around them in the Lymow app; the robot can't create them from a drawing.")}</p>
            <Button variant="primary" icon="mdi:check" block onClick={exitEdit}>
              {t("Done editing")}
            </Button>
          </div>
        )}
      </aside>
    </div>
  );
}

function MapStatus({ map }: { map: MapData }) {
  const t = useT();
  const battery = num(useMowerEntity("battery")?.state);
  const ws = map.workStatus;
  const rtk = map.rtkStatus !== undefined ? RTK[map.rtkStatus] : undefined;
  const mowing = ws !== undefined && MOWING_WORK_STATUS.has(ws);
  return (
    <div className="ly-map__status">
      {ws !== undefined && (
        <Badge tone={mowing ? "good" : ws === 7 || ws === 13 ? "bad" : ws === 3 || ws === 10 ? "warn" : "info"} icon="mdi:robot-mower">
          {WORK_STATUS[ws] ? t(WORK_STATUS[ws]) : t("Status {n}", { n: ws })}
        </Badge>
      )}
      {mowing && map.mowProgress !== undefined && <Badge icon="mdi:grass">{Math.round(map.mowProgress)}%</Badge>}
      {battery !== undefined && <Badge icon="mdi:battery">{Math.round(battery)}%</Badge>}
      {rtk && (
        <Badge tone={rtk.tone} icon="mdi:satellite-variant">
          {t(rtk.label)}
        </Badge>
      )}
    </div>
  );
}

function BrowsePanel(p: {
  map: MapData;
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  onEdit: () => void;
  labels: LabelMode;
  setLabels: (l: LabelMode) => void;
  showTrail: boolean;
  setShowTrail: (v: boolean) => void;
}) {
  const t = useT();
  const { call } = useMower();
  const ui = useUi();
  const zones = p.map.go_zones ?? [];
  const sel = [...p.selected];
  const toggle = (id: string) => {
    const next = new Set(p.selected);
    next.has(id) ? next.delete(id) : next.add(id);
    p.setSelected(next);
  };
  return (
    <div className="ly-sheet__body">
      <h2 className="ly-sheet__title">
        <Icon name="mdi:texture-box" /> {t("Zones")}
        <span className="ly-muted">{zones.length}</span>
      </h2>
      <p className="ly-muted">{t("Tap zones on the map or in the list to choose what to mow.")}</p>
      <ul className="ly-zones">
        {zones.map((z, i) => (
          <li key={z.hashId} className={cx("ly-zone", p.selected.has(z.hashId) && "ly-zone--sel", z.isEnabled === false && "ly-zone--off")}>
            <button type="button" className="ly-zone__pick" onClick={() => toggle(z.hashId)} aria-pressed={p.selected.has(z.hashId)}>
              <span className="ly-check">{p.selected.has(z.hashId) && <Icon name="mdi:check" size={14} />}</span>
              <span className="ly-zone__name">{zoneLabel(z, i, t)}</span>
              <span className="ly-zone__meta">
                {Math.round(z.area ?? area(z.polygon ?? []))} m² · {z.cutHeight ?? z.zoneConfig?.cutHeight ?? "–"} mm
              </span>
            </button>
            <Toggle
              label={`${zoneLabel(z, i, t)} enabled`}
              checked={z.isEnabled !== false}
              onChange={(v) => call("lymow", "set_zone_enabled", { zone_hash_id: z.hashId, is_enabled: v }, v ? t("Zone enabled") : t("Zone disabled"))}
            />
          </li>
        ))}
      </ul>
      <div className="ly-stack">
        <Button
          variant="primary"
          icon="mdi:play"
          block
          disabled={!sel.length}
          onClick={async () => {
            if (await call("lymow", "start_zone", { zone_hash_ids: sel }, t("Mowing {n} zones", { n: sel.length }))) p.setSelected(new Set());
          }}
        >
          {sel.length ? t("Mow {n} selected", { n: sel.length }) : t("Select zones to mow")}
        </Button>
        {sel.length >= 2 && (
          <Button
            icon="mdi:vector-union"
            block
            onClick={async () => {
              const ok = await ui.confirm({
                title: t("Merge {n} zones?", { n: sel.length }),
                body: t("They become one zone with a new ID. Schedules or per-zone settings that pointed at the old zones need to be set up again."),
                confirm: t("Merge"),
              });
              if (ok && (await call("lymow", "merge_zones", { zone_hash_ids: sel }, t("Zones merged")))) p.setSelected(new Set());
            }}
          >
            {t("Merge selected")}
          </Button>
        )}
        <Button icon="mdi:pencil-ruler" block onClick={p.onEdit}>
          {t("Edit map")}
        </Button>
      </div>
      <details className="ly-details">
        <summary>{t("Display")}</summary>
        <Field label={t("Labels")}>
          <Segmented
            value={p.labels}
            onChange={p.setLabels}
            options={[
              { value: "name", label: t("Name") },
              { value: "area", label: t("Area") },
              { value: "both", label: t("Both") },
              { value: "none", label: t("None") },
            ]}
          />
        </Field>
        <div className="ly-row">
          <span>{t("Show mowing trail")}</span>
          <Toggle checked={p.showTrail} onChange={p.setShowTrail} label={t("Show mowing trail")} />
        </div>
        <ul className="ly-legend">
          <li>
            <i className="lg-go" /> {t("Zone")}
          </li>
          <li>
            <i className="lg-off" /> {t("Disabled zone")}
          </li>
          <li>
            <i className="lg-nogo" /> {t("No-go area")}
          </li>
          <li>
            <i className="lg-ch" /> {t("Channel")}
          </li>
          <li>
            <i className="lg-mowed" /> {t("Mowed")}
          </li>
        </ul>
      </details>
    </div>
  );
}

function EditPanel(p: {
  focus: Focus;
  zone: Zone;
  index: number;
  editPts: Point[] | null;
  outline: Point[] | null;
  dirty: boolean;
  vertex: number | null;
  onDeleteVertex: () => void;
  onReset: () => void;
  onSaved: () => void;
  awaiting: boolean;
  onBack: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const { call, snap } = useMower();
  const ui = useUi();
  const { focus, zone } = p;
  const texts = {
    go: { rename: t("Rename zone"), removed: t("The zone is removed from the mower's map. Restoring a map backup can bring it back, but its no-go areas may not return.") },
    nogo: { rename: t("Rename no-go area"), removed: t("The no-go area is removed from the mower's map. Restoring a map backup doesn't always bring no-go areas back, so only delete it if you're sure.") },
    ch: { rename: t("Rename channel"), removed: t("The channel is removed from the mower's map. You can bring it back by restoring a map backup.") },
  }[focus.kind];
  const title = focus.kind === "go" ? zoneLabel(zone, p.index, t) : zone.name?.trim() || (focus.kind === "nogo" ? t("No-go area") : t("Channel"));
  const key = focus.kind === "go" ? "zone_hash_id" : focus.kind === "nogo" ? "nogo_hash_id" : "channel_hash_id";
  const svc = focus.kind === "go" ? "zone" : focus.kind === "nogo" ? "nogo_zone" : "channel";

  return (
    <div className="ly-sheet__body">
      <h2 className="ly-sheet__title">
        <Button variant="ghost" icon="mdi:arrow-left" title={t("Back")} onClick={p.onBack} />
        {title}
      </h2>
      <div className="ly-btnrow">
        <Button
          icon="mdi:rename-outline"
          onClick={async () => {
            const name = await ui.prompt({ title: texts.rename, label: t("Name"), value: zone.name ?? "", placeholder: title, maxLength: 40 });
            if (name) await call("lymow", `rename_${svc}`, { [key]: zone.hashId, name }, t("Renamed"));
          }}
        >
          {t("Rename")}
        </Button>
        <Button
          variant="danger"
          icon="mdi:delete-outline"
          onClick={async () => {
            const ok = await ui.confirm({
              title: t("Delete {name}?", { name: title }),
              body: texts.removed,
              confirm: t("Delete"),
              danger: true,
            });
            if (ok && (await call("lymow", `delete_${svc}`, { [key]: zone.hashId }, t("Deleted")))) p.onClose();
          }}
        >
          {t("Delete")}
        </Button>
      </div>

      {p.editPts && (
        <section className="ly-subsection">
          <h3>{t("Shape")}</h3>
          <p className="ly-muted">
            {t("Drag the points to reshape. Tap a small dot on an edge to add a point. Select a point and press Delete to remove it. Edges you don't touch keep their full detail.")}
          </p>
          <div className="ly-btnrow">
            <Button
              variant="primary"
              icon="mdi:content-save-outline"
              disabled={!p.dirty}
              onClick={async () => {
                const polygon = p.outline!.map((q) => ({ x: +q.x.toFixed(4), y: +q.y.toFixed(4) }));
                const ok = await call("lymow", focus.kind === "go" ? "update_zone_polygon" : "update_nogo_polygon", { [key]: zone.hashId, polygon }, t("Shape saved"));
                if (ok) p.onSaved();
              }}
            >
              {t("Save shape")}
            </Button>
            <Button icon="mdi:undo" disabled={!p.dirty} onClick={p.onReset}>
              {t("Discard")}
            </Button>
            <Button icon="mdi:vector-point-minus" disabled={p.vertex === null || p.editPts.length <= 3} onClick={p.onDeleteVertex}>
              {t("Delete point")}
            </Button>
          </div>
          {p.awaiting && (
            <p className="ly-muted">
              <span className="ly-spinner" /> {t("Waiting for the mower to confirm the new shape…")}
            </p>
          )}
        </section>
      )}

      {focus.kind === "go" && <ZoneSettings key={zone.hashId} zone={zone} global={snap?.map.mowing_settings} />}
    </div>
  );
}

const ZONE_FIELDS = [
  { key: "cut_height", src: "cutHeight", label: "Cutting height", min: 20, max: 100, step: 5, unit: "mm", fallback: 40 },
  { key: "move_speed", src: "moveSpeed", label: "Speed", min: 0.1, max: 1.5, step: 0.05, unit: "m/s", fallback: 0.5 },
  { key: "path_spacing", src: "pathSpacing", label: "Path spacing", min: 20, max: 40, step: 1, unit: "cm", fallback: 30 },
  { key: "perimeter_mow_laps", src: "perimeterMowLaps", label: "Perimeter laps", min: 0, max: 3, step: 1, unit: undefined, fallback: 1 },
] as const;

const ZONE_TOGGLES = [
  { key: "safe_margin_mode", src: "safeMarginMode", label: "Keep a safety margin from edges" },
  { key: "turn_off_outer_motor", src: "turnOffOuterMotor", label: "Turn off outer blade at edges" },
] as const;

function ZoneSettings({ zone, global }: { zone: Zone; global: Record<string, any> | undefined }) {
  const t = useT();
  const { call } = useMower();
  // Effective value: the zone's own config, else the global default the mower uses.
  const effective = (src: string, fallback: number): number => {
    const own = src === "cutHeight" ? zone.cutHeight ?? zone.zoneConfig?.cutHeight : src === "pathSpacing" ? zone.pathSpacing ?? zone.zoneConfig?.pathSpacing : zone.zoneConfig?.[src];
    return typeof own === "number" ? own : typeof global?.[src] === "number" ? global[src] : fallback;
  };
  // Only fields the user moved are sent, so untouched ones keep inheriting.
  const [draft, setDraft] = useState<Record<string, number | boolean>>({});
  const [saved, setSaved] = useState(false);
  const changed = Object.keys(draft).length > 0;
  // Keep showing what was applied until the map reply carries it, then drop the draft.
  const flag = (src: string): boolean => Boolean(zone.zoneConfig?.[src] ?? global?.[src] ?? false);
  const reported = [...ZONE_FIELDS.map((f) => effective(f.src, f.fallback)), ...ZONE_TOGGLES.map((f) => flag(f.src))].join("|");
  useEffect(() => {
    const sliders = ZONE_FIELDS.every((f) => !(f.key in draft) || Math.abs((draft[f.key] as number) - effective(f.src, f.fallback)) < 1e-6);
    const toggles = ZONE_TOGGLES.every((f) => !(f.key in draft) || draft[f.key] === flag(f.src));
    if (saved && sliders && toggles) {
      setDraft({});
      setSaved(false);
    }
  }, [reported]);
  return (
    <section className="ly-subsection">
      <h3>{t("Mowing settings for this zone")}</h3>
      {ZONE_FIELDS.map((f) => (
        <Field key={f.key} label={t(f.label)}>
          <Slider
            value={(draft[f.key] as number | undefined) ?? effective(f.src, f.fallback)}
            min={f.min}
            max={f.max}
            step={f.step}
            unit={f.unit}
            format={f.key === "move_speed" ? (v) => v.toFixed(2) : undefined}
            onChange={(v) => {
              setSaved(false);
              setDraft({ ...draft, [f.key]: v });
            }}
          />
        </Field>
      ))}
      {ZONE_TOGGLES.map((f) => (
        <div className="ly-row" key={f.key}>
          <span>{t(f.label)}</span>
          <Toggle
            label={t(f.label)}
            checked={(draft[f.key] as boolean | undefined) ?? flag(f.src)}
            onChange={(v) => {
              setSaved(false);
              setDraft({ ...draft, [f.key]: v });
            }}
          />
        </div>
      ))}
      <Button
        variant="primary"
        icon="mdi:check"
        disabled={!changed || saved}
        onClick={async () => {
          if (await call("lymow", "set_zone_config", { zone_hash_id: zone.hashId, ...draft }, t("Zone settings applied"))) setSaved(true);
        }}
      >
        {t("Apply zone settings")}
      </Button>
    </section>
  );
}
