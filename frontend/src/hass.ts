// Bridge between Home Assistant's `hass` object and React.
//
// HA hands the panel a new `hass` object on every state change anywhere in the
// instance. Rather than re-rendering the whole app each time, components read
// through useSyncExternalStore selectors (useEntity, useHass) so only the ones
// whose entity object actually changed re-render.

import { createContext, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
  last_changed: string;
  last_updated: string;
}

export interface Hass {
  states: Record<string, HassEntity>;
  entities?: Record<string, { entity_id: string; device_id?: string; platform: string; entity_category?: string | null; hidden?: boolean }>;
  themes?: { darkMode?: boolean };
  language: string;
  config: { time_zone: string };
  user?: { is_admin: boolean };
  callService(domain: string, service: string, data?: Record<string, unknown>, target?: unknown, notifyOnError?: boolean, returnResponse?: boolean): Promise<any>;
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  connection: {
    subscribeMessage<T>(cb: (msg: T) => void, msg: Record<string, unknown>): Promise<() => Promise<void>>;
  };
  hassUrl(path?: string): string;
}

type Listener = () => void;

export class HassStore {
  hass: Hass | undefined;
  private listeners = new Set<Listener>();

  set(hass: Hass): void {
    this.hass = hass;
    this.listeners.forEach((l) => l());
  }

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
}

export const StoreContext = createContext<HassStore | null>(null);

function useStore(): HassStore {
  const store = useContext(StoreContext);
  if (!store) throw new Error("HassStore missing");
  return store;
}

/** The current hass object; re-renders on every hass update (use sparingly). */
export function useHass(): Hass {
  const store = useStore();
  return useSyncExternalStore(store.subscribe, () => store.hass) as Hass;
}

/** HA's UI language; re-renders only when it changes. */
export function useHassLanguage(): string {
  const store = useStore();
  return useSyncExternalStore(store.subscribe, () => store.hass?.language ?? "en");
}

/** Stable accessor for actions — never triggers a re-render. */
export function useHassRef(): () => Hass {
  const store = useStore();
  return () => store.hass as Hass;
}

/** One entity's state object; re-renders only when that entity changes. */
export function useEntity(entityId: string | undefined): HassEntity | undefined {
  const store = useStore();
  return useSyncExternalStore(store.subscribe, () => (entityId ? store.hass?.states[entityId] : undefined));
}

// ── Lymow websocket API ─────────────────────────────────────────────────────

export interface LymowDevice {
  entry_id: string;
  thing: string;
  device_id: string | null;
  name: string;
  entities: Record<string, string>;
  /** Missing from the latest discovery (entry reloading): kept on screen, but not controllable. */
  held?: boolean;
}

export interface Point {
  x: number;
  y: number;
}

export interface Zone {
  hashId: string;
  name?: string;
  polygon?: Point[];
  isEnabled?: boolean;
  area?: number;
  cutHeight?: number;
  pathSpacing?: number;
  zoneConfig?: Record<string, any>;
  isDockingChannel?: boolean;
}

export interface MapData {
  go_zones?: Zone[];
  nogo_zones?: Zone[];
  channels?: Zone[];
  charging_station?: { x: number; y: number; theta?: number };
  mowing_settings?: Record<string, any>;
  mow_path?: { segments: Point[][] };
  poseEastM?: number;
  poseNorthM?: number;
  poseThetaRad?: number;
  rtkEastM?: number;
  rtkNorthM?: number;
  rtkStatus?: number;
  rtkLabel?: string;
  workStatus?: number;
  mowProgress?: number;
  totalTaskAreaM2?: number;
}

export interface Schedule {
  id: number;
  hour: number;
  minute: number;
  dayOfWeek?: number[];
  zones?: string[];
  isRepeated?: boolean;
  isDisabled?: boolean;
}

export interface Backup {
  file: string;
  name?: string;
  backupTime?: number;
  preview?: { goZones?: Zone[]; nogoZones?: Zone[]; channels?: Zone[] };
}

