import { useEffect, useMemo, useRef, useState } from "react";
import type { MapData, Point, Zone } from "../hass";
import { area, simplify } from "../map/geometry";
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
      localStorage.setItem(key, JSON.stringify(n));
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
    if (mowing) setTrail([]);
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
  const [editPts, setEditPts] = useState<Point[] | null>(null);
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

  const startEditShape = (f: Focus) => {
    setFocus(f);
    setVertex(null);
    setDirty(false);
    const z = find(f);
    setEditPts(f.kind !== "ch" && z?.polygon ? simplify(z.polygon, MAX_HANDLES) : null);
    setSheetOpen(true);
  };
  const leaveFocus = () => {
    setFocus(null);
    setEditPts(null);
    setVertex(null);
    setDirty(false);
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

  // Keyboard: Esc steps back, Delete removes the selected vertex, E enters edit mode.
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    const t = e.composedPath()[0] as HTMLElement;
    if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;
    if (e.key === "Escape") focus ? leaveFocus() : mode === "edit" ? exitEdit() : setSelected(new Set());
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
        <span className="ly-spinner" /> Loading map…
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
      if (dirty && focus && focus.id !== id) return ui.toast("Save or discard the current shape first", "bad");
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
            dirty={dirty}
            vertex={vertex}
            onDeleteVertex={deleteVertex}
            onReset={() => startEditShape(focus)}
            onSaved={() => setDirty(false)}
            onClose={leaveFocus}
          />
        ) : (
          <div className="ly-sheet__body">
            <h2 className="ly-sheet__title">
              <Icon name="mdi:pencil-ruler" /> Edit map
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
        <Icon name="mdi:texture-box" /> Zones
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
            if (await call("lymow", "start_zone", { zone_hash_ids: sel }, `Mowing ${sel.length} zone${sel.length > 1 ? "s" : ""}`)) p.setSelected(new Set());
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
            <i className="lg-go" /> Zone
          </li>
          <li>
            <i className="lg-off" /> Disabled zone
          </li>
          <li>
            <i className="lg-nogo" /> No-go area
          </li>
          <li>
            <i className="lg-ch" /> Channel
          </li>
          <li>
            <i className="lg-mowed" /> Mowed
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
  dirty: boolean;
  vertex: number | null;
  onDeleteVertex: () => void;
  onReset: () => void;
  onSaved: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const { call } = useMower();
  const ui = useUi();
  const { focus, zone } = p;
  const texts = {
    go: { rename: t("Rename zone"), removed: t("The zone is removed from the mower's map. You can bring it back by restoring a map backup.") },
    nogo: { rename: t("Rename no-go area"), removed: t("The no-go area is removed from the mower's map. You can bring it back by restoring a map backup.") },
    ch: { rename: t("Rename channel"), removed: t("The channel is removed from the mower's map. You can bring it back by restoring a map backup.") },
  }[focus.kind];
  const title = focus.kind === "go" ? zoneLabel(zone, p.index, t) : zone.name?.trim() || t(focus.kind === "nogo" ? t("No-go area") : t("Channel"));
  const key = focus.kind === "go" ? "zone_hash_id" : focus.kind === "nogo" ? "nogo_hash_id" : "channel_hash_id";
  const svc = focus.kind === "go" ? "zone" : focus.kind === "nogo" ? "nogo_zone" : "channel";

  return (
    <div className="ly-sheet__body">
      <h2 className="ly-sheet__title">
        <Button variant="ghost" icon="mdi:arrow-left" title={t("Back")} onClick={p.onClose} />
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
            {t("Drag the points to reshape. Tap a small dot on an edge to add a point. Select a point and press Delete to remove it. The outline is simplified to {n} points for editing.", { n: p.editPts.length })}
          </p>
          <div className="ly-btnrow">
            <Button
              variant="primary"
              icon="mdi:content-save-outline"
              disabled={!p.dirty}
              onClick={async () => {
                const polygon = p.editPts!.map((q) => ({ x: +q.x.toFixed(4), y: +q.y.toFixed(4) }));
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
        </section>
      )}

      {focus.kind === "go" && <ZoneSettings key={zone.hashId} zone={zone} />}
    </div>
  );
}

function ZoneSettings({ zone }: { zone: Zone }) {
  const t = useT();
  const { call } = useMower();
  const cfg = zone.zoneConfig ?? {};
  const [cut, setCut] = useState<number>(zone.cutHeight ?? cfg.cutHeight ?? 40);
  const [speed, setSpeed] = useState<number>(cfg.moveSpeed ?? 0.5);
  const [spacing, setSpacing] = useState<number>(zone.pathSpacing ?? cfg.pathSpacing ?? 30);
  const [laps, setLaps] = useState<number>(cfg.perimeterMowLaps ?? 1);
  const initial = useMemo(() => JSON.stringify([cut, speed, spacing, laps]), [zone.hashId]);
  const changed = JSON.stringify([cut, speed, spacing, laps]) !== initial;
  return (
    <section className="ly-subsection">
      <h3>{t("Mowing settings for this zone")}</h3>
      <Field label={t("Cutting height")}>
        <Slider value={cut} min={20} max={100} step={5} unit="mm" onChange={setCut} />
      </Field>
      <Field label={t("Speed")}>
        <Slider value={speed} min={0.1} max={1.5} step={0.05} unit="m/s" format={(v) => v.toFixed(2)} onChange={setSpeed} />
      </Field>
      <Field label={t("Path spacing")}>
        <Slider value={spacing} min={20} max={40} step={1} unit="cm" onChange={setSpacing} />
      </Field>
      <Field label={t("Perimeter laps")}>
        <Slider value={laps} min={0} max={3} onChange={setLaps} />
      </Field>
      <Button
        variant="primary"
        icon="mdi:check"
        disabled={!changed}
        onClick={() =>
          call(
            "lymow",
            "set_zone_config",
            { zone_hash_id: zone.hashId, cut_height: cut, move_speed: speed, path_spacing: spacing, perimeter_mow_laps: laps },
            t("Zone settings applied"),
          )
        }
      >
        {t("Apply zone settings")}
      </Button>
    </section>
  );
}
