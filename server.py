"""
SimosMCP Server
MCP server for Simos 18.1 ECU tuning — read, write, diff, and validate XDF/BIN files.
"""

import json
import os
import sys

from xdf_parser import parse_xdf, XdfFile, TableDef, ConstantDef
from bin_ops import BinFile
from fuzzy_search import search_items, fuzzy_find

# ── Global state ──────────────────────────────────────────────────────────────

_loaded_bins: dict[str, BinFile] = {}
_loaded_xdfs: dict[str, XdfFile] = {}


def _find_xdf_for_bin(bin_path: str) -> str | None:
    """Try to find a matching XDF file for a given BIN."""
    bin_dir = os.path.dirname(bin_path)

    # Look in same directory and parent directories
    search_dirs = [bin_dir, os.path.dirname(bin_dir)]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(".xdf"):
                return os.path.join(d, f)
    return None


def _get_or_load(bin_path: str, xdf_path: str | None = None) -> BinFile:
    """Get an already-loaded BinFile or load it fresh."""
    if bin_path in _loaded_bins:
        return _loaded_bins[bin_path]

    if xdf_path is None:
        xdf_path = _find_xdf_for_bin(bin_path)
    if xdf_path is None:
        raise FileNotFoundError(f"No XDF file found for {bin_path}")

    xdf = parse_xdf(xdf_path)
    bf = BinFile(bin_path, xdf)
    _loaded_bins[bin_path] = bf
    _loaded_xdfs[xdf_path] = xdf
    return bf


def _find_table(bf: BinFile, name: str) -> TableDef | None:
    """Find a table by title using fuzzy search.

    Prefers the main table over axis sub-tables when multiple match.
    """
    result = fuzzy_find(name, bf.xdf.tables, best_only=True)
    if result is not None:
        return result

    # Fallback: substring match for axis tables that fuzzy might rank lower
    name_lower = name.lower()
    best: TableDef | None = None
    best_size = 0
    for t in bf.xdf.tables:
        if name_lower in t.title.lower():
            size = t.z_axis.row_count * t.z_axis.col_count if t.z_axis else 0
            if "axis" in t.title.lower():
                size = max(size - 1000, 0)
            if size > best_size:
                best_size = size
                best = t
    return best


