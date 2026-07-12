# simos-mcp

MCP server for Simos 18.1 ECU tuning — read, write, diff, and validate XDF/BIN files.

## What it does

Reads XDF definition files and BIN binary files, letting you:

- **List** all tables and scalars in an XDF
- **Read** table data (2D maps) with proper axis labels and scaled values
- **Read** scalar values (single parameters)
- **Write** table data back to BIN files
- **Write** scalar values back to BIN files
- **Diff** two BIN files to see what changed
- **Validate** tables against their min/max ranges
- **Search** for tables by name (fuzzy matching via `rapidfuzz`)

## Setup

Requires Python 3.10+ and the `rapidfuzz` package.

```bash
pip install rapidfuzz
```

Or, if using the project's `pyproject.toml`:

```bash
pip install -e .
```

### VS Code MCP config

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "simos-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["path/to/simos-mcp/server.py"]
    }
  }
}
```

### Or run directly

```bash
cd path/to/simos-mcp
python server.py
```

## Tools

| Tool | Description |
|---|---|
| `list_tables` | List all tables/scalars, filter by category or search |
| `read_table` | Read a table's data from a BIN (returns x/y axes + 2D data) |
| `read_scalar` | Read a scalar/constant value |
| `write_table` | Write a 2D array to a table in a BIN |
| `write_scalar` | Write a value to a scalar in a BIN |
| `diff_bins` | Compare two BINs and show differences |
| `validate_table` | Check a table's values against min/max ranges |
| `search_tables` | Fuzzy search for tables/scalars by name |

## How it works

The XDF file is an XML definition that tells the parser:
- **Where** data lives in the BIN (addresses, data sizes)
- **How** to interpret it (signed/unsigned, endianness)
- **How** to scale raw values to human-readable units (MATH equations)

The parser handles:
- Embedded axis data (read from BIN)
- Label-based axes (defined in XDF)
- Linked axes (cross-references between tables)
- MATH equation transformation (forward and inverse)
- Both ECU (Simos 18.1) and TCU (DQ250) XDF formats
- Automatic endianness detection from `mmedtypeflags`

## Tested with

- `SC8S50_switchpatch29.33_v1.006.xdf` (ECU, Simos 18.1)
- `F45M_DSG_v1.007.xdf` (TCU, DQ250)
