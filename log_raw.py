#!/usr/bin/env python3
"""Listen to a Yacht Devices RAW TCP stream and decode NMEA 2000 frames."""

import argparse
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
    127237: "Heading/Track Control",
    127245: "Rudder",
    127250: "Vessel Heading",
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


def format_frame(frame: Nmea2000Frame) -> str:
    name = PGN_NAMES.get(frame.pgn, "Unknown PGN")
    prefix = f"{frame.timestamp} {frame.direction} " if frame.timestamp else ""
    destination = (
        f" destination={frame.destination}"
        if frame.destination is not None
        else ""
    )
    payload = " ".join(f"{byte:02X}" for byte in frame.payload)
    return (
        f"{prefix}priority={frame.priority} PGN={frame.pgn} "
        f"(0x{frame.pgn:05X}) {name} source={frame.source}"
        f"{destination} data=[{payload}]"
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

                        print(format_frame(frame))
                        message = decoder.add(frame)
                        if message is not None:
                            payload = " ".join(f"{byte:02X}" for byte in message)
                            print(
                                f"  Fast Packet complete: PGN={frame.pgn} "
                                f"length={len(message)} data=[{payload}]"
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