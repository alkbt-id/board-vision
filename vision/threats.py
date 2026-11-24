"""What the opponent threatens right now: the drill's answer key, found by rules, without an engine.

The position is the one right after the opponent's move, with the user to move. A threat is
  capture  the opponent can take one of the user's pieces or pawns and come out ahead in material, counting the
           whole exchange on that square: every legal capture and recapture, cheapest piece first, so pieces lined
           up behind others (batteries, x-rays) join in as the line opens, and a pinned piece or the king only
           takes when the rules allow it. This is the rule the user is shown (the start screen says so); the
           review spells the exchange out (`line`) whenever there is one;
  mate     the opponent can mate in one.
Both are found by handing the move back to the opponent (a null move) and looking at his legal moves.
A position where the user is in check is not a drill position (`analyse` returns None).

The user answers with arrows "who takes -> whom": an arrow counts when it goes from a piece that can take (or
mate) to the threatened square; the opposite direction counts too.
"""

import chess

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
ORDER = {**VALUE, chess.KING: 100}  # who recaptures first: the cheapest piece, the king last
NAME = {chess.PAWN: "пешка", chess.KNIGHT: "конь", chess.BISHOP: "слон", chess.ROOK: "ладья",
        chess.QUEEN: "ферзь", chess.KING: "король"}
NAME_ACC = {chess.PAWN: "пешку", chess.KNIGHT: "коня", chess.BISHOP: "слона", chess.ROOK: "ладью",
            chess.QUEEN: "ферзя", chess.KING: "короля"}


def _taken(board: chess.Board, move: chess.Move) -> int:
    """Material the capture `move` takes at once (a promotion adds the new piece)."""
    piece = board.piece_at(move.to_square)
    gain = VALUE[piece.piece_type] if piece else 0
    if move.promotion:
        gain += VALUE[move.promotion] - 1
    return gain


def _swap(board: chess.Board, square: int) -> int:
    """The most the side to move wins by recapturing on `square` (0 when stopping is better)."""
    caps = list(board.generate_legal_captures(to_mask=chess.BB_SQUARES[square]))
    if not caps:
        return 0
    move = min(caps, key=lambda m: ORDER[board.piece_type_at(m.from_square) or chess.PAWN])
    gain = _taken(board, move)
    board.push(move)
    try:
        return max(0, gain - _swap(board, square))
    finally:
        board.pop()


def capture_gain(board: chess.Board, move: chess.Move) -> int:
    """What the capture `move` wins after the whole exchange on its square; negative when it loses material."""
    gain = _taken(board, move)
    board.push(move)
    try:
        return gain - _swap(board, move.to_square)
    finally:
        board.pop()


def _play_out(board: chess.Board, moves: list[chess.Move], square: int) -> list[chess.Move]:
    """`moves` already played on `square`, then each side takes back with its cheapest piece while that pays."""
    b = board.copy(stack=False)
    for m in moves:
        b.push(m)
    out = list(moves)
    while True:
        caps = list(b.generate_legal_captures(to_mask=chess.BB_SQUARES[square]))
        if not caps:
            return out
        move = min(caps, key=lambda m: ORDER[b.piece_type_at(m.from_square) or chess.PAWN])
        if capture_gain(b, move) <= 0:
            return out
        out.append(move)
        b.push(move)


def _line(board: chess.Board, moves: list[chess.Move]) -> dict:
    """{"line": SANs, "gain": material the first capturer ends up with}."""
    b = board.copy(stack=False)
    sans, gain, sign = [], 0, 1
    for m in moves:
        sans.append(b.san(m))
        gain += sign * _taken(b, m)
        sign = -sign
        b.push(m)
    return {"line": sans, "gain": gain}


