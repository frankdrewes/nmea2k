#!/usr/bin/env python3
"""Identify NMEA 2000 devices from their Address Claim and Product Information."""

import argparse
import os
import select
import socket
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from log_raw import (
    FastPacketDecoder,
    Nmea2000Frame,
    PGN_NAMES,
    decode_payload,
    parse_raw_frame,
)

load_dotenv()

DEFAULT_HOST = os.getenv("YD_Gateway_IP", "192.168.30.162")
DEFAULT_PORT = int(os.getenv("YD_Gateway_PORT_RAW", "3000"))
DEFAULT_CAPTURE_SECONDS = 10
REQUEST_PGN = 59904
ADDRESS_CLAIM_PGN = 60928
PRODUCT_INFORMATION_PGN = 126996

MANUFACTURERS = {
    135: "Airmar",
    137: "Maretron",
    140: "Lowrance",
    144: "Mercury Marine",
    172: "Yanmar Marine",
    174: "Volvo Penta",
    229: "Garmin",
    257: "Honda Motor",
    273: "Actisense",
    275: "Navico",
    295: "BEP Marine",
    358: "Victron Energy",
    381: "B&G",
    419: "Fusion Electronics",
    437: "Digital Yacht",
    586: "Suzuki Motor",
    717: "Yacht Devices",
    1851: "Raymarine",
}

INDUSTRY_GROUPS = {0: "Global", 4: "Marine Industry"}
DEVICE_CLASSES = {
    10: "System tools",
    20: "Safety systems",
    25: "Internetwork device",
    30: "Electrical distribution",
    35: "Electrical generation",
    40: "Steering and control surfaces",
    50: "Propulsion",
    60: "Navigation",
    70: "Communication",
    75: "Sensor communication interface",
    80: "Instrumentation/general systems",
    85: "External environment",
    90: "Internal environment",
    100: "Deck and cargo",
    110: "Human interface",
    120: "Display",
    125: "Entertainment",
}


@dataclass
class SourceInfo:
    observed_pgns: set[int] = field(default_factory=set)
    address_claim: dict[str, int | str] | None = None
    product_info: dict[str, int | str] | None = None
    manufacturer_hint: str | None = None
    manufacturer_code_hint: int | None = None


def decode_address_claim(payload: bytes) -> dict[str, int | str]:
    if len(payload) != 8:
        raise ValueError("PGN 60928 Address Claim must contain 8 bytes")

    name = int.from_bytes(payload, "little")
    manufacturer_code = (name >> 21) & 0x07FF
    industry_code = (name >> 60) & 0x07
    device_class = (name >> 49) & 0x7F
    return {
        "unique_number": name & 0x1FFFFF,
        "manufacturer_code": manufacturer_code,
        "manufacturer": MANUFACTURERS.get(
            manufacturer_code, f"Unknown manufacturer ({manufacturer_code})"
        ),
        "ecu_instance": (name >> 32) & 0x07,
        "function_instance": (name >> 35) & 0x1F,
        "device_function": (name >> 40) & 0xFF,
        "device_class": DEVICE_CLASSES.get(
            device_class, f"Unknown class ({device_class})"
        ),
        "system_instance": (name >> 56) & 0x0F,
        "industry_group": INDUSTRY_GROUPS.get(
            industry_code, f"Unknown industry ({industry_code})"
        ),
        "arbitrary_address_capable": (name >> 63) & 0x01,
    }


def _decode_fixed_string(value: bytes) -> str:
    value = value.split(b"\x00", 1)[0].split(b"\xff", 1)[0]
    return value.decode("ascii", errors="replace").strip()


