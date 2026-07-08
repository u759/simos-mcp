def _balanced_parens(s: str) -> bool:
    """Check if a string has balanced parentheses."""
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0

"""
XDF File Parser
Parses XDF definition files to extract table/scalar definitions,
addresses, axis layouts, and MATH equations.
"""

import xml.etree.ElementTree as ET
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MathExpr:
    """A MATH equation that transforms raw BIN values to display values."""
    equation: str

    @staticmethod
    def _normalize_equation(eq: str) -> str:
        """Fix common XDF typos in MATH equations before eval."""
        # Fix '2.66667.0' style typos → '2.66667' (number.number.number → number.number)
        eq = re.sub(r'(\d+\.\d+)\.(\d+)', r'\1', eq)
        return eq

    def forward(self, raw_val: float) -> float:
        """Raw BIN value -> display value."""
        return eval(self._normalize_equation(self.equation),
                    {"__builtins__": {}}, {"X": raw_val, "x": raw_val})

    def inverse(self, display_val: float) -> float:
        """Display value -> raw BIN value (exact algebraic inversion).

        Supports all common linear XDF equation patterns.
        Raises ValueError for unsupported non-linear equations.
        """
        eq = self._normalize_equation(self.equation).strip()

        # Normalize: add spaces around operators to help regex matching
        # e.g. "X*0.000061035156-2" -> "X * 0.000061035156 - 2"
        eq = re.sub(r'(\d)([+\-])', r'\1 \2', eq)
        eq = re.sub(r'([+\-])(\d)', r'\1 \2', eq)
        eq = re.sub(r'\s+', ' ', eq).strip()

        # Simplify algebraic identities so regex patterns match
        # (1.0 * X) -> X, (0.0 * X) -> 0.0, X + 0.0 -> X, X - 0.0 -> X
        eq = re.sub(r'\(\s*1\.0*\s*\*\s*X\s*\)', 'X', eq)
        eq = re.sub(r'\(\s*0\.0*\s*\*\s*X\s*\)', '0.0', eq)
        eq = re.sub(r'\(\s*X\s*\*\s*1\.0*\s*\)', 'X', eq)
        eq = re.sub(r'\(\s*X\s*\*\s*0\.0*\s*\)', '0.0', eq)
        # Remove redundant no-ops: X + 0.0, X - 0.0, 0.0 + X
        eq = re.sub(r'X\s*\+\s*0\.0+', 'X', eq)
        eq = re.sub(r'X\s*\-\s*0\.0+', 'X', eq)
        eq = re.sub(r'0\.0+\s*\+\s*X', 'X', eq)
        eq = re.sub(r'0\.0+\s*\-\s*X', '-X', eq)
        # Remove now-redundant outer parens around a single term: (X) -> X
        eq = re.sub(r'\(\s*X\s*\)', 'X', eq)
        eq = re.sub(r'\(\s*(-?\d+\.?\d*)\s*\)', r'\1', eq)

        # Identity
        if eq == "X":
            return display_val

        # Normalize: strip outer parens for matching
        inner = eq
        while inner.startswith("(") and inner.endswith(")") and _balanced_parens(inner[1:-1]):
            inner = inner[1:-1].strip()

        # Pattern: X * a  or  a * X
        m = re.match(r'^X\s*\*\s*([\d.eE+\-]+)$', inner)
        if m:
            return display_val / float(m.group(1))
        m = re.match(r'^([\d.eE+\-]+)\s*\*\s*X$', inner)
        if m:
            return display_val / float(m.group(1))

        # Pattern: X / a
        m = re.match(r'^X\s*/\s*([\d.eE+\-]+)$', inner)
        if m:
            return display_val * float(m.group(1))

        # Pattern: X + a  or  a + X
        m = re.match(r'^X\s*\+\s*([\d.eE+\-]+)$', inner)
        if m:
            return display_val - float(m.group(1))
        m = re.match(r'^([\d.eE+\-]+)\s*\+\s*X$', inner)
        if m:
            return display_val - float(m.group(1))

        # Pattern: X - a
        m = re.match(r'^X\s*-\s*([\d.eE+\-]+)$', inner)
        if m:
            return display_val + float(m.group(1))

        # Pattern: a - X (note: NOT same as X - a)
        m = re.match(r'^([\d.eE+\-]+)\s*-\s*X$', inner)
        if m:
            return float(m.group(1)) - display_val

        # Pattern: (X * a) + b  or  X * a + b  (inner parens optional)
        m = re.match(r'^(?:\(?\s*X\s*\*\s*([\d.eE+\-]+)\s*\)?|[\d.eE+\-]+\s*\*\s*X)\s*\+\s*([\d.eE+\-]+)$', inner)
        if m:
            a = float(m.group(1))
            b = float(m.group(2))
            return (display_val - b) / a

        # Pattern: (X * a) - b  or  X * a - b  (inner parens optional)
        m = re.match(r'^(?:\(?\s*X\s*\*\s*([\d.eE+\-]+)\s*\)?|[\d.eE+\-]+\s*\*\s*X)\s*-\s*([\d.eE+\-]+)$', inner)
        if m:
            a = float(m.group(1))
            b = float(m.group(2))
            return (display_val + b) / a

        # Pattern: (X / a) + b  or  X / a + b  (inner parens optional)
        m = re.match(r'^\(?\s*X\s*/\s*([\d.eE+\-]+)\s*\)?\s*\+\s*([\d.eE+\-]+)$', inner)
        if m:
            a = float(m.group(1))
            b = float(m.group(2))
            return (display_val - b) * a

        # Pattern: (X / a) - b  or  X / a - b  (inner parens optional)
        m = re.match(r'^\(?\s*X\s*/\s*([\d.eE+\-]+)\s*\)?\s*-\s*([\d.eE+\-]+)$', inner)
        if m:
            a = float(m.group(1))
            b = float(m.group(2))
            return (display_val + b) * a

        # Numerical fallback: bisection to find X such that forward(X) = display_val
        try:
            lo = hi = 0.0
            # Find bracket where display_val is between forward(lo) and forward(hi)
            f_lo = f_hi = None
            # Search ranges covering typical raw value spans (up to 32-bit unsigned)
            candidates = [
                (-1e6, 1e6), (0, 1e6), (0, 1e9), (0, 1e12),
                (-1e6, 0), (-1e9, 0), (-1e12, 0),
            ]
            for attempt_lo, attempt_hi in candidates:
                try:
                    f_lo = self.forward(attempt_lo)
                    f_hi = self.forward(attempt_hi)
                    lo, hi = attempt_lo, attempt_hi
                    # Check target is within range (accounting for direction)
                    if (f_lo <= display_val <= f_hi) or (f_hi <= display_val <= f_lo):
                        break
                except (ZeroDivisionError, OverflowError, ValueError):
                    continue
            if f_lo is None or f_hi is None:
                raise ValueError("Cannot find valid bracket")
            # Verify target is within the bracket
            lo_val, hi_val = min(f_lo, f_hi), max(f_lo, f_hi)
            if display_val < lo_val or display_val > hi_val:
                raise ValueError(
                    f"Display value {display_val} outside search range [{lo_val}, {hi_val}]")
            increasing = f_lo < f_hi
            for _ in range(100):
                mid = (lo + hi) / 2.0
                try:
                    f_mid = self.forward(mid)
                except (ZeroDivisionError, OverflowError):
                    hi = mid
                    continue
                if (f_mid < display_val) == increasing:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2.0
        except Exception:
            raise ValueError(
                f"Cannot invert equation '{self.equation}': unsupported pattern. "
                f"Supported patterns: X, X*a, X/a, X+a, X-a, a-X, X*a+b, X*a-b, X/a+b, X/a-b"
            )


