#!/usr/bin/env python3
"""Listen to a Yacht Devices RAW TCP stream and decode NMEA 2000 frames."""

import argparse
import math
import os
import select
import socket
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = os.getenv("YD_Gateway_IP", "192.168.30.162")
DEFAULT_PORT = 3000
RECONNECT_DELAY_SECONDS = 2
CAPTURE_INTERVAL_SECONDS = 10

PGN_NAMES = {
    126208: "NMEA Group Function",
    126720: "Manufacturer Proprietary Fast Packet",
    126993: "Heartbeat",
    126996: "Product Information",
    127237: "Heading/Track Control",
    127245: "Rudder",
    127250: "Vessel Heading",
    127251: "Rate of Turn",
    127257: "Attitude",
    127488: "Engine Parameters, Rapid Update",
    127489: "Engine Parameters, Dynamic",
    127505: "Fluid Level",
    127506: "DC Detailed Status",
    127508: "Battery Status",
    60928: "ISO Address Claim",
    59904: "ISO Request",
    65359: "SeaTalk Pilot Heading",
    65360: "SeaTalk Pilot Locked Heading",
    65379: "SeaTalk Pilot Mode",
    128267: "Water Depth",
    129025: "Position, Rapid Update",
    129026: "COG & SOG, Rapid Update",
    129029: "GNSS Position Data",
    130306: "Wind Data",
    130916: "Manufacturer Proprietary Fast Packet",
}

FAST_PACKET_PGNS = {
    122368,
    126208,
    126720,
    126996,
    127237,
    127489,
    129029,
    130916,
}
RAYMARINE_PILOT_MODES = {
    0: "Standby",
    64: "Auto, compass commanded",
    256: "Wind",
    384: "Track",
    385: "No Drift",
}
SEATALK1_COMMANDS = {
    132: "Compass heading, autopilot course, and rudder position",
    156: "Compass heading and rudder position",
    164: "Device identification query/response",
    165: "GPS and DGPS information",
}
FLUID_TYPES = {
    0: "fuel",
    1: "water",
    2: "grey water",
    3: "live well",
    4: "oil",
    5: "black water",
    6: "other",
}
DC_TYPES = {
    0: "battery",
    1: "alternator",
    2: "converter",
    3: "charger",
    4: "inverter",
    5: "solar cell",
    6: "wind generator",
}


@dataclass(frozen=True)
class Nmea2000Frame:
    timestamp: str | None
    direction: str | None
    priority: int
    pgn: int
    source: int
    destination: int | None
    payload: bytes


class FastPacketDecoder:
    """Reassemble NMEA 2000 Fast Packets from their CAN frame fragments."""

    def __init__(self) -> None:
        self.pending: dict[
            tuple[int, int, int | None, int], tuple[int, int, bytearray]
        ] = {}

    def add(self, frame: Nmea2000Frame) -> bytes | None:
        if frame.pgn not in FAST_PACKET_PGNS:
            return None
        if len(frame.payload) != 8:
            return None

        control = frame.payload[0]
        sequence = (control >> 5) & 0x07
        index = control & 0x1F
        key = (frame.pgn, frame.source, frame.destination, sequence)

        if index == 0:
            message_length = frame.payload[1]
            if not 6 < message_length <= 223:
                self.pending.pop(key, None)
                return None
            data = bytearray(frame.payload[2:])
            if len(data) >= message_length:
                return bytes(data[:message_length])
            self.pending[key] = (1, message_length, data)
            return None

        pending = self.pending.get(key)
        if pending is None:
            return None

        next_index, message_length, data = pending
        if index != next_index:
            self.pending.pop(key, None)
            return None

        data.extend(frame.payload[1:])
        if len(data) >= message_length:
            self.pending.pop(key, None)
            return bytes(data[:message_length])

        self.pending[key] = (next_index + 1, message_length, data)
        return None


