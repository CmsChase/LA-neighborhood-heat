"""Thin entry point for the external Sentinel dashboard watchdog."""

from __future__ import annotations

from la_heat.sentinel_dashboard_watchdog import main

if __name__ == "__main__":
    raise SystemExit(main())
