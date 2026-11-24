"""What the opponent threatens right now: the drill's answer key, found by rules, without an engine.

The position is the one right after the opponent's move, with the user to move. A threat is
  capture  the opponent can take one of the user's pieces or pawns and come out ahead in material, counting the
           whole exchange on that square: every legal capture and recapture, cheapest piece first, so pieces lined
           up behind others (batteries, x-rays) join in as the line opens, and a pinned piece or the king only
           takes when the rules allow it. This is the rule the user is shown (the start screen says so); the
           review spells the exchange out (`line`) whenever there is one;
  promotion  a pawn of the opponent can walk onto the last rank and come out ahead after the exchange there
           (a promotion with a capture is already a capture threat);
  mate     the opponent can mate in one.
Both are found by handing the move back to the opponent (a null move) and looking at his legal moves.
A position where the user is in check is not a drill position (`analyse` returns None).

Chances (2026-11-24) are the same rule turned round: what the user himself can win right now — his captures that
come out ahead after the whole exchange, his pawn walking in to queen, his mate in one. They are marked in the same
step with a circle on the opponent's piece (his king for a mate); a mate or a queening on an empty square is found
by playing it. An arrow from the user's own piece is his move, not a mark.

Tactics (2026-11-24, the user: «шахи считаются угрозой?», «а ходы форсированного мата?»): what only an engine
sees — a forced mate in 2–3 and a check that wins material (a fork with check) — are added for both sides by
`add_tactics` (vision/engine.forcing): his to the threats, the user's to the chances. A tactic is found by an arrow
of its first move (or from the piece to what it hits); a chance tactic by a circle on what it wins (on his king for
a mate) or by playing its first move.

The user answers with arrows "who takes -> whom": an arrow counts when it goes from a piece that can take (or
mate) to the threatened square; the opposite direction counts too, unless it is itself a threat or a chance.
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


def xray(board: chess.Board, color: chess.Color, square: int) -> list[str]:
    """`color`'s pieces lined up behind its direct attackers of `square` (a battery: a bishop behind a pawn, a rook
    behind a rook) — they join the exchange as the line opens, so an arrow from them counts too."""
    b = board.copy(stack=False)
    seen = set(b.attackers(color, square))
    front, out = set(seen), []
    while front:
        for s in front:
            b.remove_piece_at(s)
        new = set(b.attackers(color, square)) - seen
        out += sorted(chess.square_name(s) for s in new)
        seen |= new
        front = new
    return out


def _order(board: chess.Board, move: chess.Move) -> int:
    return ORDER[board.piece_type_at(move.from_square) or chess.PAWN]


def _swap(board: chess.Board, square: int) -> int:
    """The most the side to move wins by recapturing on `square` (0 when stopping is better)."""
    if board.piece_type_at(square) == chess.KING:
        return 0
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
     "defended": ["c7", ...],   — the user's pieces the opponent attacks that are safe (an exchange there loses)
     "chances": [...]}          — what the user can win himself right now, in the same form as "threats"
    None when the side to move is in check or the game is over.
    """
    if after.is_check() or after.is_game_over():
        return None
    board = after.copy(stack=False)
    board.push(chess.Move.null())
    key = opponent_key(board)
    key["chances"] = opponent_key(after.copy(stack=False))["threats"]
    return key


