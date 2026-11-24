"""The user's own games: every move of the opponent becomes a drill position (the user's openings, the user's
blind spots). Lichess: the public games export since the last download. chess.com: the published monthly
archives from the last month read on (one request at a time — the API refuses parallel ones).
"""

import io
import json

import chess
import chess.pgn

from .. import config, store
from . import net, say, sample_game

LICHESS = "https://lichess.org/api/games/user/{user}"
CHESSCOM = "https://api.chess.com/pub/player/{user}/games/archives"


def lichess(user: str) -> int:
    since = int(store.meta_get("own_lichess_since", "0") or 0)
    params = {"since": since + 1 if since else 0, "moves": "true", "tags": "false", "clocks": "false",
              "evals": "false", "opening": "false"}
    games = newest = 0
    with net.client() as c, c.stream("GET", LICHESS.format(user=user), params=params,
                                     headers={"Accept": "application/x-ndjson"}) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw.strip():
                continue
            g = json.loads(raw)
            newest = max(newest, int(g.get("createdAt") or 0))
            if g.get("variant") != "standard" or not g.get("moves"):
                continue
            white = ((g.get("players") or {}).get("white") or {}).get("user") or {}
            me = chess.WHITE if str(white.get("id", "")).lower() == user else chess.BLACK
            board, moves = chess.Board(), []
            try:
                for san in g["moves"].split():
                    move = board.parse_san(san)
                    moves.append(move)
                    board.push(move)
            except ValueError:
                continue
            sample_game(chess.Board(), moves, me, "own", f"https://lichess.org/{g['id']}")
            games += 1
            if games % 25 == 0:
                say(f"Lichess: прочитано {games} новых партий")
    if newest:
        store.meta_set("own_lichess_since", str(newest))
    return games


def chesscom(user: str) -> int:
    last = store.meta_get("own_chesscom_month", "")
    games = 0
    with net.client() as c:
        r = c.get(CHESSCOM.format(user=user))
        r.raise_for_status()
        months = [u for u in r.json().get("archives", []) if "/".join(u.rstrip("/").split("/")[-2:]) >= last]
        for url in months:
            r = c.get(url)
            r.raise_for_status()
            for g in r.json().get("games", []):
                if (g.get("rules") or "chess") != "chess":
                    continue
                pgn = chess.pgn.read_game(io.StringIO(g.get("pgn") or ""))
                if pgn is None or pgn.errors or pgn.board().fen() != chess.STARTING_FEN:
                    continue
                white = str((g.get("white") or {}).get("username", "")).lower()
                me = chess.WHITE if white == user else chess.BLACK
                sample_game(chess.Board(), list(pgn.mainline_moves()), me, "own", g.get("url") or "")
                games += 1
            say(f"chess.com: прочитан месяц {'/'.join(url.rstrip('/').split('/')[-2:])}")
            store.meta_set("own_chesscom_month", "/".join(url.rstrip("/").split("/")[-2:]))
    return games


def run() -> None:
    li, cc = config.lichess_user(), config.chesscom_user()
    notes = []
    if li:
        notes.append(f"Lichess {lichess(li)}")
    if cc:
        notes.append(f"chess.com {chesscom(cc)}")
    say("свои партии: " + (", ".join(notes) + " новых" if notes else "ник не задан в config.toml"))
