#!/usr/bin/env python3
"""Add human-readable MAX77972 descriptions to a Total Phase I2C CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_MAP = Path(__file__).with_name("register_map.json")
HEX_BYTE_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{1,2})(\*)?$")


class DecodeError(ValueError):
    """Raised when capture or map input cannot be decoded safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append a Description column to a Total Phase Data Center I2C CSV, "
            "decoding MAX77972 traffic."
        )
    )
    parser.add_argument("input", type=Path, help="Total Phase CSV capture")
    parser.add_argument(
        "-o", "--output", type=Path, help="output CSV (default: INPUT_decoded.csv)"
    )
    parser.add_argument(
        "-m",
        "--map",
        dest="map_path",
        type=Path,
        default=DEFAULT_MAP,
        help=f"register-map JSON (default: {DEFAULT_MAP.name})",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="replace the input file atomically instead of creating a decoded copy",
    )
    return parser.parse_args()


def normalize_decode_values(values: dict[str, str]) -> dict[int, str]:
    """Accept either hex ("0x1F") or decimal ("31") keys in a decode table."""
    return {int(key, 0): text for key, text in values.items()}


def load_map(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except OSError as exc:
        raise DecodeError(f"cannot read register map {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DecodeError(f"invalid JSON in register map {path}: {exc}") from exc

    try:
        config["addresses"] = {
            int(key, 0): value for key, value in config["addresses"].items()
        }
        config["registers"] = {
            int(key, 0): value for key, value in config["registers"].items()
        }
        for register in config["registers"].values():
            if "values" in register:
                register["values"] = normalize_decode_values(register["values"])
            for field in register.get("fields", []):
                if "values" in field:
                    field["values"] = normalize_decode_values(field["values"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecodeError(f"invalid register-map schema in {path}") from exc
    return config


def decoded_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_decoded{input_path.suffix}")


def normalized_header_name(name: str) -> str:
    return name.lstrip("\ufeff# ").strip().lower()


def find_columns(header: list[str]) -> dict[str, int]:
    normalized = [normalized_header_name(name) for name in header]

    def locate(*aliases: str) -> int:
        for alias in aliases:
            if alias in normalized:
                return normalized.index(alias)
        raise DecodeError(
            f"required CSV column missing ({'/'.join(aliases)}); "
            f"found: {', '.join(header)}"
        )

    return {
        "address": locate("addr", "address"),
        "record": locate("record"),
        "data": locate("data"),
    }


def parse_address(text: str) -> tuple[int | None, bool]:
    cleaned = text.strip()
    if not cleaned or cleaned.lower() == "none":
        return None, False
    nacked = cleaned.endswith("*")
    cleaned = cleaned.rstrip("*")
    try:
        # Total Phase displays I2C addresses as hexadecimal without a 0x prefix.
        return int(cleaned, 16), nacked
    except ValueError as exc:
        raise DecodeError(f"invalid I2C address {text!r}") from exc


def parse_data(text: str) -> tuple[list[int], list[bool]]:
    cleaned = text.strip().strip("[]")
    if not cleaned:
        return [], []

    values: list[int] = []
    nacks: list[bool] = []
    for token in re.split(r"[\s,]+", cleaned):
        if not token:
            continue
        match = HEX_BYTE_RE.fullmatch(token)
        if not match:
            raise DecodeError(f"invalid data byte {token!r} in {text!r}")
        values.append(int(match.group(1), 16))
        nacks.append(bool(match.group(2)))
    return values, nacks


def to_signed(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def compact_number(value: float) -> str:
    return f"{value:.7g}"


def format_field(raw: int, field: dict[str, Any]) -> str:
    meaning = field.get("values", {}).get(raw)
    if meaning is not None:
        return f"{field['name']}={raw} ({meaning})"
    if "scale" in field:
        scaled = raw * float(field["scale"]) + float(field.get("offset", 0.0))
        return f"{field['name']}={compact_number(scaled)} {field['unit']}"
    return f"{field['name']}={raw}"


def format_bits(value: int, register: dict[str, Any]) -> str:
    decoded: list[str] = []
    for field in register.get("fields", []):
        width = int(field.get("width", 1))
        raw = (value >> int(field["lsb"])) & ((1 << width) - 1)
        if field.get("signed"):
            raw = to_signed(raw, width)

        if raw == 0 and width == 1 and not field.get("show_zero", False):
            continue
        decoded.append(format_field(raw, field))
    return ", ".join(decoded) if decoded else "no named flags set"


def format_charging_voltage(value: int) -> str:
    if value <= 0xAEFF:
        millivolts = 3400.0
    elif value <= 0xB17F:
        millivolts = 3500.0
    elif value <= 0xB3FF:
        millivolts = 3550.0
    elif value <= 0xCAAF:
        millivolts = value * 0.078125
    elif value <= 0xCCFF:
        millivolts = 4050.0
    elif value <= 0xE800:
        millivolts = value * 0.078125
    else:
        millivolts = 4640.0
    return f"{compact_number(millivolts)} mV"


def format_value(value: int, register: dict[str, Any]) -> str:
    raw = f"0x{value:04X}"
    value_format = register.get("format", "hex")

    if value_format == "scaled":
        numeric = to_signed(value, 16) if register.get("signed") else value
        scaled = numeric * float(register["scale"])
        return f"{raw} ({compact_number(scaled)} {register['unit']})"
    if value_format == "bits":
        return f"{raw} ({format_bits(value, register)})"
    if value_format == "enum":
        meaning = register.get("values", {}).get(value)
        return f"{raw} ({meaning})" if meaning else raw
    if value_format == "charging_voltage":
        return f"{raw} ({format_charging_voltage(value)})"
    return raw


class MAX77972Decoder:
    def __init__(self, config: dict[str, Any]) -> None:
        self.addresses: dict[int, dict[str, Any]] = config["addresses"]
        self.registers: dict[int, dict[str, Any]] = config["registers"]
        self.last_pointer: dict[int, int] = {}

    def internal_offset(self, address: int, pointer: int) -> int:
        return pointer + int(self.addresses[address].get("offset_bias", 0))

    def register_label(self, address: int, pointer: int) -> str:
        offset = self.internal_offset(address, pointer)
        register = self.registers.get(offset)
        name = register["name"] if register else "unknown register"
        return f"{name} (0x{offset:03X})"

    def format_words(
        self, direction: str, address: int, pointer: int, data: list[int]
    ) -> str:
        entries: list[str] = []
        complete_bytes = len(data) - (len(data) % 2)
        for index in range(0, complete_bytes, 2):
            current_pointer = (pointer + index // 2) & 0xFF
            offset = self.internal_offset(address, current_pointer)
            register = self.registers.get(
                offset, {"name": "unknown register", "format": "hex"}
            )
            value = data[index] | (data[index + 1] << 8)
            entries.append(
                f"{register['name']} (0x{offset:03X}) = "
                f"{format_value(value, register)}"
            )

        if len(data) % 2:
            entries.append(f"incomplete trailing byte 0x{data[-1]:02X}")
        if not entries:
            entries.append("no value bytes")
        return f"{direction} " + "; ".join(entries)

    def describe(
        self,
        address_text: str,
        record: str,
        data_text: str,
    ) -> str:
        address, address_nacked = parse_address(address_text)
        record_lower = record.lower()

        if address is None:
            detail = f" {data_text.strip()}" if data_text.strip() else ""
            return f"{record.strip()}{detail}".strip()

        try:
            data, data_nacks = parse_data(data_text)
        except DecodeError as exc:
            return f"Unparsed {record.strip()} at 0x{address:02X}: {exc}"

        if address not in self.addresses:
            payload = " ".join(f"{byte:02X}" for byte in data) or "(no data)"
            nack = "; address NACK" if address_nacked else ""
            return f"{record.strip()} at 0x{address:02X}: {payload}{nack}"

        if address_nacked:
            return f"{record.strip()} at 0x{address:02X}: address NACK"

        if "write" in record_lower:
            if not data:
                return f"Write at 0x{address:02X}: no data"
            pointer = data[0]
            self.last_pointer[address] = pointer
            if len(data) == 1:
                return f"Set pointer {self.register_label(address, pointer)}"
            description = self.format_words("Write", address, pointer, data[1:])
            if any(data_nacks):
                description += "; data byte NACK"
            return description

        if "read" in record_lower:
            if address not in self.last_pointer:
                payload = " ".join(f"{byte:02X}" for byte in data) or "(no data)"
                return (
                    f"Read at 0x{address:02X} with unknown pointer: {payload}"
                )
            # A star on the final read byte is the controller's normal terminating
            # NACK, not a MAX77972 error.
            return self.format_words(
                "Read", address, self.last_pointer[address], data
            )

        payload = " ".join(f"{byte:02X}" for byte in data)
        return f"{record.strip()} at 0x{address:02X}: {payload}".rstrip(": ")


def decode_capture(
    input_path: Path, output_path: Path, config: dict[str, Any]
) -> int:
    decoder = MAX77972Decoder(config)
    decoded_count = 0
    header_found = False
    columns: dict[str, int] = {}

    try:
        source = input_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DecodeError(f"cannot read capture {input_path}: {exc}") from exc

    try:
        destination = output_path.open("w", encoding="utf-8", newline="")
    except OSError as exc:
        source.close()
        raise DecodeError(f"cannot create output {output_path}: {exc}") from exc

    with source, destination:
        reader = csv.reader(source)
        writer = csv.writer(destination, lineterminator="\n")

        for row_number, row in enumerate(reader, start=1):
            if not header_found:
                normalized = [normalized_header_name(cell) for cell in row]
                if "data" in normalized and (
                    "addr" in normalized or "address" in normalized
                ):
                    columns = find_columns(row)
                    row.insert(columns["data"] + 1, "Description")
                    writer.writerow(row)
                    header_found = True
                else:
                    writer.writerow(row)
                continue

            required_index = max(columns.values())
            if len(row) <= required_index:
                row.extend([""] * (required_index + 1 - len(row)))

            try:
                description = decoder.describe(
                    row[columns["address"]],
                    row[columns["record"]],
                    row[columns["data"]],
                )
            except DecodeError as exc:
                description = f"Decode error on CSV row {row_number}: {exc}"

            row.insert(columns["data"] + 1, description)
            writer.writerow(row)
            if description:
                decoded_count += 1

    if not header_found:
        output_path.unlink(missing_ok=True)
        raise DecodeError(
            "could not find a Total Phase header containing Addr and Data"
        )
    return decoded_count


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()

    if args.in_place and args.output:
        print("error: --in-place cannot be combined with --output", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"error: input CSV not found: {input_path}", file=sys.stderr)
        return 2

    try:
        config = load_map(args.map_path.resolve())
        if args.in_place:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{input_path.stem}_",
                suffix=input_path.suffix,
                dir=input_path.parent,
            )
            os.close(fd)
            output_path = Path(temp_name)
        else:
            output_path = (
                args.output.resolve()
                if args.output
                else decoded_output_path(input_path)
            )
            if output_path == input_path:
                raise DecodeError(
                    "refusing to overwrite input; use --in-place explicitly"
                )

        count = decode_capture(input_path, output_path, config)
        if args.in_place:
            output_path.replace(input_path)
            output_path = input_path
    except DecodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Decoded {count} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
