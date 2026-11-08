#!/usr/bin/env python3

import argparse
import socket
import time
from typing import Any

from dotenv import load_dotenv
from rich.align import Align
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

load_dotenv()

DEFAULT_HOST = "192.168.30.163"
DEFAULT_PORT = 2000
DEFAULT_DURATION = 60

console = Console(force_terminal=True)


def value_or_default(value: Any, default: str = "--") -> str:
    return default if value in (None, "") else str(value)


def celsius_to_fahrenheit(celsius: float) -> float:
    return round((celsius * 9 / 5) + 32, 2)


def meters_to_feet(meters: float) -> float:
    return round(meters * 3.28084, 2)


def normalize_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ydzda(sentence: str) -> str:
    parts = sentence.split(",")
    if len(parts) < 5:
        return "--"
    try:
        time_raw = parts[1]
        day = int(parts[2])
        month = int(parts[3])
        year = int(parts[4])
        hour = int(time_raw[:2])
        minute = int(time_raw[2:4])
        second = float(time_raw[4:])
        return f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:05.2f}Z"
    except (TypeError, ValueError):
        return "--"


def parse_line(line: str, telemetry: dict[str, Any]) -> None:
    if not line or not line.startswith("$"):
        return

    if line.startswith("$YDXDR"):
        fields = line.split(",")
        if "EngineHours#0" in line and len(fields) > 2:
            value = normalize_float(fields[2])
            if value is not None:
                telemetry["engine_hours"] = round(value, 2)
        elif "Engine#0" in line and "Fuel#0" in line and "Alternator#0" in line:
            temp_value = normalize_float(fields[2]) if len(fields) > 2 else None
            voltage_value = normalize_float(fields[10]) if len(fields) > 10 else None
            if temp_value is not None:
                telemetry["engine_temp"] = round(celsius_to_fahrenheit(temp_value), 2)
            if voltage_value is not None:
                telemetry["voltage"] = round(voltage_value, 2)
            if telemetry.get("fuel") is None:
                telemetry["fuel"] = 1

    elif line.startswith("$YDHDG"):
        heading = normalize_float(line.split(",")[1])
        if heading is not None:
            telemetry["heading"] = round(heading, 1)

    elif line.startswith("$YDDBT") or line.startswith("$IIDBT") or line.startswith("$SDDBT"):
        fields = line.split(",")
        if len(fields) >= 5:
            unit = fields[2].upper() if len(fields) > 2 else ""
            depth_m = normalize_float(fields[3]) if unit == "M" else None
            depth_ft = normalize_float(fields[1]) if unit in {"F", "FT"} else None
            if depth_m is not None:
                telemetry["depth_m"] = round(depth_m, 2)
                telemetry["depth_ft"] = round(meters_to_feet(depth_m), 2)
            elif depth_ft is not None:
                telemetry["depth_ft"] = round(depth_ft, 2)
                telemetry["depth_m"] = round(depth_ft / 3.28084, 2)

    elif line.startswith("$YDGGA"):
        fields = line.split(",")
        if len(fields) >= 6:
            telemetry["latitude"] = fields[2]
            telemetry["longitude"] = fields[4]

    elif line.startswith("$YDZDA"):
        telemetry["time"] = parse_ydzda(line)

    elif line.startswith("$PCDIN") and "01F201" in line:
        parts = line.split(",")
        if len(parts) >= 5:
            hexdata = parts[4].strip().split("*")[0]
            try:
                rpm_bytes = bytes.fromhex(hexdata)
            except ValueError:
                return
            if len(rpm_bytes) >= 7:
                rpm = int.from_bytes(rpm_bytes[3:7], byteorder="little") / 4.0
                telemetry["rpm"] = round(rpm, 1)


def build_dashboard(telemetry: dict[str, Any]) -> Panel:
    engine_table = Table.grid(expand=True)
    engine_table.add_column(style="bold cyan")
    engine_table.add_column(justify="right")
    engine_table.add_row("Engine hours", value_or_default(telemetry.get("engine_hours"), "--"))
    engine_table.add_row("Engine temp", f"{value_or_default(telemetry.get('engine_temp'), '--')} °F")
    engine_table.add_row("Fuel", f"{value_or_default(telemetry.get('fuel'), '--')} L")
    engine_table.add_row("Voltage", f"{value_or_default(telemetry.get('voltage'), '--')} V")
    engine_table.add_row("RPM", value_or_default(telemetry.get("rpm"), "--"))

    nav_table = Table.grid(expand=True)
    nav_table.add_column(style="bold cyan")
    nav_table.add_column(justify="right")
    nav_table.add_row("UTC time", value_or_default(telemetry.get("time"), "--"))
    nav_table.add_row("Heading", f"{value_or_default(telemetry.get('heading'), '--')}° True")
    nav_table.add_row("Depth", f"{value_or_default(telemetry.get('depth_m'), '--')} m / {value_or_default(telemetry.get('depth_ft'), '--')} ft")
    nav_table.add_row("Latitude", value_or_default(telemetry.get("latitude"), "--"))
    nav_table.add_row("Longitude", value_or_default(telemetry.get("longitude"), "--"))

    columns = Columns(
        [
            Panel(engine_table, title="Engine", border_style="green"),
            Panel(nav_table, title="Navigation", border_style="blue"),
        ],
        expand=True,
    )
    return Panel(Align.center(columns), title="NMEA 2000 Live Monitor", border_style="bold cyan")


def main() -> int:
    parser = argparse.ArgumentParser(description="Display live NMEA 2000 telemetry from a Yacht Devices gateway.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Gateway host IP (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Gateway port (default: %(default)s)")
    parser.add_argument("--seconds", type=float, default=DEFAULT_DURATION, help="How long to display live data before exiting")
    args = parser.parse_args()

    telemetry: dict[str, Any] = {
        "latitude": None,
        "longitude": None,
        "engine_hours": None,
        "engine_temp": None,
        "fuel": None,
        "voltage": None,
        "heading": None,
        "depth_m": None,
        "depth_ft": None,
        "rpm": None,
        "time": None,
    }

    with Live(build_dashboard(telemetry), auto_refresh=False, console=console, refresh_per_second=4) as live:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((args.host, args.port), timeout=2.0) as sock:
                    sock.settimeout(1.0)
                    while time.monotonic() < deadline:
                        try:
                            chunk = sock.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        for line in chunk.decode("utf-8", errors="ignore").splitlines():
                            clean = line.strip()
                            if clean:
                                parse_line(clean, telemetry)
                        live.update(build_dashboard(telemetry))
            except OSError:
                live.update(Panel(f"Waiting for gateway at {args.host}:{args.port}...", title="Connection", border_style="yellow"))
                time.sleep(2.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
