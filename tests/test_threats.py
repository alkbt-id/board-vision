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
    assert answer == {"threats": [], "defended": [], "exchanges": {}}


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
