"""Real missed threats: moments of other people's games at the user's level where a player did not see a threat.

The same games as lichess_db.py (rated rapid, both players in the rating band of config.toml), read further on in
the month's file. A moment counts when, right after the opponent's move, the rule (vision/threats.py) finds a threat
against the player, the player makes some other move, and the opponent then really takes there and comes out at
least 2 pawns ahead (or mates). No engine is needed: the game itself shows the threat was real and was missed.
The drill position is the one the player saw: before the opponent's move, that move, the player to answer.
"""

import io
import random

import chess
import chess.pgn

from .. import config, store, threats
from . import lichess_db, say, side_name

GAMES_PER_RUN = 6000  # games read at most per run (a stream: the file is not downloaded)
TAKE_PER_RUN = 500  # moments stored at most per run
POOL_TARGET = 2500  # stop adding once there are this many
GAIN = 2  # what the opponent must win by the taking, in pawns


def moments(moves: list[chess.Move]) -> list[tuple[chess.Board, chess.Move, int, str]]:
    """(position before the opponent's move, his move, ply, what was missed) for each missed threat of a game."""
    out = []
    board = chess.Board()
    for ply in range(len(moves) - 2):
        before = board.copy(stack=False)
        m1, m2, m3 = moves[ply], moves[ply + 1], moves[ply + 2]
        board.push(m1)
        seen = board.copy(stack=False)  # what the player saw
        after_m2 = seen.copy(stack=False)
        after_m2.push(m2)
        if m3 not in after_m2.legal_moves:
            break
        mate = False
        won = after_m2.is_capture(m3) and not after_m2.is_en_passant(m3) and threats.capture_gain(after_m2, m3) >= GAIN
        if not won:
            after_m2.push(m3)
            mate = after_m2.is_checkmate()
            after_m2.pop()
        if not (won or mate):
            continue
        answer = threats.analyse(seen)
        if not answer:
            continue
        target = chess.square_name(m3.to_square)
        hit = next((t for t in answer["threats"] if (mate and t["mate"]) or (won and target in t["targets"])), None)
        if hit is None:
            continue  # the player's own move created it: a different kind of blunder
        if won and seen.piece_at(m3.to_square) != after_m2.piece_at(m3.to_square):
            continue  # the piece taken is not the one that was threatened (it moved there)
        out.append((before, m1, ply, "mate" if mate else "missed"))
    return out


def run() -> None:
    have = store.counts().get("missed", 0)
    if have >= POOL_TARGET:
        say(f"реальные зевки: уже есть {have} позиций")
        return
    lo, hi = config.rating_band()
    rng = random.Random()
    done_month = store.meta_get("missed_month", "")
    skip = int(store.meta_get("missed_skip", "0") or 0)
    for month in lichess_db.months():
        if month < done_month:
            continue
        read = taken = 0
        try:
            for head, pgn_text in lichess_db.games(month):
                if not lichess_db.fits(head, lo, hi):
                    continue
                if month == done_month and skip > 0:
                    skip -= 1
                    continue
                read += 1
                pgn = chess.pgn.read_game(io.StringIO(pgn_text))
                if pgn is None or pgn.errors:
                    continue
                found = moments(list(pgn.mainline_moves()))
                if found:  # one moment a game, so the pool is not a few long games
                    before, move, ply, what = rng.choice(found)
                    after = before.copy(stack=False)
                    after.push(move)
                    answer = threats.analyse(after)
                    if answer and store.add_item("missed", before.fen(), move.uci(), side_name(after.turn), answer,
                                                 ref=head.get("Site", ""), ply=ply, motif=what):
                        taken += 1
                if read % 500 == 0:
                    say(f"реальные зевки {month}: найдено {taken} в {read} партиях")
                if taken >= TAKE_PER_RUN or read >= GAMES_PER_RUN:
                    break
        except Exception as e:  # this month's file may not be published yet: try the one before
            say(f"реальные зевки {month}: {e}")
            continue
        used = (int(store.meta_get("missed_skip", "0") or 0) if month == done_month else 0) + read
        store.meta_set("missed_month", month)
        store.meta_set("missed_skip", str(used))
        say(f"реальные зевки {month}: {taken} позиций из {read} партий")
        return
