"""
BIN File Reader/Writer
Reads and writes binary ECU/TCU files using XDF definitions.
"""

import struct
from typing import Any, Optional
from xdf_parser import XdfFile, TableDef, ConstantDef, AxisDef, XdfDefaults


def _read_element(bin_data: bytes, address: int, size_bits: int,
                  signed: bool = False, little_endian: bool = True) -> int | float:
    """Read a single value from binary data."""
    size_bytes = size_bits // 8
    if address + size_bytes > len(bin_data):
        raise IndexError(f"Address 0x{address:X} + {size_bytes} bytes exceeds BIN size 0x{len(bin_data):X}")

    raw = bin_data[address:address + size_bytes]
    endian = "<" if little_endian else ">"

    if size_bytes == 1:
        fmt = "b" if signed else "B"
    elif size_bytes == 2:
        fmt = "h" if signed else "H"
    elif size_bytes == 4:
        fmt = "i" if signed else "I"
    else:
        raise ValueError(f"Unsupported element size: {size_bits} bits")

    return struct.unpack(endian + fmt, raw)[0]


def _write_element(bin_data: bytearray, address: int, size_bits: int,
                   value: int, signed: bool = False, little_endian: bool = True):
    """Write a single value to binary data."""
    size_bytes = size_bits // 8
    endian = "<" if little_endian else ">"

    # Clamp to valid range for the element size
    if signed:
        lo = -(1 << (size_bits - 1))
        hi = (1 << (size_bits - 1)) - 1
    else:
        lo = 0
        hi = (1 << size_bits) - 1
    value = max(lo, min(hi, int(value)))

    if size_bytes == 1:
        fmt = "b" if signed else "B"
    elif size_bytes == 2:
        fmt = "h" if signed else "H"
    elif size_bytes == 4:
        fmt = "i" if signed else "I"
    else:
        raise ValueError(f"Unsupported element size: {size_bits} bits")

    struct.pack_into(endian + fmt, bin_data, address, value)


def _is_signed(typeflags: int, defaults: XdfDefaults) -> bool:
    """Determine if data is signed based on typeflags and defaults.

    mmedtypeflags bit 0 (0x01) = signed. When typeflags is non-zero,
    the bit encodes the answer directly. Only fall back to DEFAULTS
    when typeflags is 0 (meaning no override specified).
    """
    if typeflags == 0:
        return defaults.signed
    return bool(typeflags & 0x01)


def _is_little_endian(typeflags: int, defaults: XdfDefaults) -> bool:
        """Determine endianness from typeflags and defaults.

        mmedtypeflags bit layout (confirmed from bri3d/a2l2xdf and workspace XDFs):
            - bit 0 (0x01): signed
            - bit 1 (0x02): LSB-first / little-endian
            - bit 2 (0x04): has embedded data address (NOT endianness)
            - bit 16 (0x10000): IEEE float32

        When typeflags is non-zero, the bits encode the answer directly.
        When typeflags is 0, fall back to DEFAULTS.
        """
        if typeflags == 0:
                return defaults.lsb_first
        return bool(typeflags & 0x02)