def opponent_key(board: chess.Board) -> dict:
    """The answer key with the opponent to move in `board` (what he can win right now): see `analyse`."""
    after = board
    found: dict[int, dict] = {}
    for move in board.generate_legal_captures():
        if board.is_en_passant(move) or board.piece_type_at(move.to_square) == chess.KING:
            continue
        gain = capture_gain(board, move)
        t = found.setdefault(move.to_square, {"attackers": set(), "gain": None, "best": None, "mate": False})
        t["attackers"].add(move.from_square)
        # the best capture: the one that wins most; of equal ones the cheapest piece (it is the one you would see)
        if t["gain"] is None or gain > t["gain"] or (gain == t["gain"] and _order(board, move) < _order(board, t["best"])):
            t["gain"], t["best"] = gain, move
    promos: dict[int, dict] = {}
    for move in board.legal_moves:
        if move.promotion == chess.QUEEN and not board.is_capture(move):
            gain = capture_gain(board, move)  # the new queen minus the pawn, minus what is won back there
            p = promos.setdefault(move.to_square, {"attackers": set(), "gain": None, "best": None})
            p["attackers"].add(move.from_square)
            if p["gain"] is None or gain > p["gain"]:
                p["gain"], p["best"] = gain, move
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
                "xray": xray(board, board.turn, square),
                "best": best.uci(), "san": board.san(best), "gain": t["gain"] or 0, "mate": t["mate"],
                **({} if t["mate"] else {"exchange": exchange(board, best)}),
            })
        elif piece is not None:
            defended.append(chess.square_name(square))
            exchanges[chess.square_name(square)] = exchange(board, t["best"])
    for square, p in promos.items():
        name = chess.square_name(square)
        if (p["gain"] or 0) <= 0 or any(name in (t.get("targets") or []) for t in threats):
            continue  # not winning, or that square is already a mate threat
        best = p["best"]
        threats.append({
            "target": name, "targets": [name], "piece": None, "promotion": True,
            "attackers": sorted(chess.square_name(s) for s in p["attackers"]),
            "best": best.uci(), "san": board.san(best), "gain": p["gain"], "mate": False,
            "exchange": exchange(board, best),
        })
    return {"threats": threats, "defended": sorted(defended), "exchanges": exchanges}


def add_tactics(key: dict, after: chess.Board, forcing) -> dict:
    """`key` (from `analyse`) plus the forcing lines `forcing(board)` finds (vision/engine.forcing): his, with the
    move handed to him, to "threats"; the user's to "chances". key["engine"] is True when the engine answered."""
    him = after.copy(stack=False)
    him.push(chess.Move.null())
    his, mine = forcing(chess.Board(him.fen())), forcing(after.copy(stack=False))
    if his is None or mine is None:
        return key
    key["threats"] = key["threats"] + tactic_items(chess.Board(him.fen()), his, key["threats"], "he")
    key["chances"] = (key.get("chances") or []) + tactic_items(after, mine, key.get("chances") or [], "you")
    key["engine"] = True
    return key


def tactic_items(board: chess.Board, lines: list[dict], known: list[dict], who: str) -> list[dict]:
    """Items for the forcing lines of the side to move in `board` that the capture rule does not give already
    (`known`): {"target": first move (uci, the item's key), "targets": its landing square, what it then attacks and
    the king when it checks, "attackers": [its piece], "tactic": "mate" | "check", "mate_in", "line": the moves
    numbered as `recapture_line` does, "wins": what the line takes}. `who` moves first: "he" or "you"."""
    other = {"he": "you", "you": "he"}[who]
    seen = {t["best"] for t in known}
    out = []
    for ln in lines:
        first = ln["pv"][0]
        if first.uci() in seen or (board.is_capture(first) and capture_gain(board, first) > 0 and not ln["mate"]):
            continue  # the rule has it already
        seen.add(first.uci())
        b = board.copy(stack=False)
        steps, wins = [], []
        for i, m in enumerate(ln["pv"]):
            if m not in b.legal_moves:
                break
            if i % 2 == 0 and b.piece_at(m.to_square):
                wins.append(chess.square_name(m.to_square))
            steps.append({"from": chess.square_name(m.from_square), "to": chess.square_name(m.to_square),
                          "san": b.san(m), "who": who if i % 2 == 0 else other})
            b.push(m)
        a = board.copy(stack=False)
        a.push(first)
        hits = _attacked_by(a, first)
        king = a.king(a.turn)
        landing = chess.square_name(first.to_square)
        targets = [landing, *hits["targets"], *([chess.square_name(king)] if hits["check"] and king is not None else [])]
        out.append({"target": first.uci(), "targets": list(dict.fromkeys(targets)), "piece": None,
                    "attackers": [chess.square_name(first.from_square)], "best": first.uci(), "san": board.san(first),
                    "gain": 0, "mate": bool(ln["mate"]), "mate_in": ln["mate"],
                    "tactic": "mate" if ln["mate"] else "check", "line": steps, "wins": wins})
    return out


def _who(after: chess.Board, square: str) -> str:
    p = after.piece_at(chess.parse_square(square))
    return f"{NAME[p.piece_type]} {square}" if p else square


