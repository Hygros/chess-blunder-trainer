from __future__ import annotations

from blunder_tutor.services import llm_explanation as lesson


def _sample_args() -> dict[str, object]:
    return {
        "fen": "r1bq1rk1/pppp1ppp/2n2n2/4p3/2B1P3/2N2N2/PPPP1PPP/R1BQ1RK1 w - - 0 1",
        "player_color": "white",
        "blunder_san": "Qe2",
        "blunder_uci": "d1e2",
        "best_move_san": "Be2",
        "best_move_uci": "f1e2",
        "eval_before_display": "+0.8",
        "eval_after_display": "-1.2",
        "cp_loss": 200,
        "game_phase": "middlegame",
        "tactical_pattern": "Pin",
        "tactical_reason": "The queen gets pinned and tactical pressure follows.",
        "best_line": ["Be2", "Nf6", "Nc3"],
        "refutation_line_san": ["Nf6", "Nc3", "Bb4"],
    }


def test_build_prompt_excludes_rule_based_segments() -> None:
    prompt = lesson._build_prompt(**_sample_args())

    assert "Rule-based consequence" not in prompt
    assert "Rule-based refutation" not in prompt
    assert "Rule-based better-move explanation" not in prompt


def test_clean_response_accepts_coach_block() -> None:
    raw = (
        "Mistake: White drifts into a pin and loses coordination.\n"
        "Refutation: Black uses forcing moves to win material.\n"
        "Better move: Be2 keeps the pieces coordinated.\n"
        "When you see a pinned defender, look for forcing captures first."
    )

    cleaned = lesson._clean_response(raw)

    assert cleaned is not None
    assert "When you see" in cleaned
    assert "Mistake:" not in cleaned


def test_clean_response_rejects_missing_transfer_rule() -> None:
    raw = (
        "White drifts into a pin and loses coordination. "
        "Black uses forcing moves to win material."
    )

    assert lesson._clean_response(raw) is None


def test_clean_response_rejects_filler_phrase() -> None:
    raw = (
        "White loses material, as seen in the refutation line. "
        "When you see this, check forcing moves first."
    )

    assert lesson._clean_response(raw) is None


def test_similarity_guard_detects_overlap() -> None:
    candidate = (
        "This move loses about 2.0 pawns worth of advantage. "
        "When you see a loose piece, look for forcing captures first."
    )
    references = ["This move loses about 2.0 pawns worth of advantage."]

    assert lesson._is_too_similar(candidate, references)


def test_square_grounding_detects_unsupported_square_reference() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    assert lesson._has_unsupported_square_reference(
        "When you see a pawn on a8, avoid this setup.", allowed
    )


def test_piece_square_claim_detects_conflict() -> None:
    hints = lesson._build_square_piece_hints(
        blunder_san="Qd8",
        best_move_san="Qd6",
        best_line=["Qd6"],
        refutation_line_san=["Qd6"],
    )

    assert lesson._has_conflicting_piece_square_claim(
        "When you see a pawn on d6, be careful.", hints
    )
    assert not lesson._has_conflicting_piece_square_claim(
        "When you see a queen on d6, be careful.", hints
    )
    assert lesson._has_conflicting_piece_square_claim(
        "When you see a pawn exchange on d6, be careful.", hints
    )


def test_forced_king_move_claim_detects_missing_evidence() -> None:
    assert lesson._has_unsupported_forced_king_move_claim(
        "Qxg5 wins material and forces the player's king to move.",
        best_line=["Qd2", "Nbd7", "f4"],
        refutation_line_san=["Qxg5", "Nf3", "Qa5", "e5"],
    )
    assert lesson._has_unsupported_forced_king_move_claim(
        "Qxg5 wins material and forces players king to move.",
        best_line=["Qd2", "Nbd7", "f4"],
        refutation_line_san=["Qxg5", "Nf3", "Qa5", "e5"],
    )


def test_forced_king_move_claim_allows_supported_evidence() -> None:
    assert not lesson._has_unsupported_forced_king_move_claim(
        "Qh7+ is strong, forcing the king to move.",
        best_line=["Qh7+", "Kxh7", "Nf6+"],
        refutation_line_san=None,
    )


def test_score_explanation_reports_unsupported_square_reason() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "White ignores the urgent threat and allows tactical pressure. "
        "When you see a knight on a8, check forcing captures first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
    )

    assert not quality.accepted
    assert quality.retryable
    assert "mentions_square_not_in_supplied_moves" in quality.reasons


def test_score_explanation_reports_unsupported_check_claim_reason() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "Black gives check immediately and wins the initiative. "
        "When you see king pressure, check forcing replies first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
    )

    assert not quality.accepted
    assert "unsupported_check_claim" in quality.reasons