@dataclass
class AxisDef:
    """Definition of a single axis (x, y, or z) of a table."""
    id: str  # "x", "y", or "z"
    address: Optional[int] = None  # BIN address (after base offset)
    element_size_bits: int = 8
    row_count: int = 1
    col_count: int = 1
    typeflags: int = 0
    major_stride_bits: int = 0
    math: Optional[MathExpr] = None
    units: str = ""
    decimal_pl: int = 2
    min_val: float = 0.0
    max_val: float = 255.0
    labels: dict = field(default_factory=dict)  # index -> label value
    embed_type: int = 0  # 0=none, 1=embedded, 3=linked
    link_obj_id: Optional[str] = None  # for type=3
    index_count: int = 0
    is_label_based: bool = False

    @property
    def element_size_bytes(self) -> int:
        return self.element_size_bits // 8

    @property
    def total_elements(self) -> int:
        return self.row_count * self.col_count


@dataclass
class TableDef:
    """Definition of an XDFTABLE (2D map)."""
    unique_id: str
    title: str
    description: str = ""
    categories: list = field(default_factory=list)
    x_axis: Optional[AxisDef] = None
    y_axis: Optional[AxisDef] = None
    z_axis: Optional[AxisDef] = None
    is_axis: bool = False  # True for axis display entries (not real tables)

    @property
    def is_1d(self) -> bool:
        return (self.x_axis is not None and self.x_axis.index_count > 0 and
                (self.y_axis is None or self.y_axis.index_count <= 1))