def describe(threat: dict, after: chess.Board) -> str:
    """One threat in a few words: «слон d4 ← ферзь d8» / «пешка e5 ← пешка d4, конь f3» / «мат: Qh7#»."""
    if threat.get("tactic"):
        return f"{'мат в ' + str(threat['mate_in']) if threat['mate'] else 'шах'}: {' '.join(s['san'] for s in threat['line'])}"
    attackers = sorted(threat["attackers"], key=lambda s: ORDER[after.piece_type_at(chess.parse_square(s)) or chess.PAWN])
    if threat.get("promotion"):
        return f"пешка {attackers[0]} проходит в ферзи: {threat['san']}"
    if threat["piece"] is None:
        return f"мат: {threat['san']}"
    tail = ", и это мат" if threat["mate"] else ""
    behind = threat.get("xray") or []
    return f"{_who(after, threat['target'])} ← {', '.join(_who(after, s) for s in [*attackers, *behind])}{tail}"


def swap_text(ex: dict | None) -> str:
    """The exchange behind a threat, for the small line under it: «размен hxg6 Qxg6 → соперник +2» /
    «взять обратно: Rxe5 Rxe5 Rxe5 → соперник +3»; "" when there is no exchange to show."""
    if not ex:
        return ""
    if len(ex["line"]) > 1:
        return f"размен {' '.join(ex['line'])} → соперник {signed(ex['gain'])}"
    back = ex.get("if_recapture")
    if back:
        return f"взять обратно: {' '.join(back['line'])} → соперник всё равно {signed(back['gain'])}"
    return ""


def signed(n: int) -> str:
    return f"+{n}" if n > 0 else "±0" if n == 0 else f"−{-n}"


def explain_extra(extra: dict, answer: dict, after: chess.Board) -> dict:
    """Why an arrow was extra: {"text": short, "detail": the exchange or the real attacker, may be ""}."""
    a, b = extra["from"], extra["to"]
    head = f"{a}→{b}"
    if extra["why"] == "defended":
        square = b if b in answer["defended"] else a
        ex = (answer.get("exchanges") or {}).get(square)
        detail = f"размен {' '.join(ex['line'])} → соперник {signed(ex['gain'])}" if ex and ex["line"] else ""
        return {"text": f"{head}: защищена", "detail": detail}
    if extra["why"] == "wrong_attacker":
        for t in answer["threats"]:
            if b in (t.get("targets") or []) or a in (t.get("targets") or []):
                return {"text": f"{head}: бьёт другая фигура", "detail": ", ".join(_who(after, x) for x in t["attackers"])}
    for sq in (b, a):
        p = after.piece_at(chess.parse_square(sq)) if sq else None
        if p and p.color == after.turn:
            return {"text": f"{head}: её никто не бьёт", "detail": ""}
    return {"text": f"{head}: тут нечего брать", "detail": ""}