export interface Snapshot {
  thing: string;
  run_time_config?: { cutHeight?: number; moveSpeed?: number; cutSpeed?: number };
  /** Set when the mower's config entry was unloaded; the stream ends. */
  gone?: boolean;
  /** Set when the user lost read access to the mower; the stream ends. */
  unauthorized?: boolean;
  map: MapData;
  /** null until the mower has answered the schedule query. */
  schedules: Schedule[] | null;
  backups: Backup[];
  online: boolean;
}

const EMPTY_GRACE_MS = 60000;

export function useDevices(): [LymowDevice[] | undefined, () => void] {
  const getHass = useHassRef();
  const [devices, setDevices] = useState<LymowDevice[]>();
  const [generation, setGeneration] = useState(0);
  // Survive reload(): the `gone` refresh is exactly when the grace matters.
  const known = useRef<LymowDevice[]>([]);
  const missingSince = useRef(new Map<string, number>()).current;
  useEffect(() => {
    let alive = true;
    let t = 0;
    // Entities register a moment after the entry loads, and a reload can leave the
    // list empty for a while: poll fast while settling, slowly otherwise.
    const load = (): Promise<void> =>
      getHass()
        .callWS<LymowDevice[]>({ type: "lymow/devices" })
        .then(
          (d) => d,
          () => null, // transport hiccup: keep what we have, try again
        )
        .then((d) => {
          if (!alive) return;
          if (d === null) {
            t = window.setTimeout(load, 5000);
            setDevices((prev) => prev ?? []);
            return;
          }
          // A config-entry reload briefly drops its mowers from the reply; keep each
          // previously known one (and with it any open view and draft) for a grace
          // period instead of unmounting, and keep polling until it is back.
          const now = Date.now();
          const seen = new Set(d.map((x) => x.thing));
          for (const k of [...missingSince.keys()]) if (seen.has(k)) missingSince.delete(k);
          const kept = known.current.filter((x) => {
            if (seen.has(x.thing)) return false;
            if (!missingSince.has(x.thing)) missingSince.set(x.thing, now);
            return now - missingSince.get(x.thing)! <= EMPTY_GRACE_MS;
          });
          known.current = [...d, ...kept.map((x) => ({ ...x, held: true }))];
          setDevices(known.current);
          // Also keep polling until every mower's own entity is registered (setup
          // publishes the coordinator before the platforms finish).
          // Otherwise keep a slow poll, so a mower removed (or no longer permitted)
          // without an open stream still leaves the picker.
          const settling = kept.length || !d.length || d.some((x) => !x.entities.mower);
          t = window.setTimeout(load, settling ? 5000 : 60000);
        });
    load();
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [generation]);
  return [devices, () => setGeneration((g) => g + 1)];
}

export function useSnapshot(thing: string | undefined): Snapshot | undefined {
  const getHass = useHassRef();
  const [snap, setSnap] = useState<Snapshot>();
  // Bumped to resubscribe: after the mower's entry reloads (the old stream ends
  // with `gone`) or while it isn't set up yet (subscribe fails with not_found).
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!thing) return;
    let unsub: (() => Promise<void>) | undefined;
    let alive = true;
    let retry = 0;
    const again = () => {
      if (alive) retry = window.setTimeout(() => setAttempt((a) => a + 1), Math.min(30000, 2000 * 2 ** Math.min(attempt, 4)) /* capped backoff */);
    };
    getHass()
      .connection.subscribeMessage<Snapshot>(
        (s) => {
          if (!alive) return;
          setSnap(s);
          if (s.gone) again();
        },
        { type: "lymow/subscribe", thing },
      )
      .then((u) => {
        if (alive) unsub = u;
        else u();
      })
      .catch(again);
    return () => {
      alive = false;
      window.clearTimeout(retry);
      unsub?.();
    };
  }, [thing, attempt]);
  // Never show (or act on) the previous mower's map/backups while switching.
  useEffect(() => setSnap(undefined), [thing]);
  // The reset above runs after the first render for a new mower; never hand that
  // render the previous mower's snapshot.
  return snap?.thing === thing ? snap : undefined;
}

/** Fire an HA frontend event (more-info dialog, sidebar toggle) from inside the shadow DOM. */
export function fireHassEvent(el: EventTarget | null, type: string, detail: unknown = {}): void {
  el?.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
}
