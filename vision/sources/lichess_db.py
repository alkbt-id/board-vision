"""Other people's games at the user's level: rated rapid games of the Lichess open database (CC0,
https://database.lichess.org), both players inside the rating band of config.toml (default 1000–1500).

Only the beginning of a monthly file is read (a stream, the connection is closed when enough games are taken).
Each game gives a few random positions after the moves of one side: up to 2 with a threat and up to 3 quiet ones,
so the pool keeps the real mix of a game — mostly quiet positions. Only from move 9 on (FIRST_PLY): the openings of
other people's games at this level are often nonsense (the user, 2026-11-24, after 1.a3 2.b3 3.g3 4.h3: «а можно мне
бредовые позиции не ставить?»); his own games keep their openings.
"""

import datetime
import io
import random
import re

import chess
import chess.pgn

from .. import config, store
from . import net, say, sample_game

URL = "https://database.lichess.org/standard/lichess_db_standard_rated_{month}.pgn.zst"
TAG = re.compile(r'^\[(\w+) "(.*)"\]$')
GAMES_PER_RUN = 1200
FIRST_PLY = 16  # the opponent's moves from move 9 on
POOL_TARGET = 5000  # stop adding once the pool has this many positions from other people's games


def months() -> list[str]:
    """The last finished months, newest first."""
    d = datetime.date.today().replace(day=1)
    out = []
    for _ in range(4):
        d = (d - datetime.timedelta(days=1)).replace(day=1)
        out.append(d.strftime("%Y-%m"))
    return out


def games(month: str):
    """(headers, full PGN text) of each game of the month's file, as it streams in."""
    head: dict[str, str] = {}
    text: list[str] = []
    in_moves = False
    for line in net.zst_lines(URL.format(month=month)):
        if line.startswith("[Event ") and in_moves:
            yield head, "\n".join(text)
            head, text, in_moves = {}, [], False
        text.append(line)
        if m := TAG.match(line):
            head[m.group(1)] = m.group(2)
        elif line.strip():
            in_moves = True


def fits(head: dict[str, str], lo: int, hi: int) -> bool:
    if "Rapid" not in head.get("Event", "") or head.get("Termination") not in ("Normal", "Time forfeit"):
        return False
    try:
        we, be = int(head.get("WhiteElo", "0")), int(head.get("BlackElo", "0"))
    except ValueError:
        return False
    return lo <= we <= hi and lo <= be <= hi


def run() -> None:
    have = store.counts()["db"]
    if have >= POOL_TARGET:
        say(f"чужие партии: уже есть {have} позиций")
        return
    lo, hi = config.rating_band()
    rng = random.Random()
    done_month = store.meta_get("db_month", "")
    skip = int(store.meta_get("db_skip", "0") or 0)  # games of that month already used, read past them next time
    for month in months():
        if month < done_month:
            continue
        taken = seen = 0
        try:
            for head, pgn_text in games(month):
                seen += 1
                if not fits(head, lo, hi):
                    continue
                if month == done_month and skip > 0:
                    skip -= 1
                    continue
                pgn = chess.pgn.read_game(io.StringIO(pgn_text))
                if pgn is None or pgn.errors:
                    continue
                moves = list(pgn.mainline_moves())
                if len(moves) < 20:
                    continue
                user = rng.choice([chess.WHITE, chess.BLACK])
                ref = head.get("Site", "")
                sample_game(chess.Board(), moves, user, "db", ref, first_ply=FIRST_PLY, max_threat=2, max_quiet=3, rng=rng)
                taken += 1
                if taken % 100 == 0:
                    say(f"чужие партии {month}: взято {taken} из {seen} прочитанных")
                if taken >= GAMES_PER_RUN:
                    break
        except Exception as e:  # this month's file may not be published yet: try the one before
            say(f"чужие партии {month}: {e}")
            continue
        used = (int(store.meta_get("db_skip", "0") or 0) if month == done_month else 0) + taken
        store.meta_set("db_month", month)
        store.meta_set("db_skip", str(used))
        say(f"чужие партии {month}: взято {taken} партий")
        return
