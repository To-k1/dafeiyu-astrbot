"""The shared meeting page and its browser session on each console origin."""

import hmac
from pathlib import Path

from aiohttp import web

API = "/api/dsh-team/meeting"
PAGE = "/meetings"
HTML = Path(__file__).with_name("meetings.html")


def browser_key(token):
    return hmac.digest(token.encode(), b"fishhome-meeting-browser", "sha256").hex()


def authorize(request, token, port):
    if request.path != API:
        return False
    if request.method == "POST" and request.headers.get("Origin") != f"http://127.0.0.1:{port}":
        return False
    return hmac.compare_digest(request.cookies.get(f"fishhome-meeting-{port}", "").encode(),
                               browser_key(token).encode())


def grant(response, token, port):
    response.set_cookie(f"fishhome-meeting-{port}", browser_key(token), path=API, httponly=True, samesite="Strict")


def page(dsh_ports):
    ancestors = " ".join(f"http://127.0.0.1:{port}" for port in dsh_ports)
    return web.Response(text=HTML.read_text(encoding="utf-8"), content_type="text/html", headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                   "connect-src 'self'; form-action 'none'; frame-ancestors " + ancestors,
    })
