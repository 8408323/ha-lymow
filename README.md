# ha-lymow

Home Assistant custom integration for [Lymow](https://www.lymow.com/) robotic lawn mowers.

> **Status**: Working prototype — cloud auth + MQTT real-time state implemented.

## Support

If you find this integration useful, you can buy me a coffee ☕

[![Buy me a coffee](https://img.buymeacoffee.com/button-api/?text=Buy+me+a+coffee&emoji=&slug=jhara&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/jhara)

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=8408323&repository=ha-lymow&category=integration)

Or manually:

1. In HACS, go to **Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/8408323/ha-lymow` as an **Integration**.
3. Search for **Lymow** and click **Download**.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/lymow/` to your HA `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for *Lymow*.
3. Enter your Lymow account credentials.

## Entities

Each mower device exposes the following entities:

### Lawn mower

| Entity | Features |
|--------|----------|
| Mower | Start mowing, Pause, Return to dock |

### Sensors

| Entity | Unit | Enabled by default |
|--------|------|--------------------|
| Battery | % | ✅ |
| Error code | — | ✅ |
| Wi-Fi signal | — | ✅ |
| LTE signal | — | ✅ |
| Wi-Fi RSSI | dBm | ❌ |
| Connectivity | — | ✅ |
| Firmware version | — | ✅ |
| MCU version | — | ✅ |
| IP address | — | ✅ |
| RTK satellites | — | ✅ |
| Total mowed area | m² | ✅ |
| Mow progress | % | ✅ |
| Mow strip count | — | ❌ |

### Per-zone entities (one set per configured mowing zone)

| Entity | Description |
|--------|-------------|
| Zone enabled (switch) | Enable or disable the zone for scheduled mowing |
| Cut height (number) | Blade height for this zone (mm) |

## Contributing

Pull requests are welcome. Please open an issue first to discuss what you'd like to change.

## License

MIT
