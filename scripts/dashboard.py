#!/usr/bin/env python3
"""
OMEGA GUI dashboard launcher.

Starts the web server and opens the glassmorphism dashboard in your browser.
The GUI controls 100% of the backend — trading, crowd engine, risk, wallet,
profiles, and settings are all accessible from the interface.

Usage:
    python scripts/dashboard.py               # default port 8080
    python scripts/dashboard.py --port 9090   # custom port
"""
import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omega.web.server import OmegaWebServer


def main() -> None:
    p = argparse.ArgumentParser(description="OMEGA glassmorphism dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", type=str, default="127.0.0.1")
    p.add_argument("--no-browser", action="store_true", help="don't auto-open browser")
    args = p.parse_args()

    server = OmegaWebServer(port=args.port, host=args.host)
    if not args.no_browser:
        url = f"http://localhost:{args.port}"
        # Delay browser open so the server is ready
        asyncio.get_event_loop().call_later(1.5, lambda: webbrowser.open(url))
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
