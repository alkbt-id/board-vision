"""Why a move is worse, in words, from Stockfish's line after it (no LLM) — for a wrong move of a task that Claude
did not explain in advance (vision/sources/prepare.py explains the likeliest ones).

The templates come from «Шахматный тренер» (coach/explain.py): play the line, count what each side takes until the
captures settle, and name the result — «теряешь коня», «мат», or, when no material changes, how much worse the
position is.
"""

import chess

VAL = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
ACC = {chess.PAWN: "пешку", chess.KNIGHT: "коня", chess.BISHOP: "слона", chess.ROOK: "ладью", chess.QUEEN: "ферзя"}
ACC_PL = {chess.PAWN: "пешки", chess.KNIGHT: "коня", chess.BISHOP: "слона", chess.ROOK: "ладьи", chess.QUEEN: "ферзя"}
NUMS = {2: "две", 3: "три", 4: "четыре"}
NUMS_M = {2: "два", 3: "три", 4: "четыре"}


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def pieces_text(types: list[int]) -> str:
    """[ROOK, PAWN, PAWN] -> «ладью и две пешки» (accusative)."""
    parts = []
    for t in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN):
        n = types.count(t)
        if n == 1:
            parts.append(ACC[t])
        elif n > 1:
            word = (NUMS if t in (chess.PAWN, chess.ROOK) else NUMS_M).get(n, str(n))
            noun = ACC_PL[t] if n < 5 else {chess.PAWN: "пешек", chess.ROOK: "ладей"}.get(t, ACC_PL[t])
            parts.append(f"{word} {noun}")
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " и " + parts[-1]


def san_line(board: chess.Board, moves: list[chess.Move]) -> str:
    """«12.Qd2 Nf3+ 13.Kh1 Nxd2» — with move numbers, as far as the moves are legal."""
    b = board.copy(stack=False)
    out = []
    for i, m in enumerate(moves):
        if m not in b.legal_moves:
            break
        num = b.fullmove_number
        if b.turn == chess.WHITE:
            out.append(f"{num}.{b.san(m)}")
        else:
            out.append(f"{num}…{b.san(m)}" if i == 0 else b.san(m))
        b.push(m)
    return " ".join(out)


def material(board: chess.Board, moves: list[chess.Move], me: chess.Color) -> tuple[list[int], list[int], bool]:
    """(my pieces lost, his pieces won, mate) along `moves`, up to the last capture that is not answered by another
    capture (a trailing exchange still to be answered is not counted); equal trades cancel out."""
    b = board.copy(stack=False)
    plies = []
    for m in moves:
        if m not in b.legal_moves:
            break
        cap = b.piece_at(m.to_square) if b.is_capture(m) and not b.is_en_passant(m) else None
        if b.is_en_passant(m):
            cap = chess.Piece(chess.PAWN, not b.turn)
        plies.append((b.turn == me, cap.piece_type if cap else None))
        b.push(m)
        if b.is_checkmate():
            return [], [], True
    end = len(plies) - 1
    while end >= 0 and plies[end][1] is not None:
        end -= 1
    lost = [t for mine, t in plies[: end + 1] if t and not mine]
    won = [t for mine, t in plies[: end + 1] if t and mine]
    for t in list(lost):
        if t in won:
            lost.remove(t)
            won.remove(t)
    return lost, won, False


def pawns(cp: int) -> str:
    """+0,8 / −1,5: an evaluation in pawns, as a Russian reader writes it."""
    v = cp / 100
    return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):.1f}".replace(".", ",")


def why_worse(board: chess.Board, move: chess.Move, pv: list[chess.Move], now: int | None, best: int | None,
              best_san: str) -> str:
    """One or two sentences: what Stockfish's line after `move` does (`pv`: his reply first), and how the position
    then compares with the best move (`now`, `best`: evaluations for the mover, centipawns)."""
    me = board.turn
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    shown = san_line(after, pv[:5])
    lost, won, mate = material(after, pv[:8], me)
    if mate:
        return f"После {san} соперник ставит мат: {shown}."
    if now is not None and now <= -9000:
        return f"После {san} у соперника форсированный мат: {shown}."
    if sum(VAL[t] for t in lost) - sum(VAL[t] for t in won) >= 1:
        what = f"теряешь {pieces_text(lost)}" if not won else f"отдаёшь {pieces_text(lost)} за {pieces_text(won)}"
        if lost == [chess.ROOK] and won in ([chess.KNIGHT], [chess.BISHOP]):
            what = "теряешь качество"
        return f"После {san} {shown} — {what}."
    if now is not None and best is not None:
        return (f"После {san} материал не теряется, но позиция хуже: Stockfish даёт {pawns(now)} против {pawns(best)}"
                f" после {best_san}. Его ответ: {shown}.")
    return f"После {san} лучший ответ соперника: {shown}."