def decode_product_information(payload: bytes) -> dict[str, int | str]:
    if len(payload) < 132:
        raise ValueError("PGN 126996 Product Information must contain at least 132 bytes")

    nmea_version = int.from_bytes(payload[0:2], "little")
    product_info: dict[str, int | str] = {
        "nmea_2000_version": f"{nmea_version // 1000}.{nmea_version % 1000:03d}",
        "product_code": int.from_bytes(payload[2:4], "little"),
        "model_id": _decode_fixed_string(payload[4:36]),
        "software_version": _decode_fixed_string(payload[36:68]),
        "model_version": _decode_fixed_string(payload[68:100]),
        "serial_code": _decode_fixed_string(payload[100:132]),
    }
    if len(payload) >= 134:
        certification_level = payload[132]
        product_info["certification_level"] = {
            0: "Level A",
            1: "Level B",
        }.get(certification_level, f"unknown ({certification_level})")
        product_info["load_equivalency"] = payload[133]
    return product_info


def _request_frame(source: int, destination: int, requested_pgn: int) -> bytes:
    identifier = (6 << 26) | (REQUEST_PGN << 8) | (destination << 8) | source
    payload = requested_pgn.to_bytes(3, "little") + b"\xFF" * 5
    return (
        f"{identifier:08X} "
        + " ".join(f"{byte:02X}" for byte in payload)
        + "\r\n"
    ).encode("ascii")


def create_source_table(sources: dict[int, SourceInfo]) -> Table:
    table = Table(title="NMEA 2000 Source Report", show_lines=True)
    table.add_column("Source", justify="right", no_wrap=True)
    table.add_column("Section", no_wrap=True)
    table.add_column("Item")
    table.add_column("Value")

    for source in sorted(sources):
        info = sources[source]
        claim = info.address_claim
        product = info.product_info

        def add_row(section: str, item: str, value: object) -> None:
            table.add_row(str(source), section, item, str(value))

        if claim is not None:
            claim_fields = (
                ("Manufacturer", claim["manufacturer"]),
                ("Manufacturer code", claim["manufacturer_code"]),
                ("Unique number", claim["unique_number"]),
                ("ECU instance", claim["ecu_instance"]),
                ("Function instance", claim["function_instance"]),
                ("Device function", claim["device_function"]),
                ("Device class", claim["device_class"]),
                ("System instance", claim["system_instance"]),
                ("Industry group", claim["industry_group"]),
                (
                    "Arbitrary address capable",
                    "Yes" if claim["arbitrary_address_capable"] else "No",
                ),
            )
            for item, value in claim_fields:
                add_row("Address Claim", item, value)
        else:
            add_row(
                "Address Claim",
                "Manufacturer",
                info.manufacturer_hint or "Not identified",
            )
            if info.manufacturer_code_hint is not None:
                add_row(
                    "Address Claim",
                    "Manufacturer code (proprietary PGN)",
                    info.manufacturer_code_hint,
                )

        product_fields = (
            ("Model", "model_id"),
            ("Product code", "product_code"),
            ("Model version", "model_version"),
            ("Software version", "software_version"),
            ("Serial code", "serial_code"),
            ("NMEA 2000 version", "nmea_2000_version"),
            ("Certification level", "certification_level"),
            ("Load equivalency", "load_equivalency"),
        )
        for label, key in product_fields:
            value = product.get(key, "Not supplied") if product else "Not received"
            add_row("Product Information", label, value or "Not supplied")

        if info.observed_pgns:
            for pgn in sorted(info.observed_pgns):
                add_row(
                    "Observed PGN",
                    f"PGN {pgn}",
                    PGN_NAMES.get(pgn, "Unknown PGN description"),
                )
        else:
            add_row("Observed PGN", "PGNs", "None observed")
    return table


