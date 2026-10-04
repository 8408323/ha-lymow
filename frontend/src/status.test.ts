import { describe, expect, it } from "vitest";
import type { HassEntity } from "./hass";
import { translate } from "./i18n";
import { formatState as fs, weekday } from "./status";

const formatState = (x: HassEntity) => fs(x, (k, v) => translate("en", k, v), "en");

const e = (state: string, attributes: Record<string, unknown> = {}) => ({ entity_id: "sensor.x", state, attributes }) as HassEntity;

describe("formatState", () => {
  it("formats durations by unit", () => {
    expect(formatState(e("36", { unit_of_measurement: "s", device_class: "duration" }))).toBe("36 s");
    expect(formatState(e("5400", { unit_of_measurement: "s" }))).toBe("1 h 30 min");
    expect(formatState(e("90", { unit_of_measurement: "min", device_class: "duration" }))).toBe("1 h 30 min");
  });

  it("rounds plain numbers and keeps units", () => {
    expect(formatState(e("1482.894587", { unit_of_measurement: "m²" }))).toBe("1,482.9 m²");
    expect(formatState(e("97", { unit_of_measurement: "%" }))).toBe("97 %");
  });

  it("translates and localises", () => {
    expect(fs(e("on"), (k, v) => translate("sv", k, v), "sv")).toBe("På");
    expect(fs(e("1482.9", { unit_of_measurement: "m²" }), (k) => k, "sv")).toBe("1\u00a0482,9 m²");
    expect(weekday(1, "en")).toBe("Mon");
  });

  it("shows missing values as a dash and relative dates", () => {
    expect(formatState(e("unavailable"))).toBe("—");
    const now = new Date();
    expect(formatState(e(now.toISOString(), { device_class: "timestamp" }))).toMatch(/^Today /);
  });
});