def parse_raw_frame(line: str) -> Nmea2000Frame:
    """Parse a gateway row, optionally prefixed by timestamp and direction."""
    parts = line.strip().split()
    if not parts:
        raise ValueError("empty frame")

    timestamp = None
    direction = None
    identifier_index = 0
    if len(parts) >= 3 and parts[1] in {"R", "T"}:
        timestamp, direction = parts[0], parts[1]
        identifier_index = 2

    identifier_text = parts[identifier_index].removeprefix("0x")
    if len(identifier_text) != 8:
        raise ValueError("CAN identifier must contain 8 hexadecimal digits")
    try:
        identifier = int(identifier_text, 16)
        payload = bytes.fromhex(" ".join(parts[identifier_index + 1 :]))
    except ValueError as error:
        raise ValueError("invalid hexadecimal CAN identifier or payload") from error

    if identifier > 0x1FFFFFFF:
        raise ValueError("CAN identifier exceeds the 29-bit NMEA 2000 range")
    if len(payload) > 8:
        raise ValueError("NMEA 2000 CAN payload cannot exceed 8 bytes")

    priority = (identifier >> 26) & 0x07
    pgn = (identifier >> 8) & 0x3FFFF
    pdu_format = (identifier >> 16) & 0xFF
    pdu_specific = (identifier >> 8) & 0xFF
    destination = None
    if pdu_format < 240:
        pgn &= 0x3FF00
        destination = pdu_specific

    return Nmea2000Frame(
        timestamp=timestamp,
        direction=direction,
        priority=priority,
        pgn=pgn,
        source=identifier & 0xFF,
        destination=destination,
        payload=payload,
    )


def _unsigned(
    payload: bytes, offset: int, size: int, scale: float = 1.0
) -> int | float | None:
    raw = int.from_bytes(payload[offset : offset + size], "little")
    if raw >= (1 << (size * 8)) - 3:
        return None
    value = raw * scale
    return int(value) if scale == 1 else value


def _signed(
    payload: bytes, offset: int, size: int, scale: float = 1.0
) -> int | float | None:
    raw = int.from_bytes(payload[offset : offset + size], "little")
    if raw >= (1 << (size * 8 - 1)) - 2:
        return None
    value = int.from_bytes(payload[offset : offset + size], "little", signed=True)
    value *= scale
    return int(value) if scale == 1 else value


def _decode_manufacturer_payload(
    pgn: int, payload: bytes
) -> dict[str, int | float | str | None] | None:
    if len(payload) < 2:
        return None

    header = int.from_bytes(payload[:2], "little")
    manufacturer_code = header & 0x07FF
    industry_code = (header >> 13) & 0x07
    manufacturer = {1851: "Raymarine"}.get(
        manufacturer_code, f"unknown ({manufacturer_code})"
    )
    industry = {4: "Marine Industry"}.get(
        industry_code, f"unknown ({industry_code})"
    )

    values: dict[str, int | float | str | None] = {
        "manufacturer": manufacturer,
        "manufacturer_code": manufacturer_code,
        "industry": industry,
    }
    if pgn == 126720 and len(payload) >= 4:
        proprietary_id = payload[2]
        command = payload[3]
        values.update(
            proprietary_id=(
                "SeaTalk 1 Encoded" if proprietary_id == 240 else proprietary_id
            ),
            command=("SeaTalk1" if command == 129 else command),
        )
        if proprietary_id == 240 and command == 129 and len(payload) >= 5:
            seatalk_command = payload[4]
            values["seatalk1_command"] = SEATALK1_COMMANDS.get(
                seatalk_command, f"unknown ({seatalk_command})"
            )
            values["seatalk1_command_id"] = seatalk_command
            vendor_data = payload[5:]
        else:
            vendor_data = payload[4:]
    else:
        vendor_data = payload[2:]

    if pgn in {126720, 130916}:
        values["vendor_data"] = " ".join(f"{byte:02X}" for byte in vendor_data)
    return values


