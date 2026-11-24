"""The local web server: the window's page and its small JSON API.

  GET  /              the page (no-store: the iPad caches static files, the page carries ?v= on them)
  GET  /api/ping      the window waits for this before loading
  GET  /api/state     the pool of positions, the download job, today's results
  POST /api/session   start a session -> the first position
  POST /api/answer    {session, index, arrows: [{from, to}], ms} -> the review
  POST /api/next      {session} -> the next position, or the session's summary
  POST /api/leave     {session} -> end the session early (back to the start screen)
  POST /api/log       an event from the window (vision/events.py lists the kinds)
  POST /api/refresh   download new positions now
  GET  /api/history   results per day
"""

import contextlib
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, events, session, sources, store

WEB = Path(__file__).parent / "web"
REFRESH_EVERY_MS = 12 * 3600 * 1000


async def index(_: Request):
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


async def ping(_: Request):
    return JSONResponse({"ok": True})


def state() -> dict:
    return {
        "counts": store.counts(), "ready": sources.ready(), "import": dict(sources.STATUS),
        "answered": store.answered_count(), "today": (session.history(1) or [None])[0],
        "users": {"lichess": config.lichess_user(), "chesscom": config.chesscom_user()},
    }


async def get_state(_: Request):
    return JSONResponse(state())


def guarded(fn):
    async def wrapper(request: Request):
        try:
            body = await request.json() if request.method == "POST" else {}
            return JSONResponse(fn(body if isinstance(body, dict) else {}))
        except (KeyError, ValueError, TypeError) as e:
            events.log("error", where=fn.__name__, text=str(e))
            return JSONResponse({"error": str(e) or type(e).__name__}, status_code=400)
    return wrapper


def new_session(_: dict) -> dict:
    if not sources.ready():
        raise ValueError("позиции ещё готовятся")
    return session.start()


def post_answer(body: dict) -> dict:
    return session.answer(str(body["session"]), int(body["index"]), list(body.get("arrows") or []),
                          int(body.get("ms") or 0))


def post_next(body: dict) -> dict:
    return session.next_item(str(body["session"]))


def post_leave(body: dict) -> dict:
    return session.leave(str(body["session"]))


def post_log(body: dict) -> dict:
    return {"ok": events.from_client(body) is not None}


def post_refresh(_: dict) -> dict:
    return {"started": sources.start_background()}


async def get_history(_: Request):
    return JSONResponse(session.history(30))


@contextlib.asynccontextmanager
async def lifespan(_app):
    config.ensure()
    store.db()
    events.log("app_start", port=config.PORT, counts=store.counts())
    last = int(store.meta_get("last_refresh", "0") or 0)
    # also when an earlier run was cut off before Stockfish checked every quiet position
    if not sources.ready() or time.time() * 1000 - last > REFRESH_EVERY_MS or store.counts()["quiet_unchecked"]:
        store.meta_set("last_refresh", str(int(time.time() * 1000)))
        sources.start_background()
    try:
        yield
    finally:
        sources.STOPPING.set()
        events.log("app_stop")


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/ping", ping),
        Route("/api/state", get_state),
        Route("/api/session", guarded(new_session), methods=["POST"]),
        Route("/api/answer", guarded(post_answer), methods=["POST"]),
        Route("/api/next", guarded(post_next), methods=["POST"]),
        Route("/api/leave", guarded(post_leave), methods=["POST"]),
        Route("/api/log", guarded(post_log), methods=["POST"]),
        Route("/api/refresh", guarded(post_refresh), methods=["POST"]),
        Route("/api/history", get_history),
        Mount("/static", StaticFiles(directory=WEB)),
    ],
    lifespan=lifespan,
)
