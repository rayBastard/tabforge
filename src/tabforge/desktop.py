"""
Desktop mode: the same server + a native window (pywebview).
Run:  python -m tabforge.desktop
Building an .exe/.app: see the README, "single-file build".
"""
from __future__ import annotations

import socket
import threading
import time
import urllib.request


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    import multiprocessing
    multiprocessing.freeze_support()   # required in a PyInstaller bundle

    import uvicorn
    import webview

    from .server.app import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):                      # wait for the server to come up
        try:
            urllib.request.urlopen(url, timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)

    webview.create_window("TabForge", url, width=1100, height=760,
                          min_size=(800, 560))
    webview.start()
    server.should_exit = True


if __name__ == "__main__":
    main()