def decode_payload(pgn: int, payload: bytes) -> dict[str, int | float | str | None] | None:
    """Decode common NMEA 2000 PGNs; return None for unsupported payloads."""
    if pgn == 127245 and len(payload) >= 8:
        angle_scale = 0.0001 * 180 / math.pi
        direction_order = payload[1] & 0x07
        return {
            "instance": None if payload[0] == 0xFF else payload[0],
            "direction_order": {
                0: "no order",
                1: "move to starboard",
                2: "move to port",
            }.get(direction_order),
            "angle_order_deg": _signed(payload, 2, 2, angle_scale),
            "rudder_position_deg": _signed(payload, 4, 2, angle_scale),
        }

    if pgn == 127250 and len(payload) >= 8:
        angle_scale = 0.0001 * 180 / math.pi
        reference = payload[7] & 0x03
        return {
            "heading_deg": _unsigned(payload, 1, 2, angle_scale),
            "deviation_deg": _signed(payload, 3, 2, angle_scale),
            "variation_deg": _signed(payload, 5, 2, angle_scale),
            "reference": {0: "true", 1: "magnetic"}.get(reference, "unknown"),
        }

    if pgn == 127251 and len(payload) >= 5:
        rate = _signed(payload, 1, 4, 3.125e-8)
        return {
            "rate_of_turn_deg_per_min": (
                None if rate is None else rate * 180 / math.pi * 60
            )
        }

    if pgn == 127257 and len(payload) >= 7:
        angle_scale = 0.0001 * 180 / math.pi
        return {
            "yaw_deg": _signed(payload, 1, 2, angle_scale),
            "pitch_deg": _signed(payload, 3, 2, angle_scale),
            "roll_deg": _signed(payload, 5, 2, angle_scale),
        }

    if pgn == 126993 and len(payload) >= 8:
        status = payload[3]
        return {
            "data_transmit_offset_ms": _unsigned(payload, 0, 2),
            "sequence_counter": payload[2],
            "controller_1_state": {
                0: "error active",
                1: "error passive",
                2: "bus off",
            }.get(status & 0x03),
            "controller_2_state": {
                0: "error active",
                1: "error passive",
                2: "bus off",
            }.get((status >> 2) & 0x03),
            "equipment_status": {0: "operational", 1: "fault"}.get(
                (status >> 4) & 0x03
            ),
        }

    if pgn in {65359, 65360} and len(payload) >= 8:
        angle_scale = 0.0001 * 180 / math.pi
        values = _decode_manufacturer_payload(pgn, payload) or {}
        heading_names = (
            ("heading_true_deg", "heading_magnetic_deg")
            if pgn == 65359
            else ("target_heading_true_deg", "target_heading_magnetic_deg")
        )
        values["sid"] = None if payload[2] == 0xFF else payload[2]
        values[heading_names[0]] = _unsigned(payload, 3, 2, angle_scale)
        values[heading_names[1]] = _unsigned(payload, 5, 2, angle_scale)
        return values

    if pgn == 65379 and len(payload) >= 8:
        values = _decode_manufacturer_payload(pgn, payload) or {}
        mode = int.from_bytes(payload[2:4], "little")
        values.update(
            pilot_mode=RAYMARINE_PILOT_MODES.get(mode, f"unknown ({mode})"),
            pilot_mode_code=mode,
            sub_mode=_unsigned(payload, 4, 2),
            pilot_mode_data=None if payload[6] == 0xFF else payload[6],
        )
        return values

    if pgn == 127488 and len(payload) >= 6:
        return {
            "engine_instance": payload[0],
            "engine_speed_rpm": _unsigned(payload, 1, 2, 0.25),
            "boost_pressure_kpa": _unsigned(payload, 3, 2, 0.1),
            "tilt_trim_pct": None if payload[5] == 0xFF else payload[5],
        }

    if pgn == 127489 and len(payload) >= 26:
        oil_temp = _unsigned(payload, 3, 2, 0.1)
        engine_temp = _unsigned(payload, 5, 2, 0.01)
        return {
            "engine_instance": payload[0],
            "oil_pressure_kpa": _unsigned(payload, 1, 2, 0.1),
            "oil_temperature_c": None if oil_temp is None else oil_temp - 273.15,
            "engine_temperature_c": (
                None if engine_temp is None else engine_temp - 273.15
            ),
            "alternator_voltage_v": _unsigned(payload, 7, 2, 0.01),
            "fuel_rate_l_per_h": _unsigned(payload, 9, 2, 0.1),
            "engine_hours": (
                None
                if (hours := _unsigned(payload, 11, 4)) is None
                else hours / 3600
            ),
            "coolant_pressure_kpa": _unsigned(payload, 15, 2, 0.1),
            "fuel_pressure_kpa": _unsigned(payload, 17, 2, 1.0),
            "engine_load_pct": None if payload[20] == 0xFF else payload[20],
            "engine_torque_pct": None if payload[21] == 0xFF else payload[21],
        }

    if pgn == 127505 and len(payload) >= 8:
        descriptor = payload[3]
        fluid_type = descriptor & 0x0F
        level = _unsigned(payload, 1, 2, 0.004)
        return {
            "tank_instance": payload[0],
            "tank_number": None if descriptor == 0xFF else (descriptor >> 4) & 0x0F,
            "fluid_type": (
                "unavailable"
                if descriptor == 0xFF
                else FLUID_TYPES.get(fluid_type, f"unknown ({fluid_type})")
            ),
            "level_pct": level,
            "capacity_l": _unsigned(payload, 4, 4, 0.1),
        }

    if pgn == 127506 and len(payload) >= 8:
        dc_type = payload[1]
        return {
            "instance": payload[0],
            "dc_type": DC_TYPES.get(dc_type, f"unknown ({dc_type})"),
            "state_of_charge_pct": None if payload[2] == 0xFF else payload[2],
            "state_of_health_pct": None if payload[3] == 0xFF else payload[3],
            "time_remaining_s": _unsigned(payload, 4, 4),
        }

    if pgn == 127508 and len(payload) >= 8:
        temperature = _unsigned(payload, 5, 2, 0.01)
        return {
            "battery_instance": payload[0],
            "voltage_v": _unsigned(payload, 1, 2, 0.01),
            "current_a": _signed(payload, 3, 2, 0.1),
            "temperature_c": (
                None if temperature is None else temperature - 273.15
            ),
        }

    if pgn == 128267 and len(payload) >= 8:
        return {
            "depth_m": _unsigned(payload, 1, 4, 0.01),
            "offset_m": _signed(payload, 5, 2, 0.001),
        }

    if pgn == 129025 and len(payload) >= 8:
        return {
            "latitude_deg": _signed(payload, 0, 4, 1e-7),
            "longitude_deg": _signed(payload, 4, 4, 1e-7),
        }

    if pgn == 129026 and len(payload) >= 8:
        reference = payload[1] & 0x03
        return {
            "course_deg": _unsigned(payload, 2, 2, 0.0001 * 180 / math.pi),
            "speed_m_per_s": _unsigned(payload, 4, 2, 0.01),
            "reference": {0: "true", 1: "magnetic"}.get(reference, "unknown"),
        }

    if pgn == 130306 and len(payload) >= 6:
        reference = payload[5] & 0x07
        references = {
            0: "true north",
            1: "magnetic",
            2: "apparent",
            3: "true boat",
            4: "true water",
        }
        return {
            "wind_speed_m_per_s": _unsigned(payload, 1, 2, 0.01),
            "wind_angle_deg": _unsigned(payload, 3, 2, 0.0001 * 180 / math.pi),
            "reference": references.get(reference, "unknown"),
        }

    if pgn in {126720, 130916}:
        return _decode_manufacturer_payload(pgn, payload)

    return None


