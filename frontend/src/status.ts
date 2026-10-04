import type { HassEntity } from "./hass";

export type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export const MOWER_STATE: Record<string, { label: string; tone: Tone; icon: string }> = {
  mowing: { label: "Mowing", tone: "good", icon: "mdi:robot-mower" },
  paused: { label: "Paused", tone: "warn", icon: "mdi:pause-circle" },
  docked: { label: "Docked", tone: "info", icon: "mdi:home-lightning-bolt" },
  returning: { label: "Returning to dock", tone: "info", icon: "mdi:home-import-outline" },
  error: { label: "Needs attention", tone: "bad", icon: "mdi:alert-circle" },
  unavailable: { label: "Offline", tone: "neutral", icon: "mdi:cloud-off-outline" },
  unknown: { label: "Unknown", tone: "neutral", icon: "mdi:help-circle-outline" },
};

export function mowerState(state: string | undefined) {
  return MOWER_STATE[state ?? "unknown"] ?? { label: state ?? "Unknown", tone: "neutral" as Tone, icon: "mdi:robot-mower" };
}

// PbOutput workStatus codes (live, from the robot).
export const WORK_STATUS: Record<number, string> = {
  0: "Idle",
  1: "Waiting",
  2: "Mowing",
  3: "Paused",
  4: "Docking",
  5: "Charging",
  6: "Remote control",
  7: "Error",
  8: "Resuming",
  9: "Mowing",
  10: "Paused",
  11: "Updating",
  12: "Charged",
  13: "Emergency stop",
  14: "Escaping",
  15: "Self-test",
};

export const MOWING_WORK_STATUS = new Set([2, 8, 9]);

export const RTK: Record<number, { label: string; tone: Tone }> = {
  0: { label: "No fix", tone: "bad" },
  1: { label: "Float", tone: "warn" },
  2: { label: "Fixed", tone: "good" },
  3: { label: "RTK fixed", tone: "good" },
};

export const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export const pad2 = (n: number) => String(n).padStart(2, "0");

export function num(state: string | undefined): number | undefined {
  if (state === undefined) return undefined;
  const n = Number(state);
  return Number.isFinite(n) ? n : undefined;
}

/** Human-friendly entity value: relative dates, durations, rounded numbers with units. */
export function formatState(e: HassEntity): string {
  const unit = e.attributes.unit_of_measurement;
  const dc = e.attributes.device_class;
  if (e.state === "unknown" || e.state === "unavailable") return "—";
  if (dc === "timestamp") {
    const d = new Date(e.state);
    if (isNaN(+d)) return e.state;
    const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
    const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (days === 0) return `Today ${time}`;
    if (days === 1) return `Yesterday ${time}`;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }
  const n = Number(e.state);
  if (Number.isFinite(n)) {
    const secs = unit === "s" ? n : unit === "min" ? n * 60 : unit === "h" ? n * 3600 : undefined;
    if (secs !== undefined && (dc === "duration" || unit !== "h")) {
      if (secs < 60) return `${Math.round(secs)} s`;
      const m = Math.round(secs / 60);
      return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
    }
    return `${Math.round(n * 10) / 10}${unit ? ` ${unit}` : ""}`;
  }
  return e.state;
}
