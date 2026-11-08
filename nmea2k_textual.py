import asyncio
import socket
from typing import TypeAlias

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Footer, Header, RichLog, Static

HOST = "192.168.30.163"
PORT = 2000
RECONNECT_DELAY = 5

LatestValues: TypeAlias = dict[str, int | float | str | None]


def convert_latitude_to_dms(lat_str: str) -> str:
    return _convert_coordinate_to_dms(lat_str, 2)


def convert_longitude_to_dms(lon_str: str) -> str:
    return _convert_coordinate_to_dms(lon_str, 3)


def _convert_coordinate_to_dms(coordinate: str, degree_digits: int) -> str:
    direction = coordinate[-1]
    raw = coordinate[:-1]
    degrees = int(raw[:degree_digits])
    minutes_float = float(raw[degree_digits:])
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return f'{degrees}°{minutes:02d}\'{seconds:.1f}" {direction}'


def celsius_to_fahrenheit(celsius: float) -> float:
    return round((celsius * 9 / 5) + 32, 1)


def decode_fluid_level(payload_hex: str) -> dict[str, int | float]:
    """Decode tank metadata and percentage from a PGN 127505 payload."""
    payload = bytes.fromhex(payload_hex)
    if len(payload) < 3:
        raise ValueError("PGN 127505 payload must contain at least 3 bytes")

    tank_instance = payload[0]
    fluid_type = payload[1] & 0x0F
    tank_number = (payload[1] >> 4) & 0x0F
    level_raw = (payload[2] << 8) | payload[1]

    return {
        "tank_instance": tank_instance,
        "fluid_type": fluid_type,
        "tank_number": tank_number,
        "level_raw": level_raw,
        "level_percent": level_raw * 0.004,
    }


def parse_line(line: str, latest: LatestValues) -> bool:
    """Update the requested telemetry values from one NMEA sentence."""
    if line.startswith("$YDXDR"):
        fields = line.split(",")
        for index in range(1, len(fields) - 3, 4):
            name = fields[index + 3].split("*")[0]
            if name in ("Pitch", "Roll"):
                latest[name.lower()] = round(float(fields[index + 1]), 2)
        if "EngineHours#0" in line:
            engine_hours = round(float(fields[2]), 2)
            latest["engine_hours"] = engine_hours
            return True
        if all(name in line for name in ("Engine#0", "Fuel#0", "Alternator#0")):
            engine_temp = celsius_to_fahrenheit(float(fields[2]))
            voltage = round(float(fields[10]), 2)
            latest.update(engine_temp=engine_temp, voltage=voltage)
            return True

    elif line.startswith("$YDHDG"):
        latest["heading"] = round(float(line.split(",")[1]), 1)
        return True

    elif line.startswith("$YDROT"):
        fields = line.split(",")
        if fields[2].split("*")[0] == "A":
            latest["rate_of_turn"] = round(float(fields[1]), 1)
            return True

    elif line.startswith("$YDDBT"):
        fields = line.split(",")
        depth_ft = round(float(fields[1]), 3)
        latest["depth_ft"] = depth_ft
        return True

    elif line.startswith("$YDGGA"):
        fields = line.split(",")
        latitude = convert_latitude_to_dms(fields[2] + fields[3])
        longitude = convert_longitude_to_dms(fields[4] + fields[5])
        latest.update(latitude=latitude, longitude=longitude)
        return True

    elif line.startswith("$PCDIN") and "01F211" in line:
        hexdata = line.split(",")[4].strip().split("*")[0]
        fuel = decode_fluid_level(hexdata)
        latest["fuel"] = round(fuel["level_percent"], 1)
        latest["fuel_tank_number"] = fuel["tank_number"]
        return True

    elif line.startswith("$MXPGN") and "01F211" in line:
        hexdata = line.split(",")[3].strip().split("*")[0]
        fuel = decode_fluid_level(hexdata)
        latest["fuel"] = round(fuel["level_percent"], 1)
        latest["fuel_tank_number"] = fuel["tank_number"]
        return True

    elif line.startswith("$PCDIN") and "01F201" in line:
        hexdata = line.split(",")[4].strip().split("*")[0]
        rpm_bytes = bytes.fromhex(hexdata)
        if len(rpm_bytes) >= 7:
            rpm = int.from_bytes(rpm_bytes[3:7], byteorder="little") / 4.0
            latest["rpm"] = round(rpm, 1)
            return True

    return False