def identify_sources(
    host: str,
    port: int,
    capture_seconds: float,
    request_info: bool = False,
    source_address: int | None = None,
) -> dict[int, SourceInfo]:
    sources: dict[int, SourceInfo] = {}
    requested_sources: set[int] = set()
    packet_decoder = FastPacketDecoder()
    read_buffer = bytearray()
    deadline = time.monotonic() + capture_seconds

    with socket.create_connection((host, port), timeout=5) as connection:
        connection.setblocking(False)
        if request_info and source_address is not None:
            connection.setblocking(True)
            connection.settimeout(1)
            connection.sendall(
                _request_frame(source_address, 255, ADDRESS_CLAIM_PGN)
            )
            connection.setblocking(False)

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            readable, _, _ = select.select([connection], [], [], remaining)
            if not readable:
                break
            try:
                chunk = connection.recv(4096)
            except BlockingIOError:
                continue
            if not chunk:
                break

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
                    frame = parse_raw_frame(line)
                except ValueError:
                    continue
                if frame.direction == "T" or frame.source > 251:
                    continue

                info = sources.setdefault(frame.source, SourceInfo())
                info.observed_pgns.add(frame.pgn)
                if frame.pgn == ADDRESS_CLAIM_PGN:
                    try:
                        info.address_claim = decode_address_claim(frame.payload)
                    except ValueError:
                        pass
                elif frame.pgn in {65359, 65360, 65379}:
                    manufacturer_values = decode_payload(frame.pgn, frame.payload)
                    if manufacturer_values is not None:
                        info.manufacturer_hint = str(
                            manufacturer_values["manufacturer"]
                        )
                        info.manufacturer_code_hint = int(
                            manufacturer_values["manufacturer_code"]
                        )

                if request_info and source_address is not None:
                    if (
                        frame.source != source_address
                        and frame.source not in requested_sources
                        and frame.pgn != PRODUCT_INFORMATION_PGN
                    ):
                        connection.setblocking(True)
                        connection.settimeout(1)
                        connection.sendall(
                            _request_frame(
                                source_address, frame.source, PRODUCT_INFORMATION_PGN
                            )
                        )
                        connection.setblocking(False)
                        requested_sources.add(frame.source)

                product_payload = packet_decoder.add(frame)
                if product_payload is not None:
                    manufacturer_values = decode_payload(frame.pgn, product_payload)
                    if manufacturer_values is not None and "manufacturer" in manufacturer_values:
                        info.manufacturer_hint = str(
                            manufacturer_values["manufacturer"]
                        )
                        info.manufacturer_code_hint = int(
                            manufacturer_values["manufacturer_code"]
                        )
                    if frame.pgn == PRODUCT_INFORMATION_PGN:
                        try:
                            info.product_info = decode_product_information(product_payload)
                        except ValueError:
                            pass

        if read_buffer.strip():
            try:
                frame = parse_raw_frame(
                    read_buffer.decode("ascii", errors="replace").strip()
                )
            except ValueError:
                frame = None
            if frame is not None and frame.direction != "T" and frame.source <= 251:
                info = sources.setdefault(frame.source, SourceInfo())
                info.observed_pgns.add(frame.pgn)
                if frame.pgn == ADDRESS_CLAIM_PGN:
                    try:
                        info.address_claim = decode_address_claim(frame.payload)
                    except ValueError:
                        pass

    return sources


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Identify NMEA 2000 source addresses, manufacturers, and models."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="Gateway IP address")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Gateway RAW TCP port (default: 3000)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_CAPTURE_SECONDS,
        help="How long to collect source data (default: 10 seconds)",
    )
    parser.add_argument(
        "--request-info",
        action="store_true",
        help="Request Address Claims and Product Information from observed devices",
    )
    parser.add_argument(
        "--source-address",
        type=lambda value: int(value, 0),
        help="Your unused NMEA 2000 source address, required with --request-info",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.seconds <= 0:
        parser.error("--seconds must be greater than zero")
    if args.request_info and args.source_address is None:
        parser.error("--source-address is required with --request-info")
    if args.source_address is not None and not 0 <= args.source_address <= 251:
        parser.error("--source-address must be between 0 and 251")

    try:
        sources = identify_sources(
            args.host,
            args.port,
            args.seconds,
            args.request_info,
            args.source_address,
        )
    except OSError as error:
        parser.exit(1, f"Gateway connection failed: {error}\n")

    console = Console()
    console.print(f"Observed {len(sources)} source(s) in {args.seconds:g} seconds.")
    if not sources:
        console.print("No NMEA 2000 sources observed.")
    console.print(create_source_table(sources))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())