def format_values(values: dict[str, int | float | str | None]) -> str:
    formatted = []
    for name, value in values.items():
        if value is None:
            text = "unavailable"
        elif isinstance(value, float):
            text = f"{value:.7g}"
        else:
            text = str(value)
        formatted.append(f"{name}={text}")
    return " values=[" + ", ".join(formatted) + "]"


def format_frame(
    frame: Nmea2000Frame,
    values: dict[str, int | float | str | None] | None = None,
) -> str:
    name = PGN_NAMES.get(frame.pgn, "Unknown PGN")
    timestamp = frame.timestamp or "-"
    direction = frame.direction or "-"
    destination = (
        f" destination={frame.destination}"
        if frame.destination is not None
        else ""
    )
    payload = " ".join(f"{byte:02X}" for byte in frame.payload)
    return (
        f"{timestamp} source={frame.source} direction={direction} "
        f"priority={frame.priority} PGN={frame.pgn} "
        f"(0x{frame.pgn:05X}) {name}"
        f"{destination} data=[{payload}]"
        f"{format_values(values) if values is not None else ''}"
    )


def sort_capture_records(
    records: list[tuple[int, str, int, str]],
) -> list[str]:
    return [record[3] for record in sorted(records, key=lambda item: item[:3])]


def _format_received_lines(
    line: str, decoder: FastPacketDecoder
) -> tuple[Nmea2000Frame, list[str]]:
    frame = parse_raw_frame(line)
    values = None if frame.pgn in FAST_PACKET_PGNS else decode_payload(
        frame.pgn, frame.payload
    )
    output = [format_frame(frame, values)]

    message = decoder.add(frame)
    if message is not None:
        payload = " ".join(f"{byte:02X}" for byte in message)
        values = decode_payload(frame.pgn, message)
        value_text = format_values(values) if values is not None else ""
        output.append(
            f"{frame.timestamp or '-'} source={frame.source} "
            f"direction={frame.direction or '-'} "
            f"Fast Packet complete: PGN={frame.pgn} "
            f"length={len(message)} data=[{payload}]"
            f"{value_text}"
        )
    return frame, output


