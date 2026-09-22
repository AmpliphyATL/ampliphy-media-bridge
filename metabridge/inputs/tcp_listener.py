"""Input adapter transport: a tiny TCP line listener.

PlayoutONE Monitor (and Live365's encoder, and others) can push metadata as a
line of text over TCP. Set METABRIDGE_TCP_PORT to enable. Each newline-terminated
line becomes a TrackEvent via the PlayoutONE Monitor parser.
"""
from __future__ import annotations

import logging
import os
import socket
import threading

from . import playoutone_monitor
from ..pipeline import handle, is_duplicate

log = logging.getLogger("metabridge.tcp")


def _decode(b: bytes) -> str:
    """Monitor on Windows sends its default code page (Windows-1252) — en dashes, curly
    quotes and accents arrive as single bytes that are invalid UTF-8. Try UTF-8 first
    (pure ASCII and any UTF-8 sender), then fall back to cp1252 instead of mangling."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def _serve(port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((os.environ.get("METABRIDGE_TCP_HOST", "0.0.0.0"), port))
    srv.listen(5)
    log.info("TCP metadata listener on port %d", port)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=_client, args=(conn, addr), daemon=True).start()


def _client(conn, addr):
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = _decode(line).strip()
                if not text:
                    continue
                ev = playoutone_monitor.from_line(text)
                log.info("TCP line from %s: %r", addr[0], text[:200])
                reply = b"ignored\n"
                if ev:
                    from ..pipeline import looks_like_template
                    if looks_like_template(ev):
                        threading.Thread(target=handle, args=(ev,), daemon=True).start()  # records the refusal
                        reply = b"refused\n"
                    else:
                        if not is_duplicate(ev):
                            threading.Thread(target=handle, args=(ev,), daemon=True).start()
                        reply = b"queued\n"
                try:
                    conn.sendall(reply)   # Monitor compares this with its Return Value box
                except OSError:
                    pass
        if buf.strip():
            ev = playoutone_monitor.from_line(_decode(buf))
            if ev and not is_duplicate(ev):
                threading.Thread(target=handle, args=(ev,), daemon=True).start()


_running_port: int | None = None


def start_if_configured() -> bool:
    """Start the listener on METABRIDGE_TCP_PORT. Safe to call more than once:
    if it is already up on that port nothing happens."""
    global _running_port
    port = os.environ.get("METABRIDGE_TCP_PORT")
    if not port:
        return False
    try:
        port_i = int(port)
    except ValueError:
        log.error("bad METABRIDGE_TCP_PORT %r", port)
        return False
    if _running_port == port_i:
        return True
    if _running_port is not None:
        log.warning("TCP port changed %d -> %d; restart MetaBridge for it to take effect", _running_port, port_i)
        return False
    threading.Thread(target=_serve, args=(port_i,), daemon=True).start()
    _running_port = port_i
    return True
