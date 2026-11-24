"""The answer key on the user's own blunders and on the rule's edge cases."""

import chess

from vision import threats


def targets(fen: str) -> dict[str, dict]:
    answer = threats.analyse(chess.Board(fen))
    assert answer is not None
    return {t["target"]: t for t in answer["threats"]}


def test_qf3_bc4_hits_f7_with_mate():
    # 2026-11-24 09:03, the user (Black) played 4…Nd4?? here and got 5.Qxf7#
    got = targets("r1bqkbnr/pppp1p1p/2n3p1/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 1 4")
    assert "f7" in got
    assert got["f7"]["mate"]
    assert set(got["f7"]["attackers"]) == {"c4", "f3"}


def test_qf3_ng5_hits_f7_with_mate():
    # 2026-11-24 13:41, the user (Black) played 10…Bb7?? and got 11.Qxf7#
    got = targets("r1bqk2r/2ppnpbp/p5p1/1p1Pp1N1/8/2PP1Q2/PP3PPP/RNB1K2R b KQkq - 4 10")
    assert got["f7"]["mate"]
    assert set(got["f7"]["attackers"]) == {"f3", "g5"}


def test_knight_move_opens_the_d_file_on_the_bishop():
    # 2026-11-24 13:53: 20…Nc7 opened Qd8 onto Bd4; the user (White) played 21.Qg6?? Qxd4
    got = targets("r2qr1k1/ppn3p1/2p2p1p/8/2PB4/PP1P2Q1/5PPP/R4RK1 w - - 1 21")
    assert got["d4"]["attackers"] == ["d8"]
    assert got["d4"]["piece"] == "B"
    assert got["d4"]["gain"] == 3


