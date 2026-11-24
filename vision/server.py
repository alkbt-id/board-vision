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
  GET  /api/progress  the progress chart: one point per session
  POST /api/series/start | answer {series, arrows, ms} | move {series, uci, ms} | leave {series}
                      a series against the bot (vision/series.py); the bot thinks in a worker thread
"""

import contextlib
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import bot, config, events, migrate, series, session, sources, store

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
        "series": {"length": series.length(), "best": series.best()},
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


def threaded(fn):
    """Like `guarded`, but the work (the bot's move) runs in a worker thread, not on the event loop."""
    async def wrapper(request: Request):
        try:
            body = await request.json()
            return JSONResponse(await run_in_threadpool(fn, body if isinstance(body, dict) else {}))
        except (KeyError, ValueError, TypeError, RuntimeError) as e:
            events.log("error", where=fn.__name__, text=str(e))
            return JSONResponse({"error": str(e) or type(e).__name__}, status_code=400)
    return wrapper


def series_start(_: dict) -> dict:
    return series.start()


def series_answer(body: dict) -> dict:
    return series.answer(str(body["series"]), list(body.get("arrows") or []), int(body.get("ms") or 0))


def series_move(body: dict) -> dict:
    return series.move(str(body["series"]), str(body["uci"]), int(body.get("ms") or 0))


def series_leave(body: dict) -> dict:
    return series.leave(str(body["series"]))


async def get_history(_: Request):
    return JSONResponse(session.history(30))


async def get_progress(_: Request):
    return JSONResponse(session.progress())


@contextlib.asynccontextmanager
async def lifespan(_app):
    config.ensure()
    store.db()
    events.log("app_start", port=config.PORT, counts=store.counts())
    last = int(store.meta_get("last_refresh", "0") or 0)
    # also when an earlier run was cut off before Stockfish checked every quiet position
    refresh = not sources.ready() or time.time() * 1000 - last > REFRESH_EVERY_MS or store.counts()["quiet_unchecked"]
    if refresh:
        store.meta_set("last_refresh", str(int(time.time() * 1000)))

    def background() -> None:
        migrate.run()  # a changed threat rule: positions and past answers first
        if refresh:
            sources.refresh()

    threading.Thread(target=background, name="startup", daemon=True).start()
    try:
        yield
    finally:
        sources.STOPPING.set()
        bot.close()
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
        Route("/api/progress", get_progress),
        Route("/api/series/start", threaded(series_start), methods=["POST"]),
        Route("/api/series/answer", threaded(series_answer), methods=["POST"]),
        Route("/api/series/move", threaded(series_move), methods=["POST"]),
        Route("/api/series/leave", threaded(series_leave), methods=["POST"]),
        Mount("/static", StaticFiles(directory=WEB)),
    ],
    lifespan=lifespan,
)