def check(answer: dict, arrows: list[dict], after: chess.Board, with_chances: bool = True,
          played: dict | None = None, with_tactics: bool = True) -> dict:
    """Compare the user's arrows ({"from": "d8", "to": "d4"}) with the answer key: his threats and, with
    `with_chances` (answers given before 2026-11-24 were not asked for them), the user's own chances.

    {"found": ["d4"], "missed": ["f7"], "extra": [{"from", "to", "why": "defended" | "not_attacked" | "wrong_attacker"}],
     "ok": bool, "n": threats + chances asked, "chances": with_chances, "tactics": with_tactics (answers given before
     the engine's tactics were asked for are checked without them),
     "items": [{"target", "found", "text", "detail"}], "extras": [{"text", "detail"}]}
    `played`: the user's own move ({"from", "to"}), drawn instead of finishing the marking: a chance it takes is
    found (he plays it), and it is never an extra arrow.
    A circle is an arrow of no length ({"from": "e5", "to": "e5"}): it marks a chance on the opponent's piece there
    (on his king: a mate); a circle where nothing can be won is extra ("why": "not_winnable")."""
    circles = [a.get("from") for a in arrows if a.get("from") and a.get("from") == a.get("to")]
    arrows = [a for a in arrows if not (a.get("from") and a.get("from") == a.get("to"))]
    keys = [*answer["threats"], *((answer.get("chances") or []) if with_chances else [])]
    if not with_tactics:
        keys = [t for t in keys if not t.get("tactic")]
    chances = [t for t in (answer.get("chances") or []) if with_tactics or not t.get("tactic")]
    by_square: dict[str, list[dict]] = {}
    for t in keys:
        for sq in t.get("targets") or [t["target"]]:
            by_square.setdefault(sq, []).append(t)

    def match(frm: str | None, to: str | None) -> dict | None:
        for t in by_square.get(to, []):
            if frm in t["attackers"] or frm in (t.get("xray") or []):
                return t
        return None

    found, extra = set(), []
    for arrow in arrows:
        a, b = arrow.get("from"), arrow.get("to")
        t = match(a, b) or match(b, a)  # as drawn first: a threat and a chance can share two squares
        if t:
            found.add(t["target"])
            continue
        if b in by_square or a in by_square:
            why = "wrong_attacker"
        elif b in answer["defended"] or a in answer["defended"]:
            why = "defended"
        else:
            why = "not_attacked"
        extra.append({"from": a, "to": b, "why": why})
    if played and with_chances:
        for t in chances:
            if played["to"] in (t.get("targets") or [t["target"]]) and played["from"] in t["attackers"]:
                found.add(t["target"])
    king = after.king(not after.turn)
    his_king = chess.square_name(king) if king is not None else None
    for sq in circles:
        t = next((t for t in chances if with_chances and
                  (sq in (t.get("targets") or [t["target"]]) or sq in (t.get("wins") or [])
                   or (t["mate"] and sq == his_king))), None)
        if t:
            found.add(t["target"])
        else:
            extra.append({"from": sq, "to": sq, "why": "not_winnable"})
    missed = [t["target"] for t in keys if t["target"] not in found]
    items = [{"target": t["target"], "found": t["target"] in found, "text": describe(t, after),
              "detail": "" if t["mate"] else swap_text(t.get("exchange"))} for t in keys]
    return {"found": sorted(found), "missed": missed, "extra": extra, "ok": not missed and not extra,
            "n": len(keys), "chances": with_chances, "tactics": with_tactics,
            "items": items, "extras": [explain_extra(x, answer, after) for x in extra]}


# ---------- the check of the user's own move («проверь свой ход») ----------

def own_move(before: chess.Board, move: chess.Move) -> dict:
    """What the opponent can win at once after the user's `move` in `before` (the user to move there): take, mate,
    queen a pawn — as in `analyse`, with "attacks": [] for the tactical replies vision/engine.py adds (a fork, a
    pin, a trap: a reply that wins material but is not a capture right now)."""
    b = before.copy(stack=False)
    b.push(move)
    if b.is_game_over():
        return {"threats": [], "attacks": [], "defended": [], "exchanges": {}}
    key = opponent_key(b)
    if before.is_capture(move) and not before.is_en_passant(move) and capture_gain(before, move) >= 0:
        # your capture starts an exchange you do not lose: his taking back there is its second half, not a threat
        # (the user, 2026-11-24, after Bxh4 gxh4 Qxh4: «я взял слоном коня!»)
        to = chess.square_name(move.to_square)
        key["threats"] = [t for t in key["threats"] if t["target"] != to or t["mate"]]
    key["attacks"] = []
    return key


def reply_item(after: chess.Board, reply: chess.Move, loss: int, already: set[str] = frozenset()) -> dict:
    """An «attack» item for `reply` (the opponent's move in `after`): what it newly puts under attack, by the rule
    (`already`: the squares that were threatened anyway)."""
    san = after.san(reply)
    b = after.copy(stack=False)
    b.push(reply)
    check = b.is_check()
    c = b.copy(stack=False)
    c.push(chess.Move.null())
    targets = sorted({t["target"] for t in opponent_key(c)["threats"] if t["piece"]} - set(already))
    mv = {"move": reply.uci(), "san": san, "from": chess.square_name(reply.from_square),
          "to": chess.square_name(reply.to_square), "targets": targets}
    return {"key": reply.uci(), "moves": [mv], "targets": targets, "fork": len(targets) + int(check) >= 2,
            "check": check, "loss": loss}


def _attacked_by(board: chess.Board, move: chess.Move) -> dict:
    """What `move` (just played in `board`) attacks: its targets by the capture rule and whether it gives check."""
    c = board.copy(stack=False)
    c.push(chess.Move.null())
    targets = sorted({t["target"] for t in opponent_key(c)["threats"] if t["piece"]})
    return {"targets": targets, "check": board.is_check()}


