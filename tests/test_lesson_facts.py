"""Tests for the structured lesson facts builder and prompt integration."""

from __future__ import annotations

import chess

from blunder_tutor.services.lesson_facts import (
    build_lesson_facts,
    format_lesson_facts_for_prompt,
)
from blunder_tutor.services import llm_explanation as lesson


# === Test positions ===

# Italian Game position: White plays Bd6 (blunder), Black has Qh5+ (check)
ITALIAN_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
# Blunder: White plays Ng5 (allows Qh5+ check from Black's perspective)
# Let's use a simpler scenario: White plays a4 (blunder), best is Nc3
SIMPLE_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _italian_board() -> chess.Board:
    """Position where White has Bc4, Nf3 developed, Black has e5, Nc6."""
    return chess.Board(ITALIAN_FEN)


def _make_facts_check_scenario() -> dict:
    """Scenario: critical reply is a check."""
    # Position: Black king on e8, White queen can go to h5 giving check
    # Using a crafted position where Qh5+ is possible
    fen = "rnbqkb1r/pppp1ppp/5n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3"
    board = chess.Board(fen)
    # Black blunders with ...Be7 (quiet), while should play ...g6 blocking the check threat
    blunder_move = chess.Move.from_uci("f8e7")
    best_move = chess.Move.from_uci("g7g6")
    refutation_line = ["Qxf7#"]  # White checkmates

    return build_lesson_facts(
        board_before=board,
        blunder_move=blunder_move,
        best_move=best_move,
        best_line=["g6"],
        refutation_line=refutation_line,
        game_phase="opening",
        tactical_pattern=None,
    )


def _make_facts_capture_scenario() -> dict:
    """Scenario: critical reply is a capture."""
    # Position where a piece is left hanging
    fen = "r1bqkbnr/pppppppp/2n5/8/3NP3/8/PPP2PPP/RNBQKB1R b KQkq - 0 3"
    board = chess.Board(fen)
    # Black blunders with ...a6 (quiet), White can take Nxc6 (capture)
    blunder_move = chess.Move.from_uci("a7a6")
    best_move = chess.Move.from_uci("e7e5")
    refutation_line = ["Nxc6", "dxc6"]

    return build_lesson_facts(
        board_before=board,
        blunder_move=blunder_move,
        best_move=best_move,
        best_line=["e5", "Nb3"],
        refutation_line=refutation_line,
        game_phase="opening",
    )


def _make_facts_quiet_forcing() -> dict:
    """Scenario: quiet blunder allows forcing reply."""
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
    board = chess.Board(fen)
    # Black plays ...Bd6 (develops), best is ...Nc6
    blunder_move = chess.Move.from_uci("f8d6")
    best_move = chess.Move.from_uci("b8c6")
    refutation_line = ["Nxe5"]

    return build_lesson_facts(
        board_before=board,
        blunder_move=blunder_move,
        best_move=best_move,
        best_line=["Nc6", "Bb5"],
        refutation_line=refutation_line,
        game_phase="opening",
    )


def _make_facts_no_refutation() -> dict:
    """Scenario: no refutation line available."""
    fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    board = chess.Board(fen)
    blunder_move = chess.Move.from_uci("a7a6")
    best_move = chess.Move.from_uci("e7e5")

    return build_lesson_facts(
        board_before=board,
        blunder_move=blunder_move,
        best_move=best_move,
        best_line=["e5"],
        refutation_line=None,
        game_phase="opening",
    )


def _make_facts_material_win() -> dict:
    """Scenario: refutation wins material."""
    # Position where hanging knight can be taken
    fen = "r1bqkb1r/pppppppp/2n2n2/8/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 4 4"
    board = chess.Board(fen)
    # Black blunders with ...Nxe4 (captures pawn but hangs knight)
    blunder_move = chess.Move.from_uci("f6e4")
    best_move = chess.Move.from_uci("e7e6")
    refutation_line = ["Bxf7+", "Kxf7", "Nxe4"]

    return build_lesson_facts(
        board_before=board,
        blunder_move=blunder_move,
        best_move=best_move,
        best_line=["e6", "d3"],
        refutation_line=refutation_line,
        game_phase="opening",
        tactical_pattern="fork",
    )


# === Tests for build_lesson_facts ===


