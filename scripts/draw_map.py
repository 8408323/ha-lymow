"""Quick visual sanity-check: decode map_response.bin and render zones to PNG."""

from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "custom_components" / "lymow"))

from protocol import decode_map_response  # noqa: E402

try:
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon
except ImportError:
    sys.exit("Run with: uv run --with matplotlib scripts/draw_map.py")

BIN = pathlib.Path(__file__).parent / "map_response.bin"
OUT = pathlib.Path(__file__).parent / "map.png"

data = decode_map_response(BIN.read_bytes())

go_zones = data.get("goZones", [])
nogo_zones = data.get("nogoZones", [])
cs = data.get("chargingStation", {})
gps = data.get("gpsOrigin", {})

fig, ax = plt.subplots(figsize=(12, 12))
ax.set_aspect("equal")
ax.set_facecolor("#e8f4e8")
fig.patch.set_facecolor("#f5f5f5")

# --- go zones ---
for zone in go_zones:
    pts = [(p["x"], p["y"]) for p in zone["polygon"]]
    if len(pts) < 3:
        continue
    poly = MplPolygon(pts, closed=True, facecolor="#a8d8a8", edgecolor="#2e7d32", linewidth=1.5, alpha=0.7)
    ax.add_patch(poly)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    label = zone["hashId"]
    if zone.get("area"):
        label += f"\n{zone['area']} m²"
    if zone.get("cutHeight"):
        label += f"\nh={zone['cutHeight']}mm"
    ax.text(cx, cy, label, ha="center", va="center", fontsize=7, color="#1b5e20", fontweight="bold")

# --- no-go zones ---
for nogo in nogo_zones:
    pts = [(p["x"], p["y"]) for p in nogo["polygon"]]
    if len(pts) < 3:
        continue
    poly = MplPolygon(pts, closed=True, facecolor="#ffcccc", edgecolor="#c62828", linewidth=1.2, alpha=0.8)
    ax.add_patch(poly)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    ax.text(cx, cy, nogo["hashId"], ha="center", va="center", fontsize=6, color="#b71c1c")

# --- charging station ---
if cs:
    x, y, theta = cs.get("x", 0), cs.get("y", 0), cs.get("theta", 0)
    # Draw a house-like icon: circle + direction arrow
    ax.plot(x, y, marker="o", markersize=14, color="#1565c0", zorder=5)
    ax.plot(x, y, marker="o", markersize=10, color="white", zorder=6)
    ax.plot(x, y, marker="*", markersize=8, color="#1565c0", zorder=7)
    arrow_len = 1.5
    dx = math.cos(theta) * arrow_len
    dy = math.sin(theta) * arrow_len
    ax.annotate(
        "",
        xy=(x + dx, y + dy),
        xytext=(x, y),
        arrowprops=dict(arrowstyle="->", color="#1565c0", lw=2),
        zorder=8,
    )
    ax.text(x + 0.3, y - 1.0, f"⚡ charger\n({x:.2f}, {y:.2f})", fontsize=7, color="#0d47a1")

# --- origin marker ---
ax.plot(0, 0, marker="+", markersize=16, color="black", linewidth=2, zorder=5)
ax.text(0.2, 0.2, "origin", fontsize=7, color="black")

# --- axes labels & grid ---
ax.set_xlabel("X (metres, east →)")
ax.set_ylabel("Y (metres, north ↑)")

title = "Lymow map preview"
if gps:
    title += f"\nGPS origin: lat={gps['lat']:.6f}  lon={gps['lon']:.6f}"
ax.set_title(title, fontsize=11)
ax.grid(True, linestyle="--", alpha=0.4)
ax.autoscale()
ax.margins(0.05)

# legend
legend_items = [
    mpatches.Patch(facecolor="#a8d8a8", edgecolor="#2e7d32", label=f"Go zones ({len(go_zones)})"),
    mpatches.Patch(facecolor="#ffcccc", edgecolor="#c62828", label=f"No-go zones ({len(nogo_zones)})"),
    mpatches.Patch(facecolor="white", edgecolor="#1565c0", label="Charging station"),
]
ax.legend(handles=legend_items, loc="lower right", fontsize=9)

plt.tight_layout()
plt.savefig(OUT, dpi=150)
print(f"Saved → {OUT}")
