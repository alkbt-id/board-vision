"""The app on the iPad: a second listener on the home network over https, reusing «Шахматный тренер»'s setup.

«Шахматный тренер» (coach/lan.py) already made a certificate for this Mac's name with mkcert (its root is trusted on
the iPad) and an access key that the iPad's Safari keeps in the cookie `coach_key`. This app uses the same
certificate, the same key and the same cookie on its own port (BV_LAN_PORT, default 8444): a cookie is not tied to
a port, so Safari that opens «Шахматный тренер» opens https://<mac>.local:8444 at once. A Home Screen icon has its
own cookie jar: make it from the link with ?key= (printed by `uv run python -m vision.lan`).
Nothing is created here: without «Шахматный тренер»'s tls/ and key the listener stays off.
"""

import contextlib
import hmac
import logging
import socket
import subprocess

import uvicorn
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse

from .config import COACH_LAN_KEY, COACH_TLS, LAN_PORT

CERT = COACH_TLS / "cert.pem"
KEY_PEM = COACH_TLS / "key.pem"
COOKIE = "coach_key"
log = logging.getLogger("vision.lan")


def host() -> str:
    """The Mac's name on the home network (Bonjour), e.g. alkbtmac.local."""
    try:
        name = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              check=False).stdout.strip()
    except OSError:
        name = ""
    return f"{name or socket.gethostname().removesuffix('.local')}.local"


def enabled() -> bool:
    return CERT.exists() and KEY_PEM.exists() and COACH_LAN_KEY.exists()


def key() -> str:
    return COACH_LAN_KEY.read_text().strip()


def link() -> str:
    return f"https://{host()}:{LAN_PORT}/?key={key()}"


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class Gate:
    """Only requests with the key get through: the cookie, or ?key= (which sets the cookie)."""

    def __init__(self, app, secret: str):
        self.app = app
        self.secret = secret

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        if _same(conn.cookies.get(COOKIE, ""), self.secret):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and _same(conn.query_params.get("key", ""), self.secret):
            cookie = f"{COOKIE}={self.secret}; Max-Age=315360000; Path=/; Secure; HttpOnly; SameSite=Lax"

            async def send_with_cookie(message):
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [*message.get("headers", []), (b"set-cookie", cookie.encode())]}
                await send(message)

            return await self.app(scope, receive, send_with_cookie)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        page = HTMLResponse(
            "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Видение доски</title><body style='font:17px -apple-system,system-ui;padding:40px 20px'>"
            "<h2>Нужен ключ</h2><p>Открой ссылку с ключом: её печатает <code>uv run python -m vision.lan</code> на Mac.</p>",
            status_code=401)
        return await page(scope, receive, send)


class _Follower(uvicorn.Server):
    """Leaves the signals to the main 127.0.0.1 server, which stops this one when it stops."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(app) -> None:
    server = _Follower(uvicorn.Config(
        Gate(app, key()), host="0.0.0.0", port=LAN_PORT, ssl_certfile=str(CERT), ssl_keyfile=str(KEY_PEM),
        lifespan="off", log_level="warning"))
    try:
        await server.serve()
    except SystemExit:  # uvicorn exits when it cannot bind; the window on the Mac must keep working
        log.warning("iPad: port %s is taken, the home-network listener is off", LAN_PORT)


if __name__ == "__main__":
    print(link() if enabled() else "Нет сертификата «Шахматного тренера»: сначала настрой iPad там (python -m coach.lan).")