@dataclass
class ConstantDef:
    """Definition of an XDFCONSTANT (scalar value)."""
    unique_id: str
    title: str
    description: str = ""
    categories: list = field(default_factory=list)
    address: Optional[int] = None
    element_size_bits: int = 16
    typeflags: int = 0
    math: Optional[MathExpr] = None
    units: str = ""
    decimal_pl: int = 2


@dataclass
class XdfDefaults:
    """Default values from XDF header."""
    data_size_bits: int = 8
    sig_digits: int = 4
    output_type: int = 1
    signed: bool = False
    lsb_first: bool = True
    float_mode: bool = False


@dataclass
class XdfHeader:
    """Parsed XDF header information."""
    base_offset: int = 0
    base_subtract: int = 0
    defaults: XdfDefaults = field(default_factory=XdfDefaults)
    categories: dict = field(default_factory=dict)  # index -> name
    description: str = ""


@dataclass
class XdfFile:
    """Complete parsed XDF file."""
    header: XdfHeader
    tables: list  # list of TableDef
    constants: list  # list of ConstantDef


def _get_text(element, tag, default=None):
    """Get text content of a child element."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return default


def _get_int(element, tag, default=0):
    """Get integer value from a child element."""
    text = _get_text(element, tag)
    if text is None:
        return default
    text = text.strip()
    if text.startswith("0x") or text.startswith("0X"):
        return int(text, 16)
    return int(float(text))


def _get_float(element, tag, default=0.0):
    """Get float value from a child element."""
    text = _get_text(element, tag)
    if text is None:
        return default
    return float(text.strip())


def _parse_embedded_data(elem):
    """Parse an EMBEDDEDDATA element and return its attributes."""
    if elem is None:
        return {}

    attrs = {}
    for key in ["mmedtypeflags", "mmedaddress", "mmedelementsizebits",
                 "mmedrowcount", "mmedcolcount", "mmedmajorstridebits",
                 "mmedminorstridebits"]:
        val = elem.get(key)
        if val is not None:
            val = val.strip()
            if val.startswith("0x") or val.startswith("0X"):
                attrs[key] = int(val, 16)
            else:
                attrs[key] = int(val)
    return attrs


def _parse_axis(axis_elem, defaults: XdfDefaults) -> AxisDef:
    """Parse an XDFAXIS element."""
    axis_id = axis_elem.get("id", "z")

    # Embedded data
    ed_elem = axis_elem.find("EMBEDDEDDATA")
    ed = _parse_embedded_data(ed_elem)

    # Embed info
    ei_elem = axis_elem.find("embedinfo")
    embed_type = int(ei_elem.get("type", "0")) if ei_elem is not None else 0
    link_obj_id = ei_elem.get("linkobjid") if ei_elem is not None else None

    # MATH
    math_elem = axis_elem.find("MATH")
    math_eq = None
    if math_elem is not None:
        eq = math_elem.get("equation", "X")
        math_eq = MathExpr(equation=eq)

    # Labels
    labels = {}
    for label in axis_elem.findall("LABEL"):
        idx = int(label.get("index", "0"))
        val = label.get("value", "")
        labels[idx] = val

    # Determine address
    address = None
    if "mmedaddress" in ed:
        address = ed["mmedaddress"]

    # Determine if label-based
    is_label_based = (embed_type == 0 and "mmedaddress" not in ed) or len(labels) > 0

    # Get element size
    elem_bits = ed.get("mmedelementsizebits", defaults.data_size_bits)

    # Get row/col counts
    row_count = ed.get("mmedrowcount", 1)
    col_count = ed.get("mmedcolcount", 1)

    # Index count
    index_count = int(_get_text(axis_elem, "indexcount", str(col_count)))

    return AxisDef(
        id=axis_id,
        address=address,
        element_size_bits=elem_bits,
        row_count=row_count,
        col_count=col_count,
        typeflags=ed.get("mmedtypeflags", 0),
        major_stride_bits=ed.get("mmedmajorstridebits", 0),
        math=math_eq,
        units=_get_text(axis_elem, "units", ""),
        decimal_pl=int(float(_get_text(axis_elem, "decimalpl", "2"))),
        min_val=_get_float(axis_elem, "min", 0.0),
        max_val=_get_float(axis_elem, "max", 255.0),
        labels=labels,
        embed_type=embed_type,
        link_obj_id=link_obj_id,
        index_count=index_count,
        is_label_based=is_label_based,
    )


def _parse_constant(const_elem, defaults: XdfDefaults) -> ConstantDef:
    """Parse an XDFCONSTANT element."""
    unique_id = const_elem.get("uniqueid", "0x0")

    # Embedded data
    ed_elem = const_elem.find("EMBEDDEDDATA")
    ed = _parse_embedded_data(ed_elem)

    # MATH
    math_elem = const_elem.find("MATH")
    math_eq = None
    if math_elem is not None:
        eq = math_elem.get("equation", "X")
        math_eq = MathExpr(equation=eq)

    # Categories
    categories = []
    for catmem in const_elem.findall("CATEGORYMEM"):
        cat_idx = int(catmem.get("category", "0"))
        categories.append(cat_idx)

    address = ed.get("mmedaddress")

    return ConstantDef(
        unique_id=unique_id,
        title=_get_text(const_elem, "title", ""),
        description=_get_text(const_elem, "description", ""),
        categories=categories,
        address=address,
        element_size_bits=ed.get("mmedelementsizebits", defaults.data_size_bits),
        typeflags=ed.get("mmedtypeflags", 0),
        math=math_eq,
        units=_get_text(const_elem, "units", ""),
        decimal_pl=int(float(_get_text(const_elem, "decimalpl", "2"))),
    )


def parse_xdf(filepath: str) -> XdfFile:
    """Parse an XDF file and return a structured representation."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    # Parse header
    header_elem = root.find("XDFHEADER")
    if header_elem is None:
        raise ValueError("No XDFHEADER found in XDF file")

    # Base offset
    bo_elem = header_elem.find("BASEOFFSET")
    base_offset = 0
    base_subtract = 0
    if bo_elem is not None:
        base_offset = int(bo_elem.get("offset", "0"), 0)
        base_subtract = int(bo_elem.get("subtract", "0"), 0)

    # Defaults
    defaults_elem = header_elem.find("DEFAULTS")
    defaults = XdfDefaults()
    if defaults_elem is not None:
        defaults.data_size_bits = int(defaults_elem.get("datasizeinbits", "8"))
        defaults.sig_digits = int(defaults_elem.get("sigdigits", "4"))
        defaults.output_type = int(defaults_elem.get("outputtype", "1"))
        defaults.signed = defaults_elem.get("signed", "0") == "1"
        defaults.lsb_first = defaults_elem.get("lsbfirst", "1") == "1"
        defaults.float_mode = defaults_elem.get("float", "0") == "1"

    # Categories
    categories = {}
    for cat in header_elem.findall("CATEGORY"):
        idx = int(cat.get("index", "0x0"), 16) if cat.get("index", "").startswith("0x") else int(cat.get("index", "0"))
        categories[idx] = cat.get("name", "")

    header = XdfHeader(
        base_offset=base_offset,
        base_subtract=base_subtract,
        defaults=defaults,
        categories=categories,
        description=_get_text(header_elem, "description", ""),
    )

    # Parse tables
    tables = []
    for table_elem in root.findall("XDFTABLE"):
        unique_id = table_elem.get("uniqueid", "0x0")
        title = _get_text(table_elem, "title", "")

        # Categories
        cat_list = []
        for catmem in table_elem.findall("CATEGORYMEM"):
            cat_idx = int(catmem.get("category", "0"))
            cat_list.append(cat_idx)

        # Parse axes
        x_axis = None
        y_axis = None
        z_axis = None
        for axis_elem in table_elem.findall("XDFAXIS"):
            axis = _parse_axis(axis_elem, defaults)
            if axis.id == "x":
                x_axis = axis
            elif axis.id == "y":
                y_axis = axis
            elif axis.id == "z":
                z_axis = axis

        # Apply base offset to addresses
        if z_axis and z_axis.address is not None:
            z_axis.address = z_axis.address + base_offset - base_subtract
        if x_axis and x_axis.address is not None:
            x_axis.address = x_axis.address + base_offset - base_subtract
        if y_axis and y_axis.address is not None:
            y_axis.address = y_axis.address + base_offset - base_subtract

        # Detect axis display entries (not real tables)
        # These have titles like "Table Name : x axis : variable_name"
        is_axis_entry = " : x axis :" in title.lower() or " : y axis :" in title.lower()

        tables.append(TableDef(
            unique_id=unique_id,
            title=title,
            description=_get_text(table_elem, "description", ""),
            categories=cat_list,
            x_axis=x_axis,
            y_axis=y_axis,
            z_axis=z_axis,
            is_axis=is_axis_entry,
        ))

    # Parse constants
    constants = []
    for const_elem in root.findall("XDFCONSTANT"):
        const = _parse_constant(const_elem, defaults)
        if const.address is not None:
            const.address = const.address + base_offset - base_subtract
        constants.append(const)

    return XdfFile(header=header, tables=tables, constants=constants)