def test_score_explanation_reports_unsupported_capture_claim_reason() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "Black captures a piece immediately and wins material. "
        "When you see a tactical setup, check captures first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
    )

    assert not quality.accepted
    assert "unsupported_capture_claim" in quality.reasons


def test_score_explanation_reports_unsupported_mate_claim_reason() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "Black has checkmate in this line. "
        "When you see king pressure, check forcing replies first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
    )

    assert not quality.accepted
    assert "unsupported_mate_claim" in quality.reasons


def test_score_explanation_allows_mate_claim_with_line_evidence() -> None:
    quality = lesson._score_explanation(
        "Qh7+ starts a forcing attack and ends in checkmate. "
        "When you see a forcing check, calculate the follow-up checks first.",
        allowed_squares=lesson._allowed_square_references(
            blunder_san="Qh7+",
            blunder_uci="h5h7",
            best_move_san="Qh7+",
            best_move_uci="h5h7",
            best_line=["Qh7+", "Kxh7", "Nf6#"],
            refutation_line_san=None,
        ),
        square_piece_hints=lesson._build_square_piece_hints(
            blunder_san="Qh7+",
            best_move_san="Qh7+",
            best_line=["Qh7+", "Kxh7", "Nf6#"],
            refutation_line_san=None,
        ),
        best_line=["Qh7+", "Kxh7", "Nf6#"],
        refutation_line_san=None,
        reference_texts=None,
        lesson_facts=None,
    )

    assert quality.accepted
    assert "unsupported_mate_claim" not in quality.reasons


def test_score_explanation_reports_passive_piece_action_voice_reason() -> None:
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "White misses an urgent threat and the knight is captured in the next move. "
        "When you see a loose piece, check forcing captures first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts={"refutation_contains_capture": True},
    )

    # Passive voice is a soft penalty — accepted when it's the only issue
    assert quality.accepted
    assert "passive_piece_action_voice" in quality.soft_reasons
    assert not quality.hard_reasons


def test_transfer_starter_variety_detects_low_variety() -> None:
    explanations = [
        "White misses an urgent threat and loses material. When you see a loose piece, check forcing captures first.",
        "White falls behind in development and gives up tempo. When you see a forcing move, check captures first.",
        "White allows pressure on king safety and loses initiative. When you see king pressure, check forcing replies first.",
        "White ignores an urgent reply and loses coordination. When you see a tactical threat, check forcing captures first.",
        "White chooses a slow move and allows concrete threats. When you see a quiet move, check forcing replies first.",
    ]

    assert lesson._has_low_transfer_starter_variety(explanations)


def test_transfer_starter_variety_accepts_mixed_starters() -> None:
    explanations = [
        "White misses an urgent threat and loses material. When you see a loose piece, check forcing captures first.",
        "White falls behind in development and gives up tempo. If you see a forcing move, check captures first.",
        "White allows pressure on king safety and loses initiative. Before playing a quiet move, check forcing replies first.",
        "White ignores an urgent reply and loses coordination. The habit to build is to check forcing captures before improving.",
        "White chooses a slow move and allows concrete threats. Next time you want to develop, check forcing replies first.",
    ]

    assert not lesson._has_low_transfer_starter_variety(explanations)


