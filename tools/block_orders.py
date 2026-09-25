#!/usr/bin/env python3
"""Blocks shell commands that look like order placement. Exit 2 = blocked. Keystone is paper-only."""
import json, sys
cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
for bad in ("portfolio/orders", "place_order", "create_order"):
    if bad in cmd:
        print(f"Blocked: '{bad}'. Keystone is paper-only.", file=sys.stderr)
        sys.exit(2)
