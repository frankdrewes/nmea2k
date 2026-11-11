#!/usr/bin/env python3
"""Send fixed battery test data to a Yacht Devices YDWG-02 RAW TCP port."""

import argparse
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PORT = 3000
SEND_INTERVAL_SECONDS = 15
RECONNECT_DELAY_SECONDS = 2
NMEA2000_PRIORITY = 6


@dataclass(frozen=True)
class Battery:
    instance: int
    voltage: float
    current: float
    temperature_f: float
    state_of_charge: int
    state_of_health: int


BATTERIES = (
    Battery(1, 14.5, 14.0, 90.0, 45, 90),
    Battery(2, 15.6, 19.0, 101.0, 65, 95),
)


def build_can_identifier(pgn: int, source_address: int) -> int:
    if not 0 <= source_address <= 251:
        raise ValueError("Source address must be in the assignable range 0-251")
    if not 0 <= pgn <= 0x3FFFF:
        raise ValueError(f"PGN is outside the 18-bit range: {pgn}")
    return (NMEA2000_PRIORITY << 26) | (pgn << 8) | source_address


def format_raw_frame(pgn: int, source_address: int, payload: bytes) -> bytes:
    if not 1 <= len(payload) <= 8:
        raise ValueError("NMEA 2000 payload must contain 1 to 8 bytes")
    identifier = build_can_identifier(pgn, source_address)
    line = f"{identifier:08X} {' '.join(f'{byte:02X}' for byte in payload)}\r\n"
    return line.encode("ascii")


def encode_battery_status(battery: Battery) -> bytes:
    voltage_raw = round(battery.voltage * 100)
    current_raw = round(battery.current * 10)
    temperature_kelvin_raw = round((battery.temperature_f - 32) * 5 / 9 * 100 + 27315)

    if not 0 <= voltage_raw <= 0xFFFF:
        raise ValueError("Voltage is outside the PGN 127508 range")
    if not -0x8000 <= current_raw <= 0x7FFF:
        raise ValueError("Current is outside the PGN 127508 range")
    if not 0 <= temperature_kelvin_raw <= 0xFFFF:
        raise ValueError("Temperature is outside the PGN 127508 range")

    return (
        bytes((battery.instance,))
        + voltage_raw.to_bytes(2, "little")
        + current_raw.to_bytes(2, "little", signed=True)
        + temperature_kelvin_raw.to_bytes(2, "little")
        + b"\xFF"
    )


def encode_dc_detailed_status(battery: Battery) -> bytes:
    if not 0 <= battery.state_of_charge <= 100:
        raise ValueError("State of charge must be between 0 and 100 percent")
    if not 0 <= battery.state_of_health <= 100:
        raise ValueError("State of health must be between 0 and 100 percent")

    # DC type 0 is battery; remaining time and the reserved byte are unavailable.
    return bytes(
        (
            battery.instance,
            0,
            battery.state_of_charge,
            battery.state_of_health,
            0xFF,
            0xFF,
            0xFF,
            0xFF,
        )
    )


def build_test_frames(source_address: int) -> list[bytes]:
    frames = []
    for battery in BATTERIES:
        frames.append(
            format_raw_frame(
                127508, source_address, encode_battery_status(battery)
            )
        )
        frames.append(
            format_raw_frame(
                127506, source_address, encode_dc_detailed_status(battery)
            )
        )
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate fixed PGN 127508 and 127506 battery test messages."
    )
    parser.add_argument(
        "--source-address",
        type=lambda value: int(value, 0),
        default=(
            int(os.environ["YD_Gateway_SOURCE_ADDRESS"], 0)
            if os.getenv("YD_Gateway_SOURCE_ADDRESS")
            else None
        ),
        help="Unused NMEA 2000 source address (0-251), or set YD_Gateway_SOURCE_ADDRESS",
    )
    parser.add_argument(
        "--transmit",
        action="store_true",
        help="Transmit synthetic test readings to the gateway (default is dry-run)",
    )
    args = parser.parse_args()

    host = os.getenv("YD_Gateway_IP")
    if not host:
        parser.error("YD_Gateway_IP must be set in .env or the environment")
    try:
        port = int(os.getenv("YD_Gateway_PORT_RAW", str(DEFAULT_PORT)))
    except ValueError:
        parser.error("YD_Gateway_PORT_RAW must be a valid TCP port number")
    if not 1 <= port <= 65535:
        parser.error("YD_Gateway_PORT_RAW must be between 1 and 65535")
    if args.source_address is None:
        parser.error(
            "Set YD_Gateway_SOURCE_ADDRESS or pass --source-address with an "
            "unused NMEA 2000 source address (0-251)"
        )

    try:
        frames = build_test_frames(args.source_address)
    except ValueError as error:
        parser.error(str(error))

    if not args.transmit:
        print("Dry run: no data was sent.")
        print(f"Gateway: {host}:{port}")
        for frame in frames:
            print(frame.decode("ascii").rstrip())
        print("Review the frames, then add --transmit to send every 15 seconds.")
        return 0

    print(
        f"Sending synthetic battery test data to {host}:{port} every "
        f"{SEND_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop."
    )
    try:
        while True:
            sent_at = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                with socket.create_connection((host, port), timeout=5) as connection:
                    for frame in frames:
                        connection.sendall(frame)
                    print(f"[{sent_at}] Sent {len(frames)} battery frames:")
                    for frame in frames:
                        print(f"  {frame.decode('ascii').rstrip()}")
            except OSError as error:
                print(
                    f"[{sent_at}] Gateway connection failed ({error}); "
                    f"retrying in {RECONNECT_DELAY_SECONDS} seconds."
                )
                time.sleep(RECONNECT_DELAY_SECONDS)
                continue
            time.sleep(SEND_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
