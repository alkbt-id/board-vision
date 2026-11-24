"""The attention rating: one number that goes up when you see more, with the difficulty of each position counted.

Like a puzzle rating (Elo): every answer moves the rating by how much better or worse it was than expected,
    expected = 1 / (1 + 10 ** ((difficulty - rating) / 400))
    rating  += K * (score - expected)
so a right answer on a hard position raises it a lot and a miss on an easy one lowers it a lot. That is why the
rating does not drift up just because sessions get more quiet positions or repeats: an easy position that is
answered right is expected to be, and moves it little. Repeats (a position seen before) do not count at all —
they can be remembered.

A position's difficulty is a base plus points for what makes a threat hard to see — the kinds Afek & Neiman
describe in «Невидимые шахматные ходы» and what the research on blunders points at:
  threat        there is something to find at all (a quiet position is the base)
  more          each threat beyond the first (all must be found)
  discovered    the attacking line was opened by the move, the attacker itself did not move
  old           the threat was there before the opponent's move (nothing near the moved piece points at it)
  backward      the capture goes towards the opponent's own side
  horizontal    the capture goes along a rank
  long          the capture is 4 or more squares long
  knight        a knight takes
  exchange      it is only a threat because of the whole exchange (3+ captures, or taking back does not help)
  mate          the opponent mates
  promotion     a pawn walks in to queen
  decoys        quiet position: pieces of yours that are attacked but safe (they look like threats), up to 2
The points start as a guess (WEIGHTS) and are adjusted by the answers themselves, a little after each one: a kind
of position you miss more often than expected becomes worth more.

Score of one answer: a quiet position — 1 without arrows, minus 0.5 per extra arrow; a position with threats — the
share of threats found, minus 0.25 per extra arrow; never below 0.
"""

import chess

from . import store, threats

START = 1500
BASE = 1100  # a quiet position without decoys
K = 24  # rating step
ETA = 4  # how fast the difficulty points learn
WEIGHTS = {
    "threat": 300, "more": 100, "discovered": 150, "old": 100, "backward": 100, "horizontal": 75, "long": 50,
    "knight": 50, "exchange": 150, "mate": 50, "promotion": 100, "decoys": 100,
}


def features(fen: str, move_uci: str, answer: dict) -> dict[str, float]:
    """What makes this position hard to read (see the module doc); missing keys are 0."""
    before = chess.Board(fen)
    move = chess.Move.from_uci(move_uci)
    opp = before.turn  # the side that makes the move, i.e. the opponent
    after = before.copy(stack=False)
    after.push(move)
    found = answer["threats"]
    if not found:
        return {"decoys": float(min(2, len(answer["defended"])))}
    f: dict[str, float] = {"threat": 1.0, "more": float(len(found) - 1)}
    for t in found:
        best = chess.Move.from_uci(t["best"])
        target = chess.parse_square(t["target"])
        attacker = best.from_square
        if t["mate"]:
            f["mate"] = 1.0
        if t.get("promotion"):
            f["promotion"] = 1.0
        if attacker != move.to_square and attacker not in before.attackers(opp, best.to_square):
            f["discovered"] = 1.0
        if t["piece"] is not None and not t.get("promotion") and _threat_before(before, target):
            f["old"] = 1.0
        piece = after.piece_type_at(attacker)
        dr = chess.square_rank(best.to_square) - chess.square_rank(attacker)
        dist = chess.square_distance(attacker, best.to_square)
        if piece == chess.KNIGHT:
            f["knight"] = 1.0
        elif dr == 0:
            f["horizontal"] = 1.0
        if piece != chess.KNIGHT and dr * (1 if opp == chess.WHITE else -1) < 0:
            f["backward"] = 1.0
        if dist >= 4:
            f["long"] = 1.0
        ex = t.get("exchange") or {}
        if len(ex.get("line") or []) >= 3 or ex.get("if_recapture"):
            f["exchange"] = 1.0
    return f


def _threat_before(before: chess.Board, square: int) -> bool:
    """Could the opponent already win the piece on `square` before his move (it is his move in `before`)?"""
    for m in before.generate_legal_captures(to_mask=chess.BB_SQUARES[square]):
        if threats.capture_gain(before, m) > 0:
            return True
    return False


def score(n_threats: int, found: int, extra: int) -> float:
    if n_threats == 0:
        return max(0.0, 1.0 - 0.5 * extra)
    return max(0.0, found / n_threats - 0.25 * extra)


def expected(difficulty: float, rating: float) -> float:
    return 1 / (1 + 10 ** ((difficulty - rating) / 400))


_feature_cache: dict[int, dict] = {}


def clear_cache() -> None:
    _feature_cache.clear()


def _item_features(item_id: int, fen: str, move: str, answer_json: str) -> dict:
    if item_id not in _feature_cache:
        import json
        _feature_cache[item_id] = features(fen, move, json.loads(answer_json))
    return _feature_cache[item_id]


def replay() -> list[dict]:
    """Every first-time answer in order, with the rating after it: [{session, ts, rating, difficulty, score, ms}].
    The difficulty points learn along the way, so the whole history is replayed from the start each time."""
    rows = store.q(
        "SELECT a.session, a.ts, a.ms, a.n_threats, a.found, a.extra, i.id AS item, i.fen, i.move, i.answer"
        " FROM attempts a JOIN items i ON i.id = a.item"
        " WHERE a.id = (SELECT MIN(b.id) FROM attempts b WHERE b.item = a.item) ORDER BY a.ts")
    w = dict(WEIGHTS)
    rating = float(START)
    out = []
    for r in rows:
        f = _item_features(r["item"], r["fen"], r["move"], r["answer"])
        diff = BASE + sum(w.get(k, 0) * v for k, v in f.items())
        e = expected(diff, rating)
        s = score(r["n_threats"] or 0, r["found"] or 0, r["extra"] or 0)
        rating += K * (s - e)
        for k, v in f.items():  # missed more than expected -> this kind is harder than thought
            w[k] = w.get(k, 0) + ETA * (e - s) * v
        out.append({"session": r["session"], "ts": r["ts"], "rating": rating, "difficulty": diff, "score": s,
                    "ms": r["ms"]})
    return out