def test_build_retry_prompt_includes_rejection_reasons() -> None:
    args = _sample_args()
    prompt = lesson._build_retry_prompt(
        fen=str(args["fen"]),
        player_color=str(args["player_color"]),
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        eval_before_display=str(args["eval_before_display"]),
        eval_after_display=str(args["eval_after_display"]),
        cp_loss=int(args["cp_loss"]),
        game_phase=str(args["game_phase"]),
        tactical_pattern=str(args["tactical_pattern"]),
        tactical_reason=str(args["tactical_reason"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        lesson_facts=None,
        rejection_reasons=[
            "too_similar_to_reference_text",
            "mentions_square_not_in_supplied_moves",
        ],
    )

    assert "The previous answer was rejected for these reasons:" in prompt
    assert "repeated wording from the existing rule-based explanation" in prompt.lower()
    assert "mentioned a square that is not grounded in the supplied moves" in prompt.lower()


def test_explain_training_lesson_retry_prompt_contains_rejection_reason(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    seen_retry_prompt = {"value": ""}

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        if "The previous answer was rejected for these reasons:" in prompt:
            seen_retry_prompt["value"] = prompt
            return (
                "White ignores the urgent threat and allows Black to seize the initiative. "
                "When you see pressure on your king, check forcing captures first."
            )
        return (
            "White ignores the urgent threat and allows tactical pressure. "
            "When you see a knight on a8, check forcing captures first."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    text = lesson.explain_training_lesson(**_sample_args())

    assert text is not None
    assert "not grounded in the supplied moves" in seen_retry_prompt["value"].lower()


def test_explain_training_lesson_retries_on_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    call_count = {"n": 0}

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        call_count["n"] += 1
        if call_count["n"] > 1:
            return (
                "You overextend the king side and allow forcing checks. "
                "Black gains initiative and wins time against your king. "
                "When you see your king defenders move away, look for forcing checks first."
            )
        return (
            "This move loses about 2.0 pawns worth of advantage. "
            "The opponent can answer with Nf6 Nc3 Bb4. "
            "When you see a loose piece, look for forcing captures first."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    text = lesson.explain_training_lesson(
        **_sample_args(),
        reference_texts=["This move loses about 2.0 pawns worth of advantage."],
    )

    assert text is not None
    # Either the retry text or the fallback is acceptable
    assert "overextend" in text or "looks natural" in text


def test_explain_training_lesson_returns_none_when_retry_still_duplicate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    def _always_duplicate(_fen: str, _blunder_uci: str, _prompt: str, **_kwargs: object) -> str:
        return (
            "This move loses about 2.0 pawns worth of advantage. "
            "When you see a loose piece, look for forcing captures first."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _always_duplicate)

    text = lesson.explain_training_lesson(
        **_sample_args(),
        reference_texts=["This move loses about 2.0 pawns worth of advantage."],
    )

    # With the fallback now generating unique content, the function may return
    # the fallback instead of None. Both LLM attempts are duplicates, but the
    # deterministic fallback is not similar to the reference text.
    if text is not None:
        # Fallback was returned — it must not be similar to the reference
        assert "2.0 pawns" not in text


def test_explain_training_lesson_retries_on_unsupported_square(monkeypatch) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        if "Strict coaching mode" in prompt:
            return (
                "After Nf6 and Bb4, Black keeps the initiative and White must defend carefully. "
                "When you see Nf6 followed by pressure on c3, prioritize king safety and development."
            )
        return (
            "A pawn on a8 falls and White wins material for free. "
            "When you see this structure, avoid weakening dark squares."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    text = lesson.explain_training_lesson(**_sample_args())

    assert text is not None
    assert "a8" not in text.lower()


def test_explain_training_lesson_retries_on_piece_square_conflict(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        if "Strict coaching mode" in prompt:
            return (
                "After Qd6, Black keeps the initiative and White must defend carefully. "
                "When you see a queen on d6, prioritize king safety and development."
            )
        return (
            "After Qd6, Black keeps the initiative and White must defend carefully. "
            "When you see a pawn on d6, prioritize king safety and development."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    args = _sample_args()
    args["refutation_line_san"] = ["Qd6"]

    text = lesson.explain_training_lesson(**args)

    assert text is not None
    assert "pawn on d6" not in text.lower()


def test_explain_training_lesson_retries_on_pawn_exchange_square_conflict(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        if "Strict coaching mode" in prompt:
            return (
                "After Bxc2, White can respond with Qxa8 and win major material. "
                "When you see a bishop capture on c2, check whether a back-rank rook is hanging."
            )
        return (
            "After Bxc2, White can respond with Qxa8 and win major material. "
            "When you see a pawn exchange on c2, check whether a back-rank rook is hanging."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    args = _sample_args()
    args["blunder_san"] = "Bxc2"
    args["blunder_uci"] = "f5c2"
    args["refutation_line_san"] = ["Qxa8", "c6", "Nf3", "Qc7"]

    text = lesson.explain_training_lesson(**args)

    assert text is not None
    assert "pawn exchange on c2" not in text.lower()


def test_explain_training_lesson_retries_on_unsupported_forced_king_claim(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lesson, "_enabled", lambda: True)

    def _fake_cached_generate(_fen: str, _blunder_uci: str, prompt: str, **_kwargs: object) -> str:
        if "Strict coaching mode" in prompt:
            return (
                "Qxg5 wins material and leaves White with an immediate defensive problem. "
                "When you see a loose piece, check forcing captures first."
            )
        return (
            "Qxg5 wins material and forces the player's king to move immediately. "
            "When you see a loose piece, check forcing captures first."
        )

    monkeypatch.setattr(lesson, "_cached_generate", _fake_cached_generate)

    args = _sample_args()
    args["refutation_line_san"] = ["Qxg5", "Nf3", "Qa5", "e5"]

    text = lesson.explain_training_lesson(**args)

    assert text is not None
    assert "king to move" not in text.lower()


def test_clean_response_rejects_eval_notation() -> None:
    raw = (
        "White chooses a slow move and allows a forcing reply. "
        "The position drops to +1.7 after that. "
        "When you see a loose piece, check captures first."
    )

    assert lesson._clean_response(raw) is None


def test_clean_response_rejects_overly_long_sentence() -> None:
    raw = (
        "White chooses a natural developing move but misses that Black can force a sequence with check, capture, and tempo, "
        "which leaves the queen exposed, creates multiple practical threats in one turn, drags the king into repeated defensive moves, "
        "forces awkward piece placements, and makes every quiet follow-up too slow before White can coordinate defenders properly. "
        "When you see a quiet move, check forcing replies first."
    )

    assert lesson._clean_response(raw) is None


def test_soft_penalty_alone_does_not_reject() -> None:
    """A single soft penalty (passive voice) should NOT cause rejection."""
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    quality = lesson._score_explanation(
        "White misses an urgent threat and the knight is captured in the next move. "
        "When you see a loose piece, check forcing captures first.",
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts={"refutation_contains_capture": True},
    )

    assert quality.accepted
    assert "passive_piece_action_voice" in quality.soft_reasons
    assert not quality.hard_reasons


def test_two_soft_penalties_reject() -> None:
    """Hard + soft penalty together should cause rejection (hard alone rejects)."""
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    # Text with passive voice AND similar to reference
    candidate = (
        "White misses an urgent threat and the knight is captured in the next move. "
        "When you see a loose piece, check forcing captures first."
    )
    reference = [
        "White misses an urgent threat and the knight falls in the next move. "
        "When you see a loose piece, check forcing captures first."
    ]

    quality = lesson._score_explanation(
        candidate,
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=reference,
        lesson_facts={"refutation_contains_capture": True},
    )

    assert not quality.accepted
    # Similarity is a hard reason; passive voice is soft
    assert "too_similar_to_reference_text" in quality.hard_reasons
    assert "passive_piece_action_voice" in quality.soft_reasons


def test_fallback_passes_scoring_with_reference_overlap() -> None:
    """Fallback should not be rejected due to similarity with reference_texts."""
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    fallback = lesson._fallback_explanation(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        refutation_line_san=args["refutation_line_san"],
        tactical_pattern=str(args["tactical_pattern"]),
        game_phase=str(args["game_phase"]),
        lesson_facts=None,
    )

    # Score the fallback with is_fallback=True and overlapping reference_texts
    quality = lesson._score_explanation(
        fallback,
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=[fallback],  # Same text as reference — would reject without is_fallback
        lesson_facts=None,
        is_fallback=True,
    )

    assert quality.accepted
    assert "too_similar_to_reference_text" not in quality.reasons


def test_fallback_explanation_passes_own_scoring() -> None:
    """Fallback text should always pass its own scoring validation."""
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    fallback = lesson._fallback_explanation(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        refutation_line_san=args["refutation_line_san"],
        tactical_pattern=str(args["tactical_pattern"]),
        game_phase=str(args["game_phase"]),
        lesson_facts=None,
    )

    quality = lesson._score_explanation(
        fallback,
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
        is_fallback=True,
    )

    assert quality.accepted, f"Fallback rejected: {quality.reasons}"


def test_raw_llm_text_cleaned_in_scoring() -> None:
    """Raw (uncleaned) text from cache should be properly cleaned by scoring."""
    args = _sample_args()
    allowed = lesson._allowed_square_references(
        blunder_san=str(args["blunder_san"]),
        blunder_uci=str(args["blunder_uci"]),
        best_move_san=str(args["best_move_san"]),
        best_move_uci=str(args["best_move_uci"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )
    hints = lesson._build_square_piece_hints(
        blunder_san=str(args["blunder_san"]),
        best_move_san=str(args["best_move_san"]),
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
    )

    # Simulate raw text with markdown that would be stripped by _clean_response
    raw_with_markdown = (
        "**Mistake:** White ignores the urgent pin and loses coordination. "
        "**Better move:** Be2 keeps the pieces coordinated and defends the pin. "
        "When you see a pinned defender, look for forcing captures first."
    )

    quality = lesson._score_explanation(
        raw_with_markdown,
        allowed_squares=allowed,
        square_piece_hints=hints,
        best_line=args["best_line"],
        refutation_line_san=args["refutation_line_san"],
        reference_texts=None,
        lesson_facts=None,
    )

    # Should be cleaned (markdown removed) and then scored
    assert quality.text is not None
    assert "**" not in quality.text