def _print_capture(
    records: list[tuple[int, str, int, str]], errors: list[str], interval: float
) -> None:
    print(f"\n--- {interval:g}-second capture ---")
    sorted_lines = sort_capture_records(records)
    if not sorted_lines and not errors:
        print("No NMEA 2000 data received.")
    for line in sorted_lines:
        print(line)
    for error in errors:
        print(error)
    print("--- end capture ---")


def listen(host: str, port: int, interval: float) -> None:
    print(
        f"Collecting from {host}:{port} in {interval:g}-second windows; "
        "press Ctrl+C to stop."
    )
    records: list[tuple[int, str, int, str]] = []
    errors: list[str] = []
    sequence = 0
    decoder = FastPacketDecoder()
    read_buffer = bytearray()
    connection: socket.socket | None = None
    next_capture = time.monotonic() + interval
    retry_at = 0.0

    while True:
        now = time.monotonic()
        if now >= next_capture:
            _print_capture(records, errors, interval)
            records.clear()
            errors.clear()
            sequence = 0
            next_capture = now + interval

        if connection is None:
            if now < retry_at:
                time.sleep(min(retry_at - now, next_capture - now))
                continue
            try:
                connection = socket.create_connection(
                    (host, port), timeout=max(0.1, min(5.0, next_capture - now))
                )
                connection.setblocking(False)
                decoder = FastPacketDecoder()
                read_buffer.clear()
                print(f"Connected to {host}:{port}")
            except OSError as error:
                errors.append(f"Connection failed: {error}")
                retry_at = time.monotonic() + RECONNECT_DELAY_SECONDS
                continue

        try:
            readable, _, _ = select.select(
                [connection], [], [], max(0.0, next_capture - time.monotonic())
            )
            if not readable:
                continue
            chunk = connection.recv(4096)
        except (OSError, ValueError) as error:
            errors.append(f"Connection error: {error}")
            connection.close()
            connection = None
            retry_at = time.monotonic() + RECONNECT_DELAY_SECONDS
            continue

        if not chunk:
            if read_buffer.strip():
                lines = [bytes(read_buffer)]
            else:
                lines = []
            read_buffer.clear()
            connection.close()
            connection = None
            retry_at = time.monotonic() + RECONNECT_DELAY_SECONDS
        else:
            read_buffer.extend(chunk)
            lines = []
            while b"\n" in read_buffer:
                line, _, remainder = read_buffer.partition(b"\n")
                lines.append(line)
                read_buffer = bytearray(remainder)

        for raw_line in lines:
            line = raw_line.decode("ascii", errors="replace").strip()
            if not line:
                continue
            try:
                frame, output_lines = _format_received_lines(line, decoder)
            except ValueError as error:
                errors.append(f"Invalid frame: {line} ({error})")
                continue
            for output_line in output_lines:
                records.append(
                    (frame.source, frame.timestamp or "", sequence, output_line)
                )
                sequence += 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode NMEA 2000 frames from a Yacht Devices RAW TCP stream."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Gateway IP address")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("YD_Gateway_PORT_RAW", str(DEFAULT_PORT))),
        help="Gateway RAW TCP port (default: 3000)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=CAPTURE_INTERVAL_SECONDS,
        help="Capture and output interval in seconds (default: 10)",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    try:
        listen(args.host, args.port, args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())