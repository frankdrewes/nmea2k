# Project Plan

**Status**: Planning
**Created**: 2026-11-07
**Mode**: AUGMENT

---

## 1. Project Overview

**Goal**: Build a local NMEA 2000 display that listens to a Yacht Devices gateway and renders live boat telemetry in a rich terminal dashboard. The project is designed so that every module is independently testable.

**App Type**: Background worker

**API Login**: No

**Mode**: AUGMENT

**Deployment Plan**: No deployment plan found

---

## 2. NMEA Monitor — Worker

| Component | Technology |
|-----------|-----------|
| **Language** | Python |
| **Runtime** | CPython |
| **Package Manager** | pip |
| **Test Runner** | pytest |
| **Mocking Library** | unittest.mock |
| **Test Command** | pytest |
| **Orchestration** | docker-compose |

---

## 3. Services Required

| Service | Role in App | Environment Variable | Default Value (Local) | Classification |
|---------|------------|----------------------|----------------------|----------------|
| Yacht Devices WiFi gateway | Source the raw NMEA 2000 stream | `GATEWAY_HOST`, `GATEWAY_PORT` | `192.168.30.163:2000` | Essential |
| Local terminal dashboard | Render live engine, fuel, electrical, and GPS values in real time | `DISPLAY_REFRESH_SECONDS` | `1` | Essential |

No datastore is required because the app processes the incoming stream in memory and presents live values only.

---

## 4. Prerequisites

### Run

| Tool | Service(s) | Installed | Version |
|------|------------|-----------|---------|
| Python 3.x | NMEA Monitor | ✅ | 3.14.0 |
| pip | NMEA Monitor | ❓ | unknown |
| rich | NMEA Monitor | ❓ | unknown |
| python-dotenv | NMEA Monitor | ❓ | unknown |
| textual | NMEA Monitor | ❓ | unknown |

### Debug

| Tool | Service(s) | Installed | Version |
|------|------------|-----------|---------|
| VS Code Python extension | NMEA Monitor | ❓ | unknown |
| Python debug adapter | NMEA Monitor | ❓ | unknown |

---

## 5. Project Structure

```text
nmea2k/
├── .azure/
│   └── project-plan.md
├── .env
├── README.md
├── requirements.txt
├── nmea2k-mqtt.py
├── scan.py
├── scan2.py
├── scan3.py
├── scan4.py
├── somecode.py
├── temper/
│   └── raspi_temp_monitor.py
```

---

## 6. Route Definitions

| # | Method | Path | Description | Request Body | Response Body | Status Codes |
|---|--------|------|-------------|-------------|--------------|-------------|
| 1 | TCP | `192.168.30.163:2000` | Connect to the Yacht Devices gateway and read incoming NMEA 2000 sentences | Raw socket stream | Parsed telemetry snapshots | 200, 500 |
| 2 | Local UI | `/live` | Refresh the terminal dashboard with the latest engine, fuel, electrical, and GPS values | None | In-memory telemetry snapshot | 200 |

---

## 7. Next Steps

1. Keep the app focused on local live display only, without any MQTT or broker integration.
2. Verify the parser against the real NMEA stream and validate the field mapping for rpm, engine temperature, heading, depth, latitude, and longitude.
3. Build the terminal dashboard with Textual or Rich so values refresh continuously and remain readable while underway.
4. Add targeted tests around the parsing and normalization functions before improving the display layout or adding any optional integrations.