def test_quiet_opening_position_has_no_threats():
    answer = threats.analyse(chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"))
    assert answer == {"threats": [], "defended": [], "exchanges": {}, "chances": []}


def test_battery_wins_a_knight_a_single_rook_does_not():
    battery = targets("4r1k1/8/8/4n3/8/8/4R3/4R1K1 b - - 0 1")
    assert battery["e5"]["gain"] == 3
    single = threats.analyse(chess.Board("4r1k1/8/8/4n3/8/8/4R3/6K1 b - - 0 1"))
    assert single is not None
    assert single["threats"] == []
    assert single["defended"] == ["e5"]


def test_pinned_knight_does_not_take():
    # Nc3 is pinned to Ke1 by Bb4: it cannot take the queen on d5
    answer = threats.analyse(chess.Board("4k3/8/8/3q4/1b6/2N5/8/4K3 b - - 0 1"))
    assert answer is not None
    assert "d5" not in {t["target"] for t in answer["threats"]}


def test_back_rank_mate_without_a_capture():
    got = targets("6k1/5ppp/8/8/8/8/8/R5K1 b - - 0 1")
    assert got["a8"]["mate"]
    assert got["a8"]["piece"] is None
    assert got["a8"]["attackers"] == ["a1"]


def test_mates_on_several_squares_are_one_threat():
    # the queen on e2 mates on d1, e1 and f1: one threat, an arrow to any of them finds it
    after = chess.Board("6k1/8/8/8/8/8/4qPPP/7K w - - 0 1")
    answer = threats.analyse(after)
    assert answer is not None
    mates = [t for t in answer["threats"] if t["piece"] is None]
    assert len(mates) == 1
    assert set(mates[0]["targets"]) == {"d1", "e1", "f1"}
    got = threats.check(answer, [{"from": "e2", "to": mates[0]["targets"][-1]}], after)
    assert mates[0]["target"] in got["found"]


def test_cheaper_attacker_on_a_defended_queen():
    # Bb5 attacks the queen on d3 even though the queen is defended
    got = targets("4k3/8/8/1b6/8/3Q4/4P3/4K3 w - - 0 1")
    assert got["d3"]["gain"] == 6


def test_in_check_is_not_a_drill_position():
    assert threats.analyse(chess.Board("4k3/8/8/8/8/8/4q3/4K3 w - - 0 1")) is None


def test_check_counts_arrows_both_ways_and_explains_extras():
    after = chess.Board("r2qr1k1/ppn3p1/2p2p1p/8/2PB4/PP1P2Q1/5PPP/R4RK1 w - - 1 21")
    answer = threats.analyse(after)
    assert answer is not None
    ok = threats.check(answer, [{"from": "d4", "to": "d8"}], after)
    assert ok["found"] == ["d4"]
    assert ok["missed"] == []
    miss = threats.check(answer, [{"from": "a7", "to": "a3"}], after)
    assert miss["missed"] == ["d4"]
    assert miss["extra"][0]["why"] == "not_attacked"
    assert miss["items"][0]["text"] == "слон d4 ← ферзь d8"
    assert miss["extras"][0]["text"] == "a7→a3: её никто не бьёт"


def test_review_spells_out_the_exchange():
    # the knight on e5 is defended by the rook, but the second white rook behind the first decides it
    after = chess.Board("4r1k1/8/8/4n3/8/8/4R3/4R1K1 b - - 0 1")
    answer = threats.analyse(after)
    assert answer is not None
    got = threats.check(answer, [], after)
    assert got["items"][0]["text"] == "конь e5 ← ладья e2, ладья e1"
    assert got["items"][0]["detail"] == "взять обратно: Rxe5 Rxe5 Rxe5 → соперник всё равно +3"
    # one rook only: the knight is safe, and an arrow on it is explained by the exchange
    after = chess.Board("4r1k1/8/8/4n3/8/8/4R3/6K1 b - - 0 1")
    answer = threats.analyse(after)
    assert answer is not None
    got = threats.check(answer, [{"from": "e2", "to": "e5"}], after)
    assert got["extras"] == [{"text": "e2→e5: защищена", "detail": "размен Rxe5 Rxe5 → соперник −2"}]


def test_rating_features_see_a_discovered_attack_and_a_quiet_decoy():
    from vision import rating
    # 20…Nc7 opened the d-file: the queen d8 did not move, its line to d4 was opened
    before = "r2qr1k1/pp4p1/2p2p1p/3n4/2PB4/PP1P2Q1/5PPP/R4RK1 b - - 0 20"
    b = chess.Board(before)
    b.push_uci("d5c7")
    f = rating.features(before, "d5c7", threats.analyse(b))
    assert f["threat"] == 1 and f["discovered"] == 1
    assert rating.score(2, 1, 0) == 0.5
    assert rating.score(0, 0, 1) == 0.5


def test_a_pawn_walking_in_to_queen_is_a_threat():
    # 2026-11-24 on the iPad: after …Ra2-a1 the rook hits g1 and the pawn b2 queens on b1 (Rxb1 Rxb1 still wins)
    after = chess.Board("8/R7/P1k4P/2Pp4/3Pb3/4P1K1/1p3P2/r5R1 w - - 0 1")
    answer = threats.analyse(after)
    assert answer is not None
    got = {t["target"]: t for t in answer["threats"]}
    assert set(got) == {"g1", "b1"}
    assert got["b1"]["promotion"] and got["b1"]["attackers"] == ["b2"]
    res = threats.check(answer, [{"from": "a1", "to": "g1"}, {"from": "b2", "to": "b1"}], after)
    assert res["ok"]
    assert "проходит в ферзи" in res["items"][1]["text"]


def test_own_move_static_part_and_a_reply_item():
    # 2026-11-24: Qe2xe5 left the knight d4 hanging; the static part of the own-move check finds it
    before = chess.Board("r2q3r/pp3kpn/2p4p/2b1p3/3N4/2N4P/PPP1QPP1/R1B2RK1 w - - 0 1")
    key = threats.own_move(before, chess.Move.from_uci("e2e5"))
    assert [t["target"] for t in key["threats"]] == ["d4"] and key["attacks"] == []
    assert key["threats"][0]["best"] == "c5d4"  # the red arrow comes from the cheapest piece that wins it
    # a reply item (what vision/engine.py adds for a tactic): the arrow is the reply, or from it to its target
    after = before.copy()
    after.push_uci("e2e5")
    item = threats.reply_item(after, chess.Move.from_uci("h8e8"), 300, {"d4"})
    key["attacks"].append(item)
    assert "e5" in item["targets"]
    got = threats.check_own(key, [{"from": "c5", "to": "d4"}, {"from": "h8", "to": "e8"}], after)
    assert got["ok"]
    assert "нападение на ферзь e5" in threats.describe_attack(item, after)


def test_a_bishop_behind_a_pawn_is_an_attacker_too():
    # 2026-11-24 on the iPad: after d4-d5 the pawn hits e6 and the bishop c4 stands behind it; both arrows count
    after = chess.Board("rn1qkb1r/pp3ppp/4pn2/2pP3b/2B1P3/P1N2P2/1P4PP/R1BQK1NR b KQkq - 0 1")
    answer = threats.analyse(after)
    assert answer is not None
    e6 = next(t for t in answer["threats"] if t["target"] == "e6")
    assert e6["attackers"] == ["d5"] and e6["xray"] == ["c4"]
    got = threats.check(answer, [{"from": "d5", "to": "e6"}, {"from": "c4", "to": "e6"}], after)
    assert "e6" in got["found"] and not [x for x in got["extra"] if "e6" in (x["from"], x["to"])]


def test_a_fair_looking_trade_that_loses_gets_its_line():
    # the user's game 2026-11-24: after 1.Nf3 the e3 knight guards both c4 and c2 — Bxc4, and Nxc4 lets Nxc2+ fork
    after = chess.Board("rn2kb1r/ppp1pp1p/4b1p1/4P3/1nB5/4NN2/PPP2PPP/R1B1K2R b KQkq - 0 1")
    reply = chess.Move.from_uci("e6c4")
    assert threats.capture_gain(after, reply) == 0  # by the capture rule a fair trade
    line = threats.recapture_line(after, reply, lambda b: chess.Move.from_uci("b4c2"))
    assert [(s["from"], s["to"], s["who"]) for s in line] == [("e6", "c4", "he"), ("e3", "c4", "you"), ("b4", "c2", "he")]
    assert line[-1]["check"] and line[-1]["targets"] == ["a1"]
    # an arrow of his fork counts as seeing the tactic
    key = {"threats": [], "defended": [], "attacks": [{**threats.reply_item(after, reply, 400), "line": line}]}
    assert threats.check_own(key, [{"from": "b4", "to": "c2"}], after)["ok"]
    assert not threats.check_own(key, [], after)["ok"]
def test_chance_is_what_you_can_win_yourself():
    # white to move; the black knight on a5 hangs to the rook on a1, nothing of white's is attacked
    after = chess.Board("4k3/8/8/n7/8/8/8/R3K3 w - - 0 1")
    answer = threats.analyse(after)
    assert answer["threats"] == []
    assert [(t["attackers"], t["target"]) for t in answer["chances"]] == [(["a1"], "a5")]
    assert threats.check(answer, [{"from": "a1", "to": "a5"}], after)["ok"]
    assert threats.check(answer, [], after)["missed"] == ["a5"]
    # answers given before chances were asked for are checked without them
    old = threats.check(answer, [], after, with_chances=False)
    assert old["ok"] and old["n"] == 0 and not old["chances"]


def test_threat_and_chance_on_the_same_line_go_by_the_arrow_direction():
    # rooks a1 and a5 face each other, neither defended: he can take a1 (threat), you can take a5 (chance)
    after = chess.Board("4k3/8/8/r7/8/8/8/R3K3 w - - 0 1")
    answer = threats.analyse(after)
    threat = threats.check(answer, [{"from": "a5", "to": "a1"}], after)
    assert threat["found"] == ["a1"] and threat["missed"] == ["a5"]
    chance = threats.check(answer, [{"from": "a1", "to": "a5"}], after)
    assert chance["found"] == ["a5"] and chance["missed"] == ["a1"]
    both = threats.check(answer, [{"from": "a5", "to": "a1"}, {"from": "a1", "to": "a5"}], after)
    assert both["ok"] and both["n"] == 2


def test_your_move_drawn_at_once_takes_a_chance():
    # the pawn on b7 can queen (a chance); drawing b7-b8 as your move instead of marking it counts as found
    after = chess.Board("6k1/1P6/8/8/8/8/8/6K1 w - - 0 1")
    answer = threats.analyse(after)
    assert [t["target"] for t in answer["chances"]] == ["b8"]
    assert threats.check(answer, [], after, played={"from": "b7", "to": "b8"})["ok"]
    other = threats.check(answer, [], after, played={"from": "g1", "to": "f2"})
    assert other["missed"] == ["b8"] and other["extra"] == []


def test_a_chance_is_marked_with_a_circle_on_his_piece():
    after = chess.Board("4k3/8/8/n7/8/8/8/R3K3 w - - 0 1")  # the knight on a5 hangs to the rook
    answer = threats.analyse(after)
    assert threats.check(answer, [{"from": "a5", "to": "a5"}], after)["ok"]
    wrong = threats.check(answer, [{"from": "a5", "to": "a5"}, {"from": "e8", "to": "e8"}], after)
    assert wrong["found"] == ["a5"] and wrong["extra"] == [{"from": "e8", "to": "e8", "why": "not_winnable"}]


def test_a_mate_in_one_is_marked_with_a_circle_on_his_king():
    after = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")  # Ra8 mates
    answer = threats.analyse(after)
    assert any(t["mate"] for t in answer["chances"])
    assert threats.check(answer, [{"from": "g8", "to": "g8"}], after)["ok"]


def test_a_missed_threat_in_a_game_is_found():
    from vision.sources import missed
    board = chess.Board()
    moves = [board.push_san(m) for m in ["e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"]]
    found = missed.moments(moves)
    assert [(ply, what) for _, _, ply, what in found] == [(4, "mate")]
    before, move, _, _ = found[0]
    assert before.san(move) == "Bc4"


def test_his_check_that_forks_is_a_threat():
    # black to move; if black does nothing, Nc7+ forks the king and the rook a8 (a fake engine gives the line)
    after = chess.Board("r3k3/pp6/8/1N6/8/8/PP6/4K3 b - - 0 1")
    key = threats.analyse(after)
    line = [chess.Move.from_uci(u) for u in ("b5c7", "e8d7", "c7a8", "d7c6")]
    threats.add_tactics(key, after, lambda b: [{"pv": line, "mate": None}] if b.turn == chess.WHITE else [])
    t = [t for t in key["threats"] if t.get("tactic")]
    assert len(t) == 1 and t[0]["tactic"] == "check" and t[0]["attackers"] == ["b5"]
    assert set(t[0]["targets"]) >= {"c7", "a8", "e8"}
    assert threats.check(key, [{"from": "b5", "to": "c7"}], after)["ok"]  # the arrow of his check
    assert threats.check(key, [{"from": "b5", "to": "a8"}], after)["ok"]  # or of what it wins
    assert not threats.check(key, [], after)["ok"]
    assert threats.check(key, [], after, with_tactics=False)["ok"]  # an answer from before tactics


def test_your_forced_mate_is_a_chance_found_by_a_circle_on_his_king_or_by_playing_it():
    after = chess.Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    key = threats.analyse(after)
    key["chances"] = []  # pretend the rule's mate in one is not there: a fake mate in 2 instead
    line = [chess.Move.from_uci(u) for u in ("a1a7", "h7h6", "a7a8")]
    threats.add_tactics(key, after, lambda b: [{"pv": line, "mate": 2}] if b.turn == chess.WHITE else [])
    assert [t["tactic"] for t in key["chances"]] == ["mate"]
    assert threats.check(key, [{"from": "g8", "to": "g8"}], after)["ok"]
    assert threats.check(key, [], after, played={"from": "a1", "to": "a7"})["ok"]


def test_taking_back_after_your_own_fair_capture_is_not_a_threat():
    # the user's game 2026-11-24: Bxh4 (a knight), and gxh4 Qxh4 leaves him a pawn up — not a «missed» threat
    before = chess.Board("r1bqk1nr/ppp1bppp/2n5/3p4/4p2N/3P2P1/PPP1PPBP/RNBQ1RK1 b kq - 0 1")
    key = threats.own_move(before, chess.Move.from_uci("e7h4"))
    assert [t["target"] for t in key["threats"] if not t.get("optional")] == ["e4"]
    after = before.copy()
    after.push_uci("e7h4")
    key["attacks"] = []
    left_out = threats.check_own(key, [{"from": "d3", "to": "e4"}], after)
    assert left_out["ok"]  # gxh4 is no miss…
    drawn = threats.check_own(key, [{"from": "d3", "to": "e4"}, {"from": "g3", "to": "h4"}], after)
    assert drawn["ok"] and "h4" in drawn["found"]  # …and drawn it is right, not extra
    # a capture that loses the exchange keeps the threat: the queen takes a pawn defended by a pawn
    before = chess.Board("4k3/8/2p5/3p4/8/8/3Q4/4K3 w - - 0 1")
    key = threats.own_move(before, chess.Move.from_uci("d2d5"))
    assert [t["target"] for t in key["threats"]] == ["d5"]


def test_his_fork_after_your_move_is_found_by_its_arrow():
    # after your move he has Nc7+ forking king and rook (the engine's line, faked): its arrow is right, not extra
    board = chess.Board("r3k3/pp6/8/1N6/8/8/PP6/4K3 w - - 0 1")
    key = {"threats": [], "defended": [], "attacks": []}
    line = [chess.Move.from_uci(u) for u in ("b5c7", "e8d7", "c7a8", "d7c6")]
    key["threats"] += threats.tactic_items(board, [{"pv": line, "mate": None}], key["threats"], "he")
    assert threats.check_own(key, [{"from": "b5", "to": "c7"}], board)["ok"]
    res = threats.check_own(key, [], board)
    assert not res["ok"] and res["missed"] == ["b5c7"]


def test_a_battery_arrow_from_the_piece_behind_finds_the_threat():
    # the user's game 2026-11-24: after ...g5, Rxf8+ with Rf1 behind Rf7; he drew Rf1→f7 (and f7→f8)
    before = chess.Board("5rk1/2p2Rp1/p1qp3p/1p6/1P4Q1/P3P2P/1P4P1/5RK1 b - - 0 24")
    move = chess.Move.from_uci("g7g5")
    key = {**threats.own_move(before, move), "attacks": []}
    after = before.copy()
    after.push(move)
    res = threats.check_own(key, [{"from": "f1", "to": "f7"}], after, with_checks=False)
    assert res["ok"] and res["found"] == ["f8"]


def test_a_winning_check_line_ends_with_its_last_take():
    # the user's game 2026-11-24 (simplified): Rxf1+, the bishop takes back, axb6 wins the queen; the engine's 4th
    # move (h3) is not drawn
    board = chess.Board("1k5r/pp3p1p/1Q3p2/8/5Pn1/1P4P1/P4PBP/2r2RK1 b - - 0 21")
    line = [chess.Move.from_uci(u) for u in ("c1f1", "g2f1", "a7b6", "h2h3")]
    items = threats.tactic_items(board, [{"pv": line, "mate": None}], [], "he")
    assert [s["san"] for s in items[0]["line"]] == ["Rxf1+", "Bxf1", "axb6"]


def test_every_check_after_your_move_is_to_be_marked():
    # after ...g5 (the user's game): besides Rxf8+ (a capture threat) White has Qxg5+ — a check that loses the queen
    # but is still his check to see
    before = chess.Board("5rk1/2p2Rp1/p1qp3p/1p6/1P4Q1/P3P2P/1P4P1/5RK1 b - - 0 24")
    move = chess.Move.from_uci("g7g5")
    key = {**threats.own_move(before, move), "attacks": []}
    checks = [t for t in key["threats"] if t.get("kind") == "check"]
    assert "g4g5" in [t["target"] for t in checks]
    assert not any(t["target"] == "f7f8" for t in checks)  # Rxf8+ is the capture threat's
    after = before.copy()
    after.push(move)
    missed = threats.check_own(key, [{"from": "f7", "to": "f8"}], after)
    assert not missed["ok"] and "g4g5" in missed["missed"]
    arrows = [{"from": "f7", "to": "f8"}] + [{"from": t["attackers"][0], "to": t["targets"][0]} for t in checks]
    assert threats.check_own(key, arrows, after)["ok"]
    old = threats.check_own(key, [{"from": "f7", "to": "f8"}], after, with_checks=False)
    assert old["ok"]  # answers from before checks were asked


def test_a_move_is_named_for_the_board():
    fork = chess.Board("r3k3/pp6/8/1N6/8/8/PP6/4K3 w - - 0 1")
    assert threats.motif(fork, chess.Move.from_uci("b5c7")) == "вилка"  # Nc7+: king and rook
    check = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert threats.motif(check, chess.Move.from_uci("a1a8")) == "шах"
    pin = chess.Board("4k3/4n3/8/8/8/8/8/R5K1 w - - 0 1")
    assert threats.motif(pin, chess.Move.from_uci("a1e1")) == "связка"  # the knight on e7 pinned to the king
    to_queen = chess.Board("8/2k5/8/4q3/8/4n3/8/R5K1 w - - 0 1")
    assert threats.motif(to_queen, chess.Move.from_uci("a1e1")) == "связка"  # the knight in front of the queen
    assert threats.motif(chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"), chess.Move.from_uci("a1a8")) == "мат"