def recapture_line(after: chess.Board, reply: chess.Move, best) -> list[dict]:
    """Why a capture that looks like a fair trade by the capture rule wins all the same (an overloaded defender:
    Bxc4, and Nxc4 lets Nxc2+ fork king and rook): [his capture, your natural recapture, his answer to it], each
    {"from", "to", "san", "who": "he" | "you"}; the last also "targets" and "check" — what it attacks.
    `best(board)` gives his answer (Stockfish). [] when `reply` is not such a capture or cannot be taken back."""
    if not after.is_capture(reply) or capture_gain(after, reply) > 0:
        return []
    b = after.copy(stack=False)
    step = lambda m, who: {"from": chess.square_name(m.from_square), "to": chess.square_name(m.to_square),
                           "san": b.san(m), "who": who}
    line = [step(reply, "he")]
    b.push(reply)
    caps = list(b.generate_legal_captures(to_mask=chess.BB_SQUARES[reply.to_square]))
    if not caps:
        return []
    back = min(caps, key=lambda m: _order(b, m))
    line.append(step(back, "you"))
    b.push(back)
    punish = best(b)
    if punish is None or punish not in b.legal_moves:
        return []
    line.append(step(punish, "he"))
    b.push(punish)
    line[-1].update(_attacked_by(b, punish))
    return line


def describe_attack(a: dict, board: chess.Board) -> str:
    """«вилка Nc7+: король, ладья a8» / «Re8: нападение на ферзя e5» / «Bb4 — выигрывает материал»."""
    san = a["moves"][0]["san"]
    names = (["король"] if a.get("check") else []) + [_who(board, t) for t in a["targets"]]
    if a.get("fork") and len(names) >= 2:
        return f"вилка {san}: " + ", ".join(names)
    if names:
        return f"{san}: нападение на " + ", ".join(names)
    return f"{san} — выигрывает материал"


def check_own(key: dict, arrows: list[dict], board: chess.Board) -> dict:
    """The user's arrows after his own move against `own_move`'s key (`board`: the position after his move).
    An arrow finds a «take» threat as in `check`, and an attack when it is the opponent's move itself, or goes from
    the square where his piece lands to its target."""
    by_square = {sq: t for t in key["threats"] for sq in t.get("targets") or [t["target"]]}
    found, found_a, extra = set(), set(), []
    for arrow in arrows:
        a, b = arrow.get("from"), arrow.get("to")
        hit = None
        for frm, to in ((a, b), (b, a)):
            t = by_square.get(to)
            if t and (frm in t["attackers"] or frm in (t.get("xray") or [])):
                hit = t["target"]
                break
        if hit:
            found.add(hit)
            continue
        for at in key["attacks"]:
            his = [s for s in at.get("line") or [] if s["who"] == "he"]  # his moves of the line count too
            for mv in [*at["moves"], *his]:
                if (a, b) in ((mv["from"], mv["to"]), (mv["to"], mv["from"])) or \
                        (a == mv["to"] and b in (mv.get("targets") or at["targets"])) or \
                        (b == mv["to"] and a in (mv.get("targets") or at["targets"])):
                    found_a.add(at["key"])
                    hit = at["key"]
            if hit:
                break
        if hit:
            continue
        why = "defended" if (b in key["defended"] or a in key["defended"]) else "not_attacked"
        extra.append({"from": a, "to": b, "why": why})
    missed = [t["target"] for t in key["threats"] if t["target"] not in found]
    missed_a = [at["key"] for at in key["attacks"] if at["key"] not in found_a]
    items = [{"target": t["target"], "found": t["target"] in found, "text": describe(t, board),
              "detail": "" if t["mate"] else swap_text(t.get("exchange"))} for t in key["threats"]]
    items += [{"target": at["key"], "found": at["key"] in found_a, "text": describe_attack(at, board), "detail": ""}
              for at in key["attacks"]]
    extras = []
    for x in extra:
        e = explain_extra(x, key, board) if x["why"] == "defended" else {"text": f"{x['from']}→{x['to']}: этого он не сможет", "detail": ""}
        extras.append(e)
    return {"found": sorted(found | found_a), "missed": missed + missed_a, "extra": extra,
            "ok": not missed and not missed_a and not extra, "items": items, "extras": extras}