def _find_scalar(bf: BinFile, name: str) -> ConstantDef | None:
    """Find a scalar by title using fuzzy search."""
    result = fuzzy_find(name, bf.xdf.constants, best_only=True)
    if result is not None:
        return result

    # Fallback: substring match
    name_lower = name.lower()
    for c in bf.xdf.constants:
        if name_lower in c.title.lower():
            return c
    return None


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_list_tables(bin_path: str, xdf_path: str = "", category: str = "",
                     search: str = "", show_scalars: bool = True) -> str:
    """
    List all tables and scalars defined in an XDF file.

    Args:
        bin_path: Path to the BIN file
        xdf_path: Optional path to the XDF file (auto-detected if empty)
        category: Filter by category name (partial match)
        search: Filter by title (partial match, case-insensitive)
        show_scalars: Include scalar/constant definitions
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
    except Exception as e:
        return json.dumps({"error": str(e)})

    results = []

    for t in bf.xdf.tables:
        if search and search.lower() not in t.title.lower():
            continue
        if category:
            cat_names = [bf.xdf.header.categories.get(c, str(c)) for c in t.categories]
            if not any(category.lower() in cn.lower() for cn in cat_names):
                continue

        z_info = ""
        if t.z_axis and t.z_axis.address is not None:
            z_info = f"  |  z@0x{t.z_axis.address:X}  {t.z_axis.row_count}x{t.z_axis.col_count}  {t.z_axis.element_size_bits}bit"

        x_info = ""
        if t.x_axis and t.x_axis.address is not None:
            x_info = f"  |  x@0x{t.x_axis.address:X}  {t.x_axis.col_count}elem"
        elif t.x_axis and t.x_axis.is_label_based:
            x_info = f"  |  x=labels  {t.x_axis.index_count}elem"

        cat_names = [bf.xdf.header.categories.get(c, str(c)) for c in t.categories]
        results.append({
            "title": t.title,
            "unique_id": t.unique_id,
            "categories": cat_names,
            "size": f"{t.z_axis.row_count}x{t.z_axis.col_count}" if t.z_axis else "N/A",
            "units": t.z_axis.units if t.z_axis else "",
            "info": z_info + x_info,
        })

    if show_scalars:
        for c in bf.xdf.constants:
            if search and search.lower() not in c.title.lower():
                continue
            if category:
                cat_names = [bf.xdf.header.categories.get(cat, str(cat)) for cat in c.categories]
                if not any(category.lower() in cn.lower() for cn in cat_names):
                    continue

            addr_info = f"  @0x{c.address:X}" if c.address else ""
            results.append({
                "title": c.title,
                "unique_id": c.unique_id,
                "type": "scalar",
                "units": c.units,
                "info": f"  {c.element_size_bits}bit{addr_info}",
            })

    return json.dumps({
        "bin": bin_path,
        "xdf": bf.xdf.header.description or "unknown",
        "base_offset": f"0x{bf.xdf.header.base_offset:X}",
        "table_count": len(bf.xdf.tables),
        "scalar_count": len(bf.xdf.constants),
        "results": results[:200],
        "total": len(results),
    }, indent=2)


def tool_read_table(bin_path: str, table_name: str, xdf_path: str = "") -> str:
    """
    Read a table's data from a BIN file.

    Args:
        bin_path: Path to the BIN file
        table_name: Table title to search for (fuzzy match)
        xdf_path: Optional path to the XDF file
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
        table = _find_table(bf, table_name)
        if table is None:
            return json.dumps({"error": f"No table matching '{table_name}' found"})

        data = bf.read_table(table)
        return bf.format_table(data)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_read_scalar(bin_path: str, scalar_name: str, xdf_path: str = "") -> str:
    """
    Read a scalar/constant value from a BIN file.

    Args:
        bin_path: Path to the BIN file
        scalar_name: Scalar title to search for (fuzzy match)
        xdf_path: Optional path to the XDF file
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
        const = _find_scalar(bf, scalar_name)
        if const is None:
            return json.dumps({"error": f"No scalar matching '{scalar_name}' found"})

        data = bf.read_scalar(const)
        return json.dumps(data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_write_table(bin_path: str, table_name: str, data: str,
                     x_axis: str = "", y_axis: str = "",
                     xdf_path: str = "", save: bool = True) -> str:
    """
    Write data to a table in a BIN file.

    Args:
        bin_path: Path to the BIN file
        table_name: Table title to search for
        data: JSON string of 2D array [[row0], [row1], ...]
        x_axis: Optional JSON array of new x-axis values [val1, val2, ...]
        y_axis: Optional JSON array of new y-axis values [val1, val2, ...]
        xdf_path: Optional path to the XDF file
        save: Whether to save the file after writing
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
        table = _find_table(bf, table_name)
        if table is None:
            return json.dumps({"error": f"No table matching '{table_name}' found"})

        new_data = json.loads(data)
        new_x = json.loads(x_axis) if x_axis else None
        new_y = json.loads(y_axis) if y_axis else None
        bf.write_table(table, new_data, x_axis=new_x, y_axis=new_y)

        if save:
            bf.save()

        preview = bf.format_table(bf.read_table(table))
        changes = []
        changes.append(f"{len(new_data)}x{len(new_data[0]) if new_data else 0} Z-data")
        if new_x:
            changes.append(f"{len(new_x)} x-axis values")
        if new_y:
            changes.append(f"{len(new_y)} y-axis values")
        return f"Written {', '.join(changes)} to '{table.title}' (saved={save})\n\n{preview}"
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_write_scalar(bin_path: str, scalar_name: str, value: float,
                      xdf_path: str = "", save: bool = True) -> str:
    """
    Write a scalar/constant value to a BIN file.

    Args:
        bin_path: Path to the BIN file
        scalar_name: Scalar title to search for
        value: New display value to write
        xdf_path: Optional path to the XDF file
        save: Whether to save the file after writing
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
        const = _find_scalar(bf, scalar_name)
        if const is None:
            return json.dumps({"error": f"No scalar matching '{scalar_name}' found"})

        old = bf.read_scalar(const)
        bf.write_scalar(const, value)

        if save:
            bf.save()

        return json.dumps({
            "status": "ok",
            "scalar": const.title,
            "old_value": old["value"],
            "new_value": value,
            "units": const.units,
            "saved": save,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_diff_bins(bin_path_a: str, bin_path_b: str,
                   xdf_path_a: str = "", xdf_path_b: str = "") -> str:
    """
    Compare two BIN files and list which tables changed.

    Returns a lightweight summary. Use diff_table() for full details on a specific table.
    """
    try:
        bf_a = _get_or_load(bin_path_a, xdf_path_a or None)
        bf_b = _get_or_load(bin_path_b, xdf_path_b or None)

        return bf_a.diff_summary(bf_b)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_diff_table(bin_path_a: str, bin_path_b: str, table_name: str,
                    xdf_path_a: str = "", xdf_path_b: str = "") -> str:
    """
    Show full diff for a specific table between two BIN files.

    Displays old -> new for every changed cell with axis labels.
    """
    try:
        bf_a = _get_or_load(bin_path_a, xdf_path_a or None)
        bf_b = _get_or_load(bin_path_b, xdf_path_b or None)

        return bf_a.diff_table(bf_b, table_name)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_validate_table(bin_path: str, table_name: str,
                        xdf_path: str = "") -> str:
    """
    Validate a table's data against its defined min/max ranges.

    Args:
        bin_path: Path to the BIN file
        table_name: Table title to search for
        xdf_path: Optional path to the XDF file
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)
        table = _find_table(bf, table_name)
        if table is None:
            return json.dumps({"error": f"No table matching '{table_name}' found"})

        data = bf.read_table(table)
        warnings = []
        z_axis = table.z_axis

        if z_axis:
            for r, row in enumerate(data["data"]):
                for c, val in enumerate(row):
                    if isinstance(val, (int, float)):
                        if val < z_axis.min_val:
                            warnings.append(f"[{r},{c}] {val} < min ({z_axis.min_val})")
                        if val > z_axis.max_val:
                            warnings.append(f"[{r},{c}] {val} > max ({z_axis.max_val})")

        return json.dumps({
            "table": table.title,
            "units": z_axis.units if z_axis else "",
            "range": f"{z_axis.min_val} to {z_axis.max_val}" if z_axis else "N/A",
            "warnings": warnings[:20],
            "warning_count": len(warnings),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


def tool_search_tables(bin_path: str, search: str, xdf_path: str = "") -> str:
    """
    Search for tables/scalars using fuzzy matching.

    Results are scored (0-100) and ranked. Match types:
      - exact:   search is a substring of the title
      - token:   all search words found in title
      - fuzzy:   approximate string similarity (rapidfuzz)
      - partial: best substring similarity

    Args:
        bin_path: Path to the BIN file
        search: Search term (fuzzy matching, case-insensitive)
        xdf_path: Optional path to the XDF file
    """
    try:
        bf = _get_or_load(bin_path, xdf_path or None)

        def _table_extra(t: TableDef) -> dict:
            d: dict = {"type": "table"}
            if t.z_axis:
                d["size"] = f"{t.z_axis.row_count}x{t.z_axis.col_count}"
                d["units"] = t.z_axis.units
            return d

        def _const_extra(c: ConstantDef) -> dict:
            return {"type": "scalar", "units": c.units}

        table_results = search_items(
            search, bf.xdf.tables, max_results=100, extra_fn=_table_extra)
        scalar_results = search_items(
            search, bf.xdf.constants, max_results=100, extra_fn=_const_extra)

        all_results = table_results + scalar_results
        all_results.sort(key=lambda r: r.score, reverse=True)

        results = [r.to_dict() for r in all_results[:100]]

        return json.dumps({
            "search": search,
            "results": results,
            "total": len(results),
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── MCP Server (stdio JSON-RPC) ──────────────────────────────────────────────

TOOLS = {
    "list_tables": {
        "description": "List all tables and scalars defined in an XDF file. Optionally filter by category or search term.",
        "function": tool_list_tables,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "xdf_path": {"type": "string", "description": "Optional XDF path (auto-detected)", "default": ""},
            "category": {"type": "string", "description": "Filter by category name", "default": ""},
            "search": {"type": "string", "description": "Filter by title", "default": ""},
            "show_scalars": {"type": "boolean", "description": "Include scalars", "default": True},
        },
    },
    "read_table": {
        "description": "Read a table from a BIN file. Returns a formatted text grid with axis labels and scaled values.",
        "function": tool_read_table,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "table_name": {"type": "string", "description": "Table title (fuzzy match)"},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
        },
    },
    "read_scalar": {
        "description": "Read a scalar/constant value from a BIN file.",
        "function": tool_read_scalar,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "scalar_name": {"type": "string", "description": "Scalar title (fuzzy match)"},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
        },
    },
    "write_table": {
        "description": "Write data to a table in a BIN file. Data should be a 2D JSON array.",
        "function": tool_write_table,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "table_name": {"type": "string", "description": "Table title (fuzzy match)"},
            "data": {"type": "string", "description": "JSON 2D array [[row0], [row1], ...]"},
            "x_axis": {"type": "string", "description": "Optional JSON array of new x-axis values", "default": ""},
            "y_axis": {"type": "string", "description": "Optional JSON array of new y-axis values", "default": ""},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
            "save": {"type": "boolean", "description": "Save after writing", "default": True},
        },
    },
    "write_scalar": {
        "description": "Write a scalar/constant value to a BIN file.",
        "function": tool_write_scalar,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "scalar_name": {"type": "string", "description": "Scalar title (fuzzy match)"},
            "value": {"type": "number", "description": "New display value"},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
            "save": {"type": "boolean", "description": "Save after writing", "default": True},
        },
    },
    "diff_bins": {
        "description": "Compare two BIN files. Returns compact text showing only changed cells with before/after values.",
        "function": tool_diff_bins,
        "parameters": {
            "bin_path_a": {"type": "string", "description": "Path to first BIN"},
            "bin_path_b": {"type": "string", "description": "Path to second BIN"},
            "xdf_path_a": {"type": "string", "description": "Optional XDF for first BIN", "default": ""},
            "xdf_path_b": {"type": "string", "description": "Optional XDF for second BIN", "default": ""},
        },
    },
    "validate_table": {
        "description": "Validate a table's data against its min/max ranges defined in the XDF.",
        "function": tool_validate_table,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "table_name": {"type": "string", "description": "Table title (fuzzy match)"},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
        },
    },
    "search_tables": {
        "description": "Search for tables and scalars matching a term. Uses fuzzy matching — approximate/misspelled terms will still find relevant results. Results include a score (0-100) and match_type (exact, token, fuzzy, partial).",
        "function": tool_search_tables,
        "parameters": {
            "bin_path": {"type": "string", "description": "Path to the BIN file"},
            "search": {"type": "string", "description": "Search term (fuzzy matching)"},
            "xdf_path": {"type": "string", "description": "Optional XDF path", "default": ""},
        },
    },
    "diff_table": {
        "description": "Show full diff for a specific table between two BIN files. Use after diff_bins to inspect details.",
        "function": tool_diff_table,
        "parameters": {
            "bin_path_a": {"type": "string", "description": "Path to first BIN"},
            "bin_path_b": {"type": "string", "description": "Path to second BIN"},
            "table_name": {"type": "string", "description": "Table title (fuzzy match)"},
            "xdf_path_a": {"type": "string", "description": "Optional XDF for first BIN", "default": ""},
            "xdf_path_b": {"type": "string", "description": "Optional XDF for second BIN", "default": ""},
        },
    },
}


def _handle_request(request: dict) -> dict | None:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "simosmcp",
                    "version": "1.0.0"
                },
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        tools_list = []
        for name, tool in TOOLS.items():
            tools_list.append({
                "name": name,
                "description": tool["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": tool["parameters"],
                    "required": [k for k, v in tool["parameters"].items()
                                 if "default" not in v],
                },
            })
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools_list},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }

        try:
            result_text = TOOLS[tool_name]["function"](**arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def main():
    """Run the MCP server over stdio."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            request = json.loads(line)

            response = _handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

        except EOFError:
            break
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
