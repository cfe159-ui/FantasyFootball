"""Desktop launcher.

Runs the API on a background thread and points a native macOS window at it via
pywebview (WKWebView), so this behaves like an application rather than a browser
tab. Falls back to the default browser when a native window is unavailable.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Optional

import uvicorn

from .server import app as api_app

DEFAULT_PORT = 8777


def free_port(preferred: int = DEFAULT_PORT) -> int:
    """The preferred port when available, otherwise any free one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int) -> uvicorn.Server:
    config = uvicorn.Config(api_app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for the socket to accept before showing a window at it.
    deadline = time.time() + 20
    while time.time() < deadline:
        if getattr(server, "started", False):
            return server
        time.sleep(0.05)
    return server


def run_window(port: Optional[int] = None, width: int = 1280,
               height: int = 860) -> None:
    """Open the app in a native window."""
    port = port or free_port()
    _serve(port)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview
    except ImportError:
        run_browser(port)
        return
    webview.create_window("Fantasy Football Agent", url,
                          width=width, height=height,
                          min_size=(1024, 700))
    webview.start()


def run_browser(port: Optional[int] = None, open_browser: bool = True) -> None:
    """Serve the app and open it in the default browser."""
    import webbrowser

    port = port or free_port()
    server = _serve(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Fantasy Football Agent running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True
        print("\nstopped")
