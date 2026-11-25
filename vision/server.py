"""The local web server: the window's page and its small JSON API.

  GET  /              the page (no-store: the iPad caches static files, the page carries ?v= on them)
  GET  /api/ping      the window waits for this before loading
  GET  /api/state     the pool of positions, the download job, today's results
  POST /api/play/start                          a sitting (vision/play.py) -> the first series' first check
  POST /api/play/answer {play, arrows, ms}      -> the review (and what comes next: your move or the next series)
  POST /api/play/commit {play, move, arrows, ms} your move with what he can do after it -> its check
  POST /api/play/next   {play}                  the bot's answer, or the next series, or the sitting's summary
  POST /api/play/leave  {play}                  end the sitting early (back to the start screen)
  POST /api/log       an event from the window (vision/events.py lists the kinds)
  POST /api/refresh   download new positions now
  GET  /api/history   results per day
  GET  /api/progress  the progress charts: the attention rating per position, the «Решения» rating per task
  the play routes run in a worker thread: the bot thinks there, not on the event loop
"""

import contextlib
import os
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import bot, config, engine, events, migrate, play, session, sources, store, tasks
from .sources import prepare, tactics

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
        "level": play.level(), "best": play.best(), "tasks": prepare.status(),
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










def play_start(_: dict) -> dict:
    if not sources.ready():
        raise ValueError("позиции ещё готовятся")
    return play.start()


def play_answer(body: dict) -> dict:
    return play.answer(str(body["play"]), list(body.get("arrows") or []), int(body.get("ms") or 0),
                       str(body.get("move") or ""))


def play_commit(body: dict) -> dict:
    return play.commit(str(body["play"]), str(body["move"]), list(body.get("arrows") or []), int(body.get("ms") or 0))


def play_next(body: dict) -> dict:
    return play.next_series(str(body["play"]))


def play_leave(body: dict) -> dict:
    return play.leave(str(body["play"]))


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









async def get_history(_: Request):
    return JSONResponse(session.history(30))


async def get_progress(_: Request):
    return JSONResponse({**session.progress(), "decisions": await run_in_threadpool(tasks.progress)})


@contextlib.asynccontextmanager
async def lifespan(_app):
    config.ensure()
    store.db()
    events.log("app_start", port=config.PORT, counts=store.counts())
    last = int(store.meta_get("last_refresh", "0") or 0)
    # also when an earlier run was cut off before Stockfish checked every quiet position
    # and when the real misses of other people's games (vision/sources/missed.py) were never read
    refresh = (not sources.ready() or time.time() * 1000 - last > REFRESH_EVERY_MS or store.counts()["quiet_unchecked"]
               or not store.counts().get("missed") or tactics.pending_hidden())
    if refresh:
        store.meta_set("last_refresh", str(int(time.time() * 1000)))

    def background() -> None:
        if os.environ.get("BV_OFFLINE"):  # the window's tests: no downloads, no task preparation, no Claude
            return
        migrate.run()  # a changed threat rule: positions and past answers first
        migrate.sources_v2()  # puzzles from the solver's side, real misses that could be parried (once)
        migrate.sources_v3()  # other people's games from move 9 on (once)
        if refresh or not store.counts().get("puzzle"):
            sources.refresh()
        prepare.start_background()  # tasks for the move: Stockfish, Maia and Claude, a stock kept full

    threading.Thread(target=background, name="startup", daemon=True).start()
    try:
        yield
    finally:
        sources.STOPPING.set()
        bot.close()
        engine.close()
        events.log("app_stop")


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/ping", ping),
        Route("/api/state", get_state),
        Route("/api/log", guarded(post_log), methods=["POST"]),
        Route("/api/refresh", guarded(post_refresh), methods=["POST"]),
        Route("/api/history", get_history),
        Route("/api/progress", get_progress),
        Route("/api/play/start", threaded(play_start), methods=["POST"]),
        Route("/api/play/answer", threaded(play_answer), methods=["POST"]),
        Route("/api/play/commit", threaded(play_commit), methods=["POST"]),
        Route("/api/play/next", threaded(play_next), methods=["POST"]),
        Route("/api/play/leave", threaded(play_leave), methods=["POST"]),
        Mount("/static", StaticFiles(directory=WEB)),
    ],
    lifespan=lifespan,
)