class BinFile:
    """Represents a BIN file with its associated XDF definitions."""

    def __init__(self, filepath: str, xdf: XdfFile):
        self.filepath = filepath
        self.xdf = xdf
        with open(filepath, "rb") as f:
            self._data = bytearray(f.read())

    def save(self, filepath: Optional[str] = None):
        """Save the BIN file."""
        out = filepath or self.filepath
        with open(out, "wb") as f:
            f.write(self._data)

    @property
    def size(self) -> int:
        return len(self._data)

    def read_raw(self, address: int, length: int) -> bytes:
        """Read raw bytes from the BIN."""
        return bytes(self._data[address:address + length])

    def write_raw(self, address: int, data: bytes):
        """Write raw bytes to the BIN."""
        self._data[address:address + len(data)] = data

    def read_element(self, address: int, size_bits: int,
                     signed: bool = False, little_endian: bool = True) -> int | float:
        """Read a single element."""
        return _read_element(self._data, address, size_bits, signed, little_endian)

    def write_element(self, address: int, size_bits: int, value: int,
                      signed: bool = False, little_endian: bool = True):
        """Write a single element."""
        _write_element(self._data, address, size_bits, value, signed, little_endian)

    def _get_axis_data(self, axis: AxisDef) -> list[list[float]]:
        """Read axis data and return display values."""
        if axis.is_label_based:
            # Label-based axis
            count = axis.index_count or max(axis.labels.keys(), default=0) + 1
            result = []
            for i in range(count):
                label = axis.labels.get(i, str(i))
                try:
                    result.append([float(label)])
                except ValueError:
                    result.append([label])
            return result

        if axis.address is None:
            return []

        defaults = self.xdf.header.defaults
        signed = _is_signed(axis.typeflags, defaults)
        le = _is_little_endian(axis.typeflags, defaults)
        count = axis.col_count or axis.index_count
        size = axis.element_size_bytes

        values = []
        for i in range(count):
            addr = axis.address + i * size
            raw = _read_element(self._data, addr, axis.element_size_bits, signed, le)
            display = axis.math.forward(float(raw)) if axis.math else float(raw)
            values.append([display])

        return values

    def _write_axis_data(self, axis: AxisDef, values: list[float]):
        """Write axis display values back to BIN."""
        if axis.is_label_based or axis.address is None:
            return

        defaults = self.xdf.header.defaults
        signed = _is_signed(axis.typeflags, defaults)
        le = _is_little_endian(axis.typeflags, defaults)
        count = axis.col_count or axis.index_count
        size = axis.element_size_bytes

        for i in range(min(count, len(values))):
            addr = axis.address + i * size
            raw = int(round(axis.math.inverse(values[i]))) if axis.math else int(round(values[i]))
            _write_element(self._data, addr, axis.element_size_bits, raw, signed, le)

    def read_table(self, table: TableDef) -> dict:
        """
        Read a table's data from the BIN.

        Returns dict with:
            'title': table title
            'x_axis': list of x-axis display values
            'y_axis': list of y-axis display values
            'data': 2D list of z-axis display values [rows][cols]
            'units': z-axis units
        """
        result = {
            "title": table.title,
            "x_axis": [],
            "y_axis": [],
            "data": [],
            "units": table.z_axis.units if table.z_axis else "",
        }

        # Read X axis
        if table.x_axis:
            x_data = self._get_axis_data(table.x_axis)
            result["x_axis"] = [v[0] for v in x_data]

        # Read Y axis
        if table.y_axis:
            y_data = self._get_axis_data(table.y_axis)
            result["y_axis"] = [v[0] for v in y_data]

        # Read Z data
        if table.z_axis and table.z_axis.address is not None:
            defaults = self.xdf.header.defaults
            signed = _is_signed(table.z_axis.typeflags, defaults)
            le = _is_little_endian(table.z_axis.typeflags, defaults)
            rows = table.z_axis.row_count
            cols = table.z_axis.col_count
            size = table.z_axis.element_size_bytes

            data = []
            for r in range(rows):
                row = []
                for c in range(cols):
                    addr = table.z_axis.address + (c * rows + r) * size
                    raw = _read_element(self._data, addr, table.z_axis.element_size_bits, signed, le)
                    display = table.z_axis.math.forward(float(raw)) if table.z_axis.math else float(raw)
                    row.append(round(display, table.z_axis.decimal_pl))
                data.append(row)
            result["data"] = data

        return result

    @staticmethod
    def format_table(data: dict) -> str:
        """Format table data as a compact, AI-readable text grid."""
        title = data["title"]
        units = data.get("units", "")
        x_axis = data.get("x_axis", [])
        y_axis = data.get("y_axis", [])
        rows = data.get("data", [])

        if not rows:
            return f"{title}: (empty)"

        # Determine column widths
        # Header row: X-axis values
        def _fmt(v):
            if isinstance(v, (int, float)):
                return f"{v:g}"
            return str(v)
        row_labels = [_fmt(v) for v in y_axis] if y_axis else [str(i) for i in range(len(rows))]
        col_labels = [_fmt(v) for v in x_axis] if x_axis else [str(i) for i in range(len(rows[0]))]

        label_w = max(len(l) for l in row_labels) if row_labels else 0
        col_w = max(max(len(l) for l in col_labels), 8) if col_labels else 8

        lines = []
        header = " " * (label_w + 1) + "  ".join(f"{l:>{col_w}}" for l in col_labels)
        lines.append(f"{title} ({units})" if units else title)
        lines.append(header)

        for i, row in enumerate(rows):
            label = row_labels[i] if i < len(row_labels) else str(i)
            vals = "  ".join(f"{v:>{col_w}.2f}" for v in row)
            lines.append(f"{label:>{label_w}}: {vals}")

        return "\n".join(lines)
    def write_table(self, table: TableDef, data: list[list[float]],
                    x_axis: Optional[list[float]] = None,
                    y_axis: Optional[list[float]] = None):
        """
        Write table data to the BIN.

        Args:
            table: Table definition from XDF
            data: 2D list of display values [rows][cols]
            x_axis: Optional new x-axis display values
            y_axis: Optional new y-axis display values
        """
        defaults = self.xdf.header.defaults

        # Write Z data
        if table.z_axis and table.z_axis.address is not None:
            signed = _is_signed(table.z_axis.typeflags, defaults)
            le = _is_little_endian(table.z_axis.typeflags, defaults)
            rows = table.z_axis.row_count
            cols = table.z_axis.col_count
            size = table.z_axis.element_size_bytes

            for r in range(min(rows, len(data))):
                for c in range(min(cols, len(data[r]))):
                    addr = table.z_axis.address + (c * rows + r) * size
                    raw = int(round(table.z_axis.math.inverse(data[r][c]))) if table.z_axis.math else int(round(data[r][c]))
                    _write_element(self._data, addr, table.z_axis.element_size_bits, raw, signed, le)

        # Write X axis
        if x_axis and table.x_axis:
            self._write_axis_data(table.x_axis, x_axis)

        # Write Y axis
        if y_axis and table.y_axis:
            self._write_axis_data(table.y_axis, y_axis)

    def read_scalar(self, const: ConstantDef) -> dict:
        """Read a scalar/constant value."""
        if const.address is None:
            return {"title": const.title, "value": None, "units": const.units}

        defaults = self.xdf.header.defaults
        signed = _is_signed(const.typeflags, defaults)
        le = _is_little_endian(const.typeflags, defaults)

        raw = _read_element(self._data, const.address, const.element_size_bits, signed, le)
        display = const.math.forward(float(raw)) if const.math else float(raw)

        return {
            "title": const.title,
            "value": round(display, const.decimal_pl),
            "units": const.units,
            "raw": raw,
        }

    def write_scalar(self, const: ConstantDef, display_value: float):
        """Write a scalar/constant value."""
        if const.address is None:
            return

        defaults = self.xdf.header.defaults
        signed = _is_signed(const.typeflags, defaults)
        le = _is_little_endian(const.typeflags, defaults)

        raw = int(round(const.math.inverse(display_value))) if const.math else int(round(display_value))
        _write_element(self._data, const.address, const.element_size_bits, raw, signed, le)

    def diff(self, other: "BinFile") -> list[dict]:
        """Compare this BIN with another and return differences in tables/scalars."""
        differences = []

        # Compare tables
        for table in self.xdf.tables:
            if table.z_axis and table.z_axis.address is not None:
                try:
                    self_data = self.read_table(table)
                    other_data = other.read_table(table)

                    if self_data["data"] != other_data["data"]:
                        differences.append({
                            "type": "table",
                            "title": table.title,
                            "self_data": self_data["data"],
                            "other_data": other_data["data"],
                        })
                except Exception:
                    pass  # skip tables with unreadable data (e.g. malformed MATH)

        # Compare scalars
        for const in self.xdf.constants:
            if const.address is not None:
                try:
                    self_val = self.read_scalar(const)
                    other_val = other.read_scalar(const)

                    if self_val["value"] != other_val["value"]:
                        differences.append({
                            "type": "scalar",
                            "title": const.title,
                            "self_value": self_val["value"],
                            "other_value": other_val["value"],
                            "units": const.units,
                        })
                except Exception:
                    pass

        return differences

    @staticmethod
    def _fmt_axis(axis: list, idx: int) -> str:
        """Format an axis value as a label string."""
        if idx < len(axis):
            val = axis[idx]
            if isinstance(val, (int, float)):
                return f"{val:g}"
            return str(val)
        return f"idx{idx}"

    def diff_summary(self, other: "BinFile") -> str:
        """Compare two BINs and return a lightweight summary of what changed.

        Just lists table names and change counts. Use diff_table() for details.
        """
        lines = []
        table_count = 0
        scalar_count = 0

        for table in self.xdf.tables:
            if table.z_axis and table.z_axis.address is not None:
                try:
                    self_data = self.read_table(table)
                    other_data = other.read_table(table)

                    if self_data["data"] == other_data["data"]:
                        continue

                    table_count += 1
                    s_rows = self_data["data"]
                    o_rows = other_data["data"]
                    total = len(s_rows) * len(s_rows[0]) if s_rows else 0

                    changed = 0
                    for r in range(len(s_rows)):
                        for c in range(len(s_rows[r])):
                            if r < len(o_rows) and c < len(o_rows[r]):
                                if s_rows[r][c] != o_rows[r][c]:
                                    changed += 1

                    units = self_data.get("units", "")
                    size = f"{len(s_rows)}x{len(s_rows[0])}" if s_rows else "?"
                    lines.append(f"  {table_count:3d}. {table.title} ({units}) [{changed}/{total} cells, {size}]")
                except Exception:
                    pass

        for const in self.xdf.constants:
            if const.address is not None:
                try:
                    self_val = self.read_scalar(const)
                    other_val = other.read_scalar(const)
                    if self_val["value"] != other_val["value"]:
                        scalar_count += 1
                        lines.append(f"  {table_count + scalar_count:3d}. {const.title} ({const.units}) [scalar]")
                except Exception:
                    pass

        if not lines:
            return "No differences found."

        header = f"Diff: {table_count} table{'s' if table_count != 1 else ''}, {scalar_count} scalar{'s' if scalar_count != 1 else ''} changed"
        return header + "\n" + "\n".join(lines)

    def diff_table(self, other: "BinFile", table_name: str) -> str:
        """Show full side-by-side diff for a specific table.

        Displays old and new values for every changed cell, with axis labels.
        """
        table = None
        name_lower = table_name.lower()
        for t in self.xdf.tables:
            if name_lower in t.title.lower():
                table = t
                break

        if table is None:
            return f"No table matching '{table_name}' found."

        try:
            self_data = self.read_table(table)
            other_data = other.read_table(table)
        except Exception as e:
            return f"Error reading table: {e}"

        if self_data["data"] == other_data["data"]:
            return f"{table.title}: No differences."

        units = self_data.get("units", "")
        x_axis = self_data.get("x_axis", [])
        y_axis = self_data.get("y_axis", [])
        s_rows = self_data["data"]
        o_rows = other_data["data"]

        lines = [f"=== {table.title} ({units}) ==="]

        # Find changed cells
        changes = []
        for r in range(len(s_rows)):
            for c in range(len(s_rows[r])):
                if r < len(o_rows) and c < len(o_rows[r]):
                    if s_rows[r][c] != o_rows[r][c]:
                        changes.append((r, c, s_rows[r][c], o_rows[r][c]))

        lines.append(f"{len(changes)} cell{'s' if len(changes) != 1 else ''} changed\n")

        # Group changes by row
        row_changes = {}
        for r, c, old, new in changes:
            row_changes.setdefault(r, []).append((c, old, new))

        for r, cell_changes in sorted(row_changes.items()):
            y_label = self._fmt_axis(y_axis, r)
            lines.append(f"Row [{y_label}]:")
            for c, old, new in cell_changes:
                x_label = self._fmt_axis(x_axis, c)
                lines.append(f"  {x_label}: {old} -> {new}")
            lines.append("")

        return "\n".join(lines)
