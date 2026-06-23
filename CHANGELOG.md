# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
Entries start with the first tagged release. Until then, track changes via the
commit history and pull requests. When cutting a release, move items from
"Unreleased" into a new versioned section.
-->

## [Unreleased]

## [0.3.0] - 2026-06-23

First feature release since the initial HACS publish (v0.1.0).

### Added
- **Lovelace map card** — interactive map of zones, no-go zones, channels and the live mower position; start mowing and edit zone boundaries from the card.
- **Zone editing over Wi-Fi** — drag, insert and delete boundary vertices, and merge zones, without the Lymow app.
- **Mowing schedules** — view, create, edit and enable/disable schedules as HA entities.
- **Per-zone settings** — cut-height and path-spacing numbers, plus per-zone enable switches.
- **RTK diagnostics** — detailed GNSS health (location precision, per-band satellite counts and SNR, base-station status, data-error rate, differential age, LoRa bandwidth, antenna gain) as Diagnostic sensors, kept live without the app via the new **App presence** and **RTK diagnostics** switches.
- **Backup maps** — list, restore, rename and delete saved maps, with per-backup preview thumbnails.
- **Live video** — `lymow.start_video_session` opens a Kinesis Video WebRTC session for an external viewer.
- **Bluetooth manual drive** — local BLE remote control via `lymow.ble_drive`.
- Many device-feature switches (theft detection/lock, find-robot, notifications, headlight, …), runtime/config numbers, and connectivity/diagnostic sensors.

### Changed
- RTK sensors regrouped under the Diagnostic category; basic RTK enabled by default, advanced metrics opt-in.

### Fixed
- Lawn-mower activity now reflects PAUSED / ERROR correctly; server-side mow-trail rendering.
- Map-card vertex insert/delete clicks; edit actions unsupported over Wi-Fi are disabled with an explanation.
- RTK diagnostic decode (base-station status and data-error rate were swapped).
