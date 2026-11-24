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
  chance        there is something of his to win yourself (marked in the same step since 2026-11-24; counted only
                for answers that were asked for it), each one beyond the first adds to «more»
  fork          a check that wins material, his or yours (the engine's, since 2026-11-24, for answers asked for it)
  mate_long     a forced mate in 2–3, his or yours (likewise)
The check of your own move («проверь свой ход», vision/play.commit) is scored on the same scale with its own points:
  own           it is your own move: the position after it has to be imagined (+100)
  attack        a fork or a new attack on your queen to see, not only a capture (+150)
  threat, more, mate, exchange   as above, for what he can win at once
The points start as a guess (WEIGHTS) and are adjusted by the answers themselves, a little after each one: a kind
of position you miss more often than expected becomes worth more.

Score of one answer: a quiet position — 1 without arrows, minus 0.5 per extra arrow; a position with threats — the
share of threats found, minus 0.25 per extra arrow; never below 0.
"""

import json

import chess

from . import store, threats

START = 1500
BASE = 1100  # a quiet position without decoys
K = 24  # rating step
ETA = 4  # how fast the difficulty points learn
WEIGHTS = {
    "threat": 300, "more": 100, "discovered": 150, "old": 100, "backward": 100, "horizontal": 75, "long": 50,
    "knight": 50, "exchange": 150, "mate": 50, "promotion": 100, "decoys": 100, "own": 100, "attack": 150,
    "chance": 200, "fork": 200, "mate_long": 250,
}


def features(fen: str, move_uci: str, answer: dict) -> dict[str, float]:
    """What makes this position hard to read (see the module doc); missing keys are 0."""
    before = chess.Board(fen)
    move = chess.Move.from_uci(move_uci)
    opp = before.turn  # the side that makes the move, i.e. the opponent
    after = before.copy(stack=False)
    after.push(move)
    found = [t for t in answer["threats"] if not t.get("tactic")]  # the engine's: `with_chances`, per answer
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


def features_own(answer: dict) -> dict[str, float]:
    """What makes the check of your own move hard (see the module doc)."""
    found = [t for t in answer.get("threats") or [] if not t.get("optional")]
    attacks = answer.get("attacks") or []
    f: dict[str, float] = {"own": 1.0}
    if not found and not attacks:
        f["decoys"] = float(min(2, len(answer.get("defended") or [])))
        return f
    f["threat"] = 1.0
    f["more"] = float(len(found) + len(attacks) - 1)
    if attacks:
        f["attack"] = 1.0
    for t in found:
        if t.get("mate"):
            f["mate"] = 1.0
        ex = t.get("exchange") or {}
        if len(ex.get("line") or []) >= 3 or ex.get("if_recapture"):
            f["exchange"] = 1.0
    return f


def with_chances(f: dict[str, float], answer_json: str, result_json: str | None) -> dict[str, float]:
    """`f` plus the chances of the position, when the answer was asked for them."""
    import json
    result = json.loads(result_json or "{}")
    answer = json.loads(answer_json)
    tactics = bool(result.get("tactics"))
    chances = [t for t in answer.get("chances") or [] if tactics or not t.get("tactic")] if result.get("chances") else []
    his = [t for t in answer.get("threats") or [] if t.get("tactic")] if tactics else []
    if not chances and not his:
        return f
    f = dict(f)
    f.pop("decoys", None)  # a position with something to find is not a quiet one
    if chances:
        f["chance"] = 1.0
    extra = len(chances) + len(his)
    f["more"] = f.get("more", 0.0) + extra - (0 if "threat" in f else 1)
    if his:
        f["threat"] = 1.0
    for t in [*chances, *his]:
        if t.get("tactic") == "check":
            f["fork"] = 1.0
        elif t.get("tactic") == "mate":
            f["mate_long"] = 1.0
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


def _item_features(item_id: int, fen: str, move: str, answer_json: str, source: str = "") -> dict:
    if item_id not in _feature_cache:
        import json
        answer = json.loads(answer_json)
        _feature_cache[item_id] = features_own(answer) if source == "ownmove" else features(fen, move, answer)
    return _feature_cache[item_id]


_last: dict = {}  # the rating and the difficulty points after the latest replay


def current() -> tuple[float, dict[str, float]]:
    """The rating now and the difficulty points learned so far (a replay of every answer)."""
    replay()
    return _last.get("rating", float(START)), _last.get("weights", dict(WEIGHTS))


def difficulty(item: dict, weights: dict[str, float]) -> float:
    """How hard a pool position is for a new answer (with chances asked): `item` has id, fen, move, answer, source."""
    f = _item_features(item["id"], item["fen"], item["move"], item["answer"], item["source"])
    if item["source"] != "ownmove":
        f = with_chances(f, item["answer"], '{"chances": true, "tactics": true}')
    return BASE + sum(weights.get(k, 0) * v for k, v in f.items())


def replay() -> list[dict]:
    """Every first-time answer in order, with the rating after it: [{session, ts, rating, difficulty, score, ms}].
    The difficulty points learn along the way, so the whole history is replayed from the start each time."""
    rows = store.q(
        "SELECT a.session, a.series, a.ts, a.ms, a.n_threats, a.found, a.extra, a.result, i.id AS item, i.fen, i.move,"
        " i.answer,"
        " i.source"
        " FROM attempts a JOIN items i ON i.id = a.item"
        " WHERE a.id = (SELECT MIN(b.id) FROM attempts b WHERE b.item = a.item) ORDER BY a.ts")
    w = dict(WEIGHTS)
    rating = float(START)
    out = []
    for r in rows:
        f = _item_features(r["item"], r["fen"], r["move"], r["answer"], r["source"])
        if r["source"] != "ownmove":
            if r["result"] and json.loads(r["result"]).get("threats_asked") is False:
                f = {}  # only his pieces to win were asked: the chances make it hard, not his threats
            f = with_chances(f, r["answer"], r["result"])
        diff = BASE + sum(w.get(k, 0) * v for k, v in f.items())
        e = expected(diff, rating)
        s = score(r["n_threats"] or 0, r["found"] or 0, r["extra"] or 0)
        rating += K * (s - e)
        for k, v in f.items():  # missed more than expected -> this kind is harder than thought
            w[k] = w.get(k, 0) + ETA * (e - s) * v
        out.append({"session": r["session"], "series": r["series"], "ts": r["ts"], "rating": rating, "difficulty": diff,
                    "score": s,
                    "ms": r["ms"]})
    _last.update(rating=rating, weights=dict(w))
    return out