def exchange(board: chess.Board, first: chess.Move) -> dict:
    """The exchange on `first`'s square as both sides would play it: each side takes back with its cheapest piece
    while that pays. When the defender would not take back at all, `if_recapture` shows what taking back gives."""
    square = first.to_square
    out = _line(board, _play_out(board, [first], square))
    if len(out["line"]) == 1:
        b = board.copy(stack=False)
        b.push(first)
        caps = list(b.generate_legal_captures(to_mask=chess.BB_SQUARES[square]))
        if caps:
            back = min(caps, key=lambda m: ORDER[b.piece_type_at(m.from_square) or chess.PAWN])
            out["if_recapture"] = _line(board, _play_out(board, [first, back], square))
    return out


def analyse(after: chess.Board) -> dict | None:
    """The threats against the side to move in `after` (the position right after the opponent's move).

    {"threats": [{"target": "d4", "piece": "B", "attackers": ["d8"], "best": "d8d4", "san": "Qxd4",
                  "gain": 3, "mate": false}, ...],
     "defended": ["c7", ...]}   — the user's pieces the opponent attacks that are safe (an exchange there loses)
    None when the side to move is in check or the game is over.
    """
    if after.is_check() or after.is_game_over():
        return None
    board = after.copy(stack=False)
    board.push(chess.Move.null())
    found: dict[int, dict] = {}
    for move in board.generate_legal_captures():
        if board.is_en_passant(move):
            continue
        gain = capture_gain(board, move)
        t = found.setdefault(move.to_square, {"attackers": set(), "gain": None, "best": None, "mate": False})
        t["attackers"].add(move.from_square)
        if t["gain"] is None or gain > t["gain"]:
            t["gain"], t["best"] = gain, move
    for move in board.legal_moves:
        board.push(move)
        mate = board.is_checkmate()
        board.pop()
        if not mate:
            continue
        t = found.setdefault(move.to_square, {"attackers": set(), "gain": None, "best": None, "mate": False})
        t["attackers"].add(move.from_square)
        if not t["mate"]:
            t["mate"], t["best"] = True, move
    threats, defended = [], []
    exchanges: dict[str, dict] = {}  # defended square -> the exchange there, which does not pay the opponent
    mates: dict | None = None  # mates on empty squares are one threat: any of their arrows finds it
    for square, t in sorted(found.items(), key=lambda kv: -(100 if kv[1]["mate"] else kv[1]["gain"] or 0)):
        piece = after.piece_at(square)
        if t["mate"] and piece is None:
            name = chess.square_name(square)
            if mates is None:
                best: chess.Move = t["best"]
                mates = {"target": name, "targets": [], "piece": None, "attackers": [], "best": best.uci(),
                         "san": board.san(best), "gain": 0, "mate": True}
                threats.insert(0, mates)
            mates["targets"].append(name)
            mates["attackers"] = sorted(set(mates["attackers"]) | {chess.square_name(s) for s in t["attackers"]})
        elif t["mate"] or (t["gain"] or 0) > 0:
            best = t["best"]
            threats.append({
                "target": chess.square_name(square), "targets": [chess.square_name(square)],
                "piece": piece.symbol().upper() if piece else None,
                "attackers": sorted(chess.square_name(s) for s in t["attackers"]),
                "best": best.uci(), "san": board.san(best), "gain": t["gain"] or 0, "mate": t["mate"],
                **({} if t["mate"] else {"exchange": exchange(board, best)}),
            })
        elif piece is not None:
            defended.append(chess.square_name(square))
            exchanges[chess.square_name(square)] = exchange(board, t["best"])
    return {"threats": threats, "defended": sorted(defended), "exchanges": exchanges}


def describe(threat: dict, after: chess.Board) -> str:
    """«слон d4 — бьёт ферзь d8» / «пешка e5 — бьют: пешка d4, конь f3» / «мат в один ход: Qh7# — ферзь d3»."""
    def who(square: str) -> str:
        p = after.piece_at(chess.parse_square(square))
        return f"{NAME[p.piece_type]} {square}" if p else square

    attackers = sorted(threat["attackers"], key=lambda s: ORDER[after.piece_type_at(chess.parse_square(s)) or chess.PAWN])
    if threat["piece"] is None:
        more = len(threat.get("targets") or []) - 1
        also = f" (и ещё {more} {'поле' if more == 1 else 'поля'} мата)" if more > 0 else ""
        return f"мат в один ход: {threat['san']} — {', '.join(who(s) for s in attackers)}{also}"
    by = f"бьёт {who(attackers[0])}" if len(attackers) == 1 else "бьют: " + ", ".join(who(s) for s in attackers)
    tail = " — и это мат" if threat["mate"] else ""
    return f"{who(threat['target'])} — {by}{tail}{swap_text(threat.get('exchange'))}"