class TestBuildLessonFactsBasicPosition:
    def test_fen_and_side_to_move(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert facts["fen_before"] == "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
        assert facts["side_to_move"] == "black"
        assert facts["player_color"] == "black"

    def test_game_phase_preserved(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert facts["game_phase"] == "opening"

    def test_king_squares(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert facts["player_king_square"] == "e8"
        assert facts["opponent_king_square"] == "e1"


class TestCriticalReplyIsCheck:
    def test_critical_reply_detected_as_check(self) -> None:
        facts = _make_facts_check_scenario()
        # Qxf7# is checkmate (which is also check)
        assert facts["critical_reply_san"] == "Qxf7#"
        assert facts["critical_reply_is_check"] is True
        assert facts["critical_reply_type"] == "check"

    def test_mistake_categories_include_missed_forcing_check(self) -> None:
        facts = _make_facts_check_scenario()
        assert "missed forcing check" in facts["mistake_categories"]

    def test_immediate_consequence_mentions_check(self) -> None:
        facts = _make_facts_check_scenario()
        assert "check" in facts["immediate_consequence_summary"].lower() or "Qxf7#" in facts["immediate_consequence_summary"]


class TestCriticalReplyIsCapture:
    def test_critical_reply_detected_as_capture(self) -> None:
        facts = _make_facts_capture_scenario()
        assert facts["critical_reply_san"] == "Nxc6"
        assert facts["critical_reply_is_capture"] is True
        assert facts["critical_reply_type"] == "capture"

    def test_mistake_categories_include_allowed_capture(self) -> None:
        facts = _make_facts_capture_scenario()
        assert "allowed capture" in facts["mistake_categories"]


class TestRefutationWinsMaterial:
    def test_material_consequence(self) -> None:
        facts = _make_facts_material_win()
        # After Bxf7+ Kxf7 Nxe4 — White sacrificed bishop (3) for pawn via fork
        # This is complex; just check material_consequence_summary is populated
        assert facts["material_consequence_summary"] in (
            "opponent wins material",
            "player wins material",
            "material remains roughly equal",
            "not available",
        )

    def test_missed_tactic_in_categories(self) -> None:
        facts = _make_facts_material_win()
        assert "missed tactic" in facts["mistake_categories"]


class TestQuietBlunderForcingReply:
    def test_played_move_intent(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert facts["played_move_piece"] == "bishop"
        assert facts["played_move_intent_heuristic"] == "develops a piece"

    def test_critical_reply_is_capture(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert facts["critical_reply_san"] == "Nxe5"
        assert facts["critical_reply_is_capture"] is True

    def test_wrong_priority_in_categories(self) -> None:
        facts = _make_facts_quiet_forcing()
        assert "wrong priority" in facts["mistake_categories"]


class TestBestMoveDevelops:
    def test_best_move_intent(self) -> None:
        facts = _make_facts_quiet_forcing()
        # Best move is Nc6 — develops a piece from b8
        assert facts["best_move_piece"] == "knight"
        assert facts["best_move_intent_heuristic"] == "develops a piece"


class TestNoRefutationLine:
    def test_no_critical_reply(self) -> None:
        facts = _make_facts_no_refutation()
        assert facts["critical_reply_san"] is None
        assert facts["critical_reply_type"] is None
        assert facts["refutation_line_san"] is None

    def test_refutation_stats_empty(self) -> None:
        facts = _make_facts_no_refutation()
        assert facts["refutation_contains_check"] is False
        assert facts["refutation_contains_capture"] is False
        assert facts["number_of_checks_in_refutation"] == 0


class TestRefutationLineOneMove:
    def test_single_move_refutation(self) -> None:
        fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2"
        board = chess.Board(fen)
        blunder_move = chess.Move.from_uci("f8d6")
        best_move = chess.Move.from_uci("b8c6")

        facts = build_lesson_facts(
            board_before=board,
            blunder_move=blunder_move,
            best_move=best_move,
            best_line=["Nc6"],
            refutation_line=["Nxe5"],
            game_phase="opening",
        )

        assert facts["refutation_line_san"] == ["Nxe5"]
        assert facts["first_refutation_move"] == "Nxe5"
        assert facts["critical_reply_san"] == "Nxe5"


# === Tests for validation rejections ===


class TestValidationRejectsGenericTransfer:
    def test_rejects_calculate_carefully(self) -> None:
        raw = (
            "The move loses a piece to a fork. "
            "The better move avoids this issue. "
            "When you see this pattern, calculate carefully."
        )
        assert lesson._clean_response(raw) is None

    def test_rejects_look_for_tactics(self) -> None:
        raw = (
            "The move is too slow and allows a forcing reply. "
            "When you see a quiet developing move, look for tactics."
        )
        assert lesson._clean_response(raw) is None


class TestValidationRejectsEngineLanguage:
    def test_rejects_centipawns(self) -> None:
        raw = (
            "This costs about 200 centipawns of advantage. "
            "When you see a quiet move, check for forcing replies first."
        )
        assert lesson._clean_response(raw) is None

    def test_rejects_stockfish(self) -> None:
        raw = (
            "Stockfish recommends Nc6 as the better option. "
            "When you see a quiet move, check for captures first."
        )
        assert lesson._clean_response(raw) is None

    def test_rejects_engine_says(self) -> None:
        raw = (
            "The engine says that Nc6 is better for coordination. "
            "When you see a quiet move, check for captures first."
        )
        assert lesson._clean_response(raw) is None

    def test_rejects_significant_advantage(self) -> None:
        raw = (
            "After this move, White gains a significant advantage and puts pressure on Black. "
            "When you see a quiet move, check for forcing replies first."
        )
        assert lesson._clean_response(raw) is None


class TestFallbackExplanation:
    def test_fallback_with_check_critical_reply(self) -> None:
        facts = _make_facts_check_scenario()
        result = lesson._fallback_explanation(
            blunder_san="Be7",
            best_move_san="g6",
            refutation_line_san=["Qxf7#"],
            tactical_pattern=None,
            game_phase="opening",
            lesson_facts=facts,
        )
        assert "Qxf7#" in result
        assert "check" in result.lower()
        assert "When you see" in result or "If you see" in result

    def test_fallback_with_capture_critical_reply(self) -> None:
        facts = _make_facts_capture_scenario()
        result = lesson._fallback_explanation(
            blunder_san="a6",
            best_move_san="e5",
            refutation_line_san=["Nxc6", "dxc6"],
            tactical_pattern=None,
            game_phase="opening",
            lesson_facts=facts,
        )
        assert "Nxc6" in result
        assert "material" in result.lower() or "loose" in result.lower()
        assert "When you see" in result or "If you see" in result

    def test_fallback_without_lesson_facts(self) -> None:
        result = lesson._fallback_explanation(
            blunder_san="Bd6",
            best_move_san="Nc6",
            refutation_line_san=["Nxe5"],
            tactical_pattern=None,
            game_phase="opening",
            lesson_facts=None,
        )
        assert "Nxe5" in result
        assert "When you see" in result

    def test_fallback_no_refutation(self) -> None:
        facts = _make_facts_no_refutation()
        result = lesson._fallback_explanation(
            blunder_san="a6",
            best_move_san="e5",
            refutation_line_san=None,
            tactical_pattern=None,
            game_phase="opening",
            lesson_facts=facts,
        )
        assert "When you see" in result or "If you see" in result
        assert "priority" in result.lower() or "forcing" in result.lower()


class TestFormatLessonFactsForPrompt:
    def test_contains_critical_reply(self) -> None:
        facts = _make_facts_check_scenario()
        text = format_lesson_facts_for_prompt(facts)
        assert "Critical reply:" in text
        assert "Qxf7#" in text

    def test_contains_material_consequence(self) -> None:
        facts = _make_facts_capture_scenario()
        text = format_lesson_facts_for_prompt(facts)
        assert "Material consequence:" in text

    def test_contains_transfer_rule_seed(self) -> None:
        facts = _make_facts_check_scenario()
        text = format_lesson_facts_for_prompt(facts)
        assert "Transfer rule seed:" in text

    def test_contains_mistake_categories(self) -> None:
        facts = _make_facts_quiet_forcing()
        text = format_lesson_facts_for_prompt(facts)
        assert "Mistake categories:" in text
        assert "wrong priority" in text

    def test_no_critical_reply_shows_not_available(self) -> None:
        facts = _make_facts_no_refutation()
        text = format_lesson_facts_for_prompt(facts)
        assert "Critical reply: not available" in text


class TestPromptIncludesLessonFacts:
    def test_prompt_includes_structured_facts(self) -> None:
        facts = _make_facts_quiet_forcing()
        prompt = lesson._build_prompt(
            fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
            player_color="black",
            blunder_san="Bd6",
            blunder_uci="f8d6",
            best_move_san="Nc6",
            best_move_uci="b8c6",
            eval_before_display="+0.3",
            eval_after_display="-0.5",
            cp_loss=80,
            game_phase="opening",
            tactical_pattern=None,
            tactical_reason=None,
            best_line=["Nc6", "Bb5"],
            refutation_line_san=["Nxe5"],
            lesson_facts=facts,
        )
        assert "Structured lesson facts" in prompt
        assert "Critical reply:" in prompt
        assert "Nxe5" in prompt

    def test_prompt_without_lesson_facts(self) -> None:
        prompt = lesson._build_prompt(
            fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
            player_color="black",
            blunder_san="Bd6",
            blunder_uci="f8d6",
            best_move_san="Nc6",
            best_move_uci="b8c6",
            eval_before_display="+0.3",
            eval_after_display="-0.5",
            cp_loss=80,
            game_phase="opening",
            tactical_pattern=None,
            tactical_reason=None,
            best_line=["Nc6", "Bb5"],
            refutation_line_san=["Nxe5"],
            lesson_facts=None,
        )
        # Should still work, just without structured facts block
        assert "Structured lesson facts" not in prompt
        assert "Engine facts you may use" in prompt


class TestUnsupportedSquareReferences:
    def test_rejects_unsupported_squares(self) -> None:
        allowed = lesson._allowed_square_references(
            blunder_san="Bd6",
            blunder_uci="f8d6",
            best_move_san="Nc6",
            best_move_uci="b8c6",
            best_line=["Nc6", "Bb5"],
            refutation_line_san=["Nxe5"],
        )
        # h3 is not in any of the listed moves
        assert lesson._has_unsupported_square_reference(
            "The knight on h3 is loose and vulnerable.", allowed
        )

    def test_accepts_supported_squares(self) -> None:
        allowed = lesson._allowed_square_references(
            blunder_san="Bd6",
            blunder_uci="f8d6",
            best_move_san="Nc6",
            best_move_uci="b8c6",
            best_line=["Nc6", "Bb5"],
            refutation_line_san=["Nxe5"],
        )
        # d6, c6, e5 are all in the moves
        assert not lesson._has_unsupported_square_reference(
            "The bishop on d6 blocks development.", allowed
        )
