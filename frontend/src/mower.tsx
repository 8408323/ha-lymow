import { createContext, useContext, type ReactNode } from "react";
import { useEntity, useHassRef, type HassEntity, type LymowDevice, type Snapshot, type Zone } from "./hass";
import { useT, type T } from "./i18n";
import { useUi } from "./ui";

interface MowerApi {
  device: LymowDevice;
  snap: Snapshot | undefined;
  /** entity_id for a unique-id key (e.g. "battery"), if the entity exists. */
  ent(key: string): string | undefined;
  /**
   * Call a service, targeting the mower via entity_id unless `entity_id` is given.
   * Shows a toast with `success` on success and the error message on failure.
   * Resolves true on success, false on failure (never throws).
   */
  call(domain: string, service: string, data?: Record<string, unknown>, success?: string): Promise<boolean>;
  /** Same as call() but returns the service response (start_video_session). */
  callWithResponse<T>(domain: string, service: string, data?: Record<string, unknown>): Promise<T>;
  zoneName(hashId: string): string;
}

const MowerContext = createContext<MowerApi | null>(null);

export function useMower(): MowerApi {
  const m = useContext(MowerContext);
  if (!m) throw new Error("MowerProvider missing");
  return m;
}

/** State object for a mower entity by key. */
export function useMowerEntity(key: string): HassEntity | undefined {
  return useEntity(useMower().ent(key));
}

export function zoneLabel(z: Zone, index: number | undefined, t: T): string {
  if (z.name && z.name.trim()) return z.name.trim();
  return t("Zone {n}", { n: index !== undefined ? index + 1 : z.hashId.slice(0, 4) });
}

function errorText(e: unknown): string {
  if (e && typeof e === "object" && "message" in e) return String((e as any).message);
  return String(e);
}

export function MowerProvider({ device, snap, children }: { device: LymowDevice; snap: Snapshot | undefined; children: ReactNode }) {
  const getHass = useHassRef();
  const ui = useUi();
  const t = useT();
  const mower = device.entities.mower;
  const zones = snap?.map.go_zones ?? [];

  const api: MowerApi = {
    device,
    snap,
    ent: (key) => device.entities[key],
    async call(domain, service, data = {}, success) {
      try {
        await getHass().callService(domain, service, { entity_id: mower, ...data });
        if (success) ui.toast(t(success));
        return true;
      } catch (e) {
        ui.toast(errorText(e), "bad");
        return false;
      }
    },
    async callWithResponse<T>(domain: string, service: string, data: Record<string, unknown> = {}) {
      const res = await getHass().callService(domain, service, { entity_id: [mower], ...data }, undefined, false, true);
      return (res?.response ?? res) as T;
    },
    zoneName(hashId) {
      const i = zones.findIndex((z) => z.hashId === hashId);
      return i >= 0 ? zoneLabel(zones[i], i, t) : t("Zone {n}", { n: hashId.slice(0, 4) });
    },
  };
  return <MowerContext.Provider value={api}>{children}</MowerContext.Provider>;
}