def swap_text(ex: dict | None) -> str:
    """«; размен Nxe5 Nxe5 dxe5 — соперник +1» / «; если взять обратно: Rxe5 Rxe5 Rxe5 — соперник +3»."""
    if not ex:
        return ""
    if len(ex["line"]) > 1:
        return f"; размен {' '.join(ex['line'])} — соперник {signed(ex['gain'])}"
    back = ex.get("if_recapture")
    if back:
        return f"; если взять обратно: {' '.join(back['line'])} — соперник всё равно {signed(back['gain'])}"
    return ""


def signed(n: int) -> str:
    return f"+{n}" if n > 0 else "±0" if n == 0 else f"−{-n}"


def explain_extra(extra: dict, answer: dict, after: chess.Board) -> str:
    """Why an arrow was extra, in words."""
    a, b = extra["from"], extra["to"]
    head = f"лишняя стрелка {a}→{b}: "
    me = after.turn
    if extra["why"] == "defended":
        square = b if b in answer["defended"] else a
        ex = (answer.get("exchanges") or {}).get(square)
        if ex and ex["line"]:
            return head + f"фигура защищена — размен {' '.join(ex['line'])}, и соперник {signed(ex['gain'])}"
        return head + "фигура защищена — взять её без потерь нельзя"
    def who(sq: str) -> str:
        p = after.piece_at(chess.parse_square(sq))
        return f"{NAME[p.piece_type]} {sq}" if p else sq
    if extra["why"] == "wrong_attacker":
        for t in answer["threats"]:
            if b in (t.get("targets") or []) or a in (t.get("targets") or []):
                return head + "бьёт её не эта фигура, а " + ", ".join(who(x) for x in t["attackers"])
    for sq in (b, a):
        p = after.piece_at(chess.parse_square(sq)) if sq else None
        if p and p.color == me:
            return head + "эту фигуру никто не бьёт"
    return head + "здесь нет твоей фигуры под ударом"


def check(answer: dict, arrows: list[dict], after: chess.Board) -> dict:
    """Compare the user's arrows ({"from": "d8", "to": "d4"}) with the answer key.

    {"found": ["d4"], "missed": ["f7"], "extra": [{"from", "to", "why": "defended" | "not_attacked" | "wrong_attacker"}],
     "ok": bool, "lines": [text, ...]}"""
    by_square = {sq: t for t in answer["threats"] for sq in t.get("targets") or [t["target"]]}
    found, extra = set(), []
    for arrow in arrows:
        a, b = arrow.get("from"), arrow.get("to")
        hit = None
        for frm, to in ((a, b), (b, a)):
            t = by_square.get(to)
            if t and frm in t["attackers"]:
                hit = t["target"]
                break
        if hit:
            found.add(hit)
            continue
        if b in by_square or a in by_square:
            why = "wrong_attacker"
        elif b in answer["defended"] or a in answer["defended"]:
            why = "defended"
        else:
            why = "not_attacked"
        extra.append({"from": a, "to": b, "why": why})
    missed = [t["target"] for t in answer["threats"] if t["target"] not in found]
    lines = []
    for t in answer["threats"]:
        mark = "найдено" if t["target"] in found else "пропущено"
        lines.append(f"{mark}: {describe(t, after)}")
    extra_lines = [explain_extra(x, answer, after) for x in extra]
    return {"found": sorted(found), "missed": missed, "extra": extra, "ok": not missed and not extra, "lines": lines,
            "extra_lines": extra_lines}
