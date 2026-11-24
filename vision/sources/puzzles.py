"""Lichess puzzles turned around (database CC0, https://database.lichess.org/#puzzles).

A Lichess puzzle row: FEN before the opponent's mistake, Moves = the mistake, then the winning line. Here the
position after the mistake is the "before", the winning blow is the opponent's move, and the user plays the side
that made the mistake: right after the blow he has to see what is now attacked. Only positions where the blow is
not a check (a check is no drill) and the rule finds a threat are kept. The puzzle's theme becomes the motif.
"""

import csv

import chess

from .. import store
from . import add_position, net, say

URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
POOL_TARGET = 3000
RATING = (700, 1800)
# the first of these themes a puzzle has becomes its motif
MOTIFS = ("mateIn1", "fork", "skewer", "discoveredAttack", "pin", "hangingPiece", "trappedPiece", "xRayAttack",
          "attraction", "deflection", "interference", "intermezzo", "doubleCheck", "mateIn2")


def run() -> None:
    have = store.counts()["puzzle"]
    if have >= POOL_TARGET:
        say(f"задачи Lichess: уже есть {have} позиций")
        return
    seen = int(store.meta_get("puzzle_rows", "0") or 0)  # rows already read: go past them next time
    kept = row_no = 0
    for row in csv.reader(net.zst_lines(URL)):
        row_no += 1
        if row_no == 1 and row and row[0] == "PuzzleId":
            continue
        if row_no <= seen:
            continue
        if len(row) < 8:
            continue
        pid, fen, moves, rating, _rd, popularity, plays, themes = row[:8]
        try:
            if not (RATING[0] <= int(rating) <= RATING[1]) or int(popularity) < 70 or int(plays) < 200:
                continue
            board = chess.Board(fen)
            line = [chess.Move.from_uci(u) for u in moves.split()]
            board.push(line[0])
            blow = line[1]
        except ValueError, IndexError:
            continue
        theme_set = set(themes.split())
        motif = next((t for t in MOTIFS if t in theme_set), "")
        n = add_position(board, blow, "puzzle", pid, ply=0, motif=motif, rating=int(rating), need_threat=True)
        if n:
            kept += 1
            if kept % 200 == 0:
                say(f"задачи Lichess: взято {kept}")
        if have + kept >= POOL_TARGET:
            break
    store.meta_set("puzzle_rows", str(row_no))
    say(f"задачи Lichess: взято {kept}")
