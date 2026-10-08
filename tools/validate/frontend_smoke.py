"""Browser smoke test for the generated guest and staff entry pages."""
from __future__ import annotations

import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
BASE_URL = "http://127.0.0.1:8765"


class FrontendHandler(SimpleHTTPRequestHandler):
    """Serve the same static aliases used by the public FastAPI app."""

    def translate_path(self, path: str) -> str:
        route = urlsplit(path).path
        aliases = {"/": "index.html", "/staff": "staff.html"}
        if route in aliases:
            relative = aliases[route]
        elif route.startswith("/static/"):
            relative = route.removeprefix("/static/")
        else:
            relative = route.lstrip("/")
        root = WEB.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            target = root / "__missing__"
        return str(target)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), FrontendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.5)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for route in ("/", "/staff"):
                    page = browser.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    response = page.goto(BASE_URL + route, wait_until="networkidle", timeout=15000)
                    if response is None or not response.ok:
                        raise AssertionError(f"{route} returned {response.status if response else 'no response'}")
                    if errors:
                        raise AssertionError(f"{route} pageerror: {errors}")
                    page.close()
            finally:
                browser.close()
        print("frontend Playwright smoke ok: / /staff")
        return 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
