"""Lichess puzzles from the solver's side (database CC0, https://database.lichess.org/#puzzles).

A Lichess puzzle row: FEN before the opponent's mistake, Moves = the mistake, then the winning line. The drill
position is just that: the opponent's mistake is his move, and the user plays the solver — the winning blow is his
to see in step 1 («Что у него висит?»: a chance), and the position is won, not lost (the user, 2026-11-24: no lost
positions; the puzzles used to be turned around, the user sitting on the side already hit). Positions where the
solver is in check after the mistake are left out. The puzzle's theme becomes the motif.
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
            mistake = line[0]
        except ValueError, IndexError:
            continue
        theme_set = set(themes.split())
        motif = next((t for t in MOTIFS if t in theme_set), "")
        n = add_position(board, mistake, "puzzle", pid, ply=0, motif=motif, rating=int(rating))
        if n is not None:
            kept += 1
            if kept % 200 == 0:
                say(f"задачи Lichess: взято {kept}")
        if have + kept >= POOL_TARGET:
            break
    store.meta_set("puzzle_rows", str(row_no))
    say(f"задачи Lichess: взято {kept}")
