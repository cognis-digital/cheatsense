"""CHEATSENSE MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from cheatsense.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-cheatsense[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-cheatsense[mcp]'")
        return 1
    app = FastMCP("cheatsense")

    @app.tool()
    def cheatsense_scan(target: str) -> str:
        """Anti-cheat telemetry analyzer that ingests game session logs and flags statistically anomalous input/aim/movement signatures with explainable per-flag scoring.. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