class NmeaDashboard(App[None]):
    TITLE = "NMEA 2000 Telemetry"
    CSS = """
    #dashboard {
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1fr 1fr;
        grid-gutter: 1 2;
        padding: 1 2;
        height: 1fr;
    }
    .card {
        border: round $primary;
        padding: 1 2;
    }
    .card-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    .reading {
        padding-bottom: 1;
    }
    #connection-status {
        padding: 0 2;
        color: $text-muted;
    }
    #raw-log-title {
        padding: 0 2;
        text-style: bold;
        color: $accent;
    }
    #raw-log {
        height: 10;
        margin: 0 2 1;
        border: round $primary;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.latest: LatestValues = {
            "rpm": None,
            "engine_temp": None,
            "engine_hours": None,
            "fuel": None,
            "fuel_tank_number": None,
            "voltage": None,
            "latitude": None,
            "longitude": None,
            "depth_ft": None,
            "heading": None,
            "rate_of_turn": None,
            "pitch": None,
            "roll": None,
        }

    def compose(self) -> ComposeResult:
        yield Header()
        with Grid(id="dashboard"):
            with Vertical(classes="card"):
                yield Static("Engine", classes="card-title")
                yield Static("RPM: --", id="rpm", classes="reading")
                yield Static("Engine temp: --", id="engine-temp", classes="reading")
                yield Static("Engine hours: --", id="engine-hours", classes="reading")
            with Vertical(classes="card"):
                yield Static("Fuel1", classes="card-title")
                yield Static("Fuel: --", id="fuel", classes="reading")
            with Vertical(classes="card"):
                yield Static("Electrical", classes="card-title")
                yield Static("Voltage: --", id="voltage", classes="reading")
            with Vertical(classes="card"):
                yield Static("Boat", classes="card-title")
                yield Static("Latitude: --", id="latitude", classes="reading")
                yield Static("Longitude: --", id="longitude", classes="reading")
                yield Static("Depth: --", id="depth-ft", classes="reading")
                yield Static("Heading: --", id="heading", classes="reading")
                yield Static("Rate of Turn: --", id="rate-of-turn", classes="reading")
                yield Static("Pitch: --", id="pitch", classes="reading")
                yield Static("Roll: --", id="roll", classes="reading")
        yield Static("Connecting...", id="connection-status")
        yield Static("Raw network data", id="raw-log-title")
        yield RichLog(id="raw-log", wrap=True, max_lines=200)
        yield Footer()

    def on_mount(self) -> None:
        self.connect_to_gateway()

    @work(exclusive=True)
    async def connect_to_gateway(self) -> None:
        while self.is_running:
            writer = None
            try:
                self._set_connection_status(f"Connecting to {HOST}:{PORT}...")
                reader, writer = await asyncio.open_connection(HOST, PORT)
                self._set_connection_status(f"Connected to {HOST}:{PORT}")

                while self.is_running:
                    data = await reader.readline()
                    if not data:
                        self._set_connection_status("Gateway closed the connection; reconnecting...")
                        break
                    line = data.decode(errors="replace").strip()
                    if not line:
                        continue
                    self.query_one("#raw-log", RichLog).write(line)
                    try:
                        if parse_line(line, self.latest):
                            self._update_readings()
                    except (IndexError, ValueError) as error:
                        sentence_type = line.split(",", 1)[0]
                        self._set_connection_status(
                            f"Invalid {sentence_type} sentence: {error}"
                        )
            except OSError as error:
                self._set_connection_status(f"Connection error: {error}; retrying...")
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()

            if self.is_running:
                await asyncio.sleep(RECONNECT_DELAY)

    def _set_connection_status(self, message: str) -> None:
        self.query_one("#connection-status", Static).update(message)

    def _update_readings(self) -> None:
        values = self.latest
        rate_of_turn = values["rate_of_turn"]
        if rate_of_turn is None:
            rate_of_turn_text = "--"
        elif rate_of_turn < 0:
            rate_of_turn_text = f"{abs(rate_of_turn):.1f}°/min to port"
        elif rate_of_turn > 0:
            rate_of_turn_text = f"{rate_of_turn:.1f}°/min to starboard"
        else:
            rate_of_turn_text = "0.0°/min"
        readings = {
            "rpm": f"RPM: {values['rpm'] if values['rpm'] is not None else '--'}",
            "engine-temp": f"Engine temp: {values['engine_temp'] if values['engine_temp'] is not None else '--'} °F",
            "engine-hours": f"Engine hours: {values['engine_hours'] if values['engine_hours'] is not None else '--'} h",
            "fuel": (
                f"Fuel: {values['fuel'] if values['fuel'] is not None else '--'}% "
                f"(Tank {values['fuel_tank_number'] if values['fuel_tank_number'] is not None else '--'})"
            ),
            "voltage": f"Voltage: {values['voltage'] if values['voltage'] is not None else '--'} V",
            "latitude": f"Latitude: {values['latitude'] if values['latitude'] is not None else '--'}",
            "longitude": f"Longitude: {values['longitude'] if values['longitude'] is not None else '--'}",
            "depth-ft": f"Depth: {values['depth_ft'] if values['depth_ft'] is not None else '--'} ft",
            "heading": f"Heading: {values['heading'] if values['heading'] is not None else '--'}°",
            "rate-of-turn": f"Rate of Turn: {rate_of_turn_text}",
            "pitch": f"Pitch: {values['pitch'] if values['pitch'] is not None else '--'}°",
            "roll": f"Roll: {values['roll'] if values['roll'] is not None else '--'}°",
        }
        for widget_id, text in readings.items():
            self.query_one(f"#{widget_id}", Static).update(text)


if __name__ == "__main__":
    NmeaDashboard().run()
