#!/usr/bin/env python3
"""Listen to a Yacht Devices RAW TCP stream and decode NMEA 2000 frames."""

import argparse
import math
import os
import socket
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOST = os.getenv("YD_Gateway_IP", "192.168.30.162")
DEFAULT_PORT = 3000
RECONNECT_DELAY_SECONDS = 2

PGN_NAMES = {
    126208: "NMEA Group Function",
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
    128267: "Water Depth",
    129025: "Position, Rapid Update",
    129026: "COG & SOG, Rapid Update",
    129029: "GNSS Position Data",
    130306: "Wind Data",
}

FAST_PACKET_PGNS = {122368, 126208, 127237, 127489, 129029}
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


def decode_payload(pgn: int, payload: bytes) -> dict[str, int | float | str | None] | None:
    """Decode common NMEA 2000 PGNs; return None for unsupported payloads."""
    if pgn == 127245 and len(payload) >= 8:
        angle_scale = 0.0001 * 180 / math.pi
        return {
            "instance": payload[1],
            "direction_order": None if payload[2] == 0xFF else payload[2],
            "angle_order_deg": _signed(payload, 3, 2, angle_scale),
            "rudder_position_deg": _signed(payload, 5, 2, angle_scale),
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


def listen(host: str, port: int) -> None:
    decoder = FastPacketDecoder()
    print(f"Listening to YD RAW TCP at {host}:{port}; press Ctrl+C to stop.")
    while True:
        try:
            with socket.create_connection((host, port), timeout=5) as connection:
                connection.settimeout(None)
                print(f"Connected to {host}:{port}")
                stream = connection.makefile("r", encoding="ascii", errors="replace")
                with stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        try:
                            frame = parse_raw_frame(line)
                        except ValueError as error:
                            print(f"Invalid frame: {line.rstrip()} ({error})")
                            continue

                        if frame.pgn in FAST_PACKET_PGNS:
                            values = None
                        else:
                            values = decode_payload(frame.pgn, frame.payload)
                        print(format_frame(frame, values))
                        message = decoder.add(frame)
                        if message is not None:
                            payload = " ".join(f"{byte:02X}" for byte in message)
                            values = decode_payload(frame.pgn, message)
                            value_text = (
                                format_values(values) if values is not None else ""
                            )
                            print(
                                f"{frame.timestamp or '-'} source={frame.source} "
                                f"direction={frame.direction or '-'} "
                                f"Fast Packet complete: PGN={frame.pgn} "
                                f"length={len(message)} data=[{payload}]"
                                f"{value_text}"
                            )
                print("Gateway closed the connection.")
        except OSError as error:
            print(f"Connection failed: {error}")

        print(f"Retrying in {RECONNECT_DELAY_SECONDS} seconds...")
        time.sleep(RECONNECT_DELAY_SECONDS)


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
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        listen(args.host, args.port)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())