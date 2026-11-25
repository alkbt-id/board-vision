"""The Lichess API for preparing tasks (vision/sources/prepare.py): a puzzle with its solution and game, a game's
moves, the tablebase for ≤7 pieces, the user's rapid rating.

Politely: one request at a time, PAUSE seconds apart, a minute's wait on 429 (Lichess asks for it); answers are kept
in web_cache, so a position is never fetched twice. Public endpoints only, no login.
"""

import json
import threading
import time

from .. import store
from . import net

PAUSE = 1.0
_lock = threading.Lock()
_last = 0.0


class Unavailable(RuntimeError):
    """The API did not answer (no network, 404, repeated 429)."""


def _get(url: str, key: str, accept: str = "application/json") -> str:
    global _last
    rows = store.q("SELECT body FROM web_cache WHERE key=?", (key,))
    if rows:
        return rows[0]["body"]
    with _lock:
        for _ in range(3):
            wait = PAUSE - (time.time() - _last)
            if wait > 0:
                time.sleep(wait)
            try:
                with net.client() as c:
                    r = c.get(url, headers={"Accept": accept})
            except Exception as e:  # noqa: BLE001 — no network
                raise Unavailable(str(e)) from e
            finally:
                _last = time.time()
            if r.status_code == 429:
                time.sleep(60)
                continue
            if r.status_code != 200:
                raise Unavailable(f"{url}: HTTP {r.status_code}")
            store.run("INSERT OR REPLACE INTO web_cache (key, body, ts) VALUES (?,?,?)", (key, r.text, store.now_ms()))
            return r.text
    raise Unavailable(f"{url}: too many requests")


def puzzle(pid: str) -> dict:
    """{"game": {"pgn": moves up to and including the opponent's mistake, ...}, "puzzle": {"solution": [uci...],
    "themes": [...], "rating", "initialPly", ...}}."""
    return json.loads(_get(f"https://lichess.org/api/puzzle/{pid}", f"puzzle:{pid}"))


def game_pgn(game_id: str) -> str:
    return _get(f"https://lichess.org/game/export/{game_id}?clocks=false&evals=false&opening=false",
                f"game:{game_id}", accept="application/x-chess-pgn")


def tablebase(fen: str) -> dict:
    """The Lichess tablebase (≤7 pieces): "category" for the side to move, "moves" best first, each with its
    "category" seen from the OTHER side (a winning move is "loss")."""
    f = fen.replace(" ", "_")
    return json.loads(_get(f"https://tablebase.lichess.ovh/standard?fen={f}", f"tb:{fen}"))


def rapid_rating(user: str) -> int | None:
    try:
        d = json.loads(_get(f"https://lichess.org/api/user/{user}", f"user:{user}:{time.strftime('%Y-%m')}"))
        return int(d["perfs"]["rapid"]["rating"])
    except (Unavailable, KeyError, ValueError, TypeError):
        return None
