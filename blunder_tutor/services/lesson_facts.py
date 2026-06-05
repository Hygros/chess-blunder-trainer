"""Compute structured lesson facts from chess positions and moves.

This module produces a dictionary of concrete, pre-computed chess facts
that the LLM prompt can reference directly.  The LLM should not be expected
to infer chess logic from raw FEN — it receives these facts as ground truth.
"""

from __future__ import annotations

from typing import Any

import chess

# Standard piece values (pawns).
PIECE_VALUES: dict[int, int] = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

PIECE_NAMES: dict[int, str] = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}


def _material_balance(board: chess.Board, perspective: chess.Color) -> int:
    """Material balance in pawns from *perspective* side's point of view."""
    balance = 0
    for piece_type in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        balance += len(board.pieces(piece_type, perspective)) * PIECE_VALUES[piece_type]
        balance -= len(board.pieces(piece_type, not perspective)) * PIECE_VALUES[piece_type]
    return balance


def _piece_name_at(board: chess.Board, square: chess.Square) -> str:
    piece = board.piece_at(square)
    if piece is None:
        return "unknown"
    return PIECE_NAMES.get(piece.piece_type, "unknown")


def _move_piece_name(board: chess.Board, move: chess.Move) -> str:
    return _piece_name_at(board, move.from_square)


def _is_castling(board: chess.Board, move: chess.Move) -> bool:
    return board.is_castling(move)


def _gives_check(board: chess.Board, move: chess.Move) -> bool:
    board.push(move)
    result = board.is_check()
    board.pop()
    return result


def _intent_heuristic(board: chess.Board, move: chess.Move) -> str:
    """Conservative heuristic for the intent of a move."""
    if board.is_castling(move):
        return "castles"
    if board.is_capture(move):
        return "captures material"
    piece = board.piece_at(move.from_square)
    if piece is None:
        return "unknown"
    if piece.piece_type == chess.PAWN:
        dest_file = chess.square_file(move.to_square)
        if dest_file in (3, 4):  # d or e file
            return "moves a central pawn"
        return "unknown"
    # Non-pawn, non-capture, non-castle: heuristic for development
    rank = chess.square_rank(move.from_square)
    home_rank = 0 if piece.color == chess.WHITE else 7
    if rank == home_rank:
        return "develops a piece"
    return "unknown"


def _critical_reply_type(board: chess.Board, move: chess.Move) -> str:
    """Classify the critical reply type: check > capture > promotion > unknown."""
    if _gives_check(board, move):
        return "check"
    if board.is_capture(move):
        return "capture"
    if move.promotion is not None:
        return "promotion"
    return "unknown"


def _castled_status(board: chess.Board, color: chess.Color) -> str:
    king_sq = board.king(color)
    if king_sq is None:
        return "unknown"
    rank = chess.square_rank(king_sq)
    file = chess.square_file(king_sq)
    expected_rank = 0 if color == chess.WHITE else 7
    if rank == expected_rank and file in (6, 2):  # g1/c1 or g8/c8
        return "player king appears castled"
    if rank == expected_rank and file == 4:  # e1/e8
        return "player king appears uncastled"
    return "unknown"


def _attacked_pieces_by_move(board: chess.Board, move: chess.Move) -> list[str]:
    """Return list of SAN squares of pieces attacked by the moved piece after the move."""
    board.push(move)
    attacked: list[str] = []
    attacker_color = not board.turn  # The side that just moved
    victim_color = board.turn
    attacks = board.attacks(move.to_square)
    for sq in attacks:
        piece = board.piece_at(sq)
        if piece is not None and piece.color == victim_color:
            attacked.append(f"{PIECE_NAMES.get(piece.piece_type, 'piece')} on {chess.square_name(sq)}")
    board.pop()
    return attacked


def _undefended_pieces(board: chess.Board, color: chess.Color) -> list[str]:
    """Return list of piece descriptions that are undefended for given color."""
    undefended: list[str] = []
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece is None or piece.color != color or piece.piece_type == chess.KING:
            continue
        if not board.is_attacked_by(color, sq):
            undefended.append(f"{PIECE_NAMES.get(piece.piece_type, 'piece')} on {chess.square_name(sq)}")
    return undefended


def _play_line(board: chess.Board, moves_san: list[str]) -> tuple[chess.Board, list[chess.Move]]:
    """Play a line of SAN moves on a copy of the board, returning final board and parsed moves."""
    b = board.copy()
    parsed: list[chess.Move] = []
    for san in moves_san:
        try:
            move = b.parse_san(san)
            parsed.append(move)
            b.push(move)
        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            break
    return b, parsed


def _line_stats(board: chess.Board, moves_san: list[str]) -> dict[str, Any]:
    """Compute statistics for a line of moves."""
    stats: dict[str, Any] = {
        "contains_check": False,
        "contains_capture": False,
        "contains_promotion": False,
        "number_of_checks": 0,
        "number_of_captures": 0,
        "captured_pieces": [],
        "king_was_forced_to_move": False,
    }
    b = board.copy()
    for san in moves_san:
        try:
            move = b.parse_san(san)
        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            break

        if b.is_capture(move):
            stats["contains_capture"] = True
            stats["number_of_captures"] += 1
            captured = b.piece_at(move.to_square)
            if captured:
                stats["captured_pieces"].append(PIECE_NAMES.get(captured.piece_type, "piece"))
        if move.promotion is not None:
            stats["contains_promotion"] = True

        piece = b.piece_at(move.from_square)
        if piece and piece.piece_type == chess.KING:
            stats["king_was_forced_to_move"] = True

        b.push(move)

        if b.is_check():
            stats["contains_check"] = True
            stats["number_of_checks"] += 1

    return stats


def _best_move_purpose_candidates(
    board_before: chess.Board,
    best_move: chess.Move,
    refutation_line_san: list[str] | None,
    critical_reply_type: str | None,
) -> list[str]:
    """Generate conservative purpose candidates for the best move."""
    purposes: list[str] = []

    if critical_reply_type in ("check", "capture", "promotion"):
        purposes.append("addresses an immediate forcing reply")

    if _gives_check(board_before, best_move):
        purposes.append("gives check")

    piece = board_before.piece_at(best_move.from_square)
    if piece and piece.piece_type != chess.PAWN:
        rank = chess.square_rank(best_move.from_square)
        home_rank = 0 if piece.color == chess.WHITE else 7
        if rank == home_rank:
            purposes.append("develops a piece")

    if board_before.is_castling(best_move):
        purposes.append("improves king safety")

    if board_before.is_capture(best_move):
        purposes.append("avoids material loss")

    # Check if best move defends a piece that the critical reply would capture
    if refutation_line_san:
        board_after_blunder = board_before.copy()
        # We can't replay the blunder here (we don't have it), but we check
        # if the best move moves a piece to defend something
        pass

    if not purposes:
        purposes.append("prevents or reduces opponent initiative")

    return purposes


def _mistake_categories(
    *,
    critical_reply_is_check: bool,
    critical_reply_is_capture: bool,
    refutation_wins_material: bool,
    played_is_quiet_developing: bool,
    critical_reply_is_forcing: bool,
    game_phase: str | None,
    best_move_develops_or_castles: bool,
    tactical_pattern: str | None,
) -> list[str]:
    """Generate conservative mistake categories from computed facts."""
    categories: list[str] = []

    if critical_reply_is_check:
        categories.append("missed forcing check")
    if critical_reply_is_capture or refutation_wins_material:
        if critical_reply_is_capture:
            categories.append("allowed capture")
        categories.append("allowed material loss")
    if played_is_quiet_developing and critical_reply_is_forcing:
        categories.append("wrong priority")
    if game_phase and game_phase.lower() == "opening" and best_move_develops_or_castles and critical_reply_is_forcing:
        categories.append("opening development")
    if tactical_pattern and tactical_pattern.lower() != "none":
        categories.append("missed tactic")
    if not categories:
        categories.append("wrong priority")

    return categories


def _immediate_consequence_summary(
    *,
    played_move_san: str,
    critical_reply_san: str | None,
    critical_reply_type_val: str | None,
    refutation_wins_material: bool,
    best_move_san: str | None,
    king_forced_to_move: bool,
) -> str:
    """Create a short factual summary of the immediate consequence."""
    if critical_reply_san and critical_reply_type_val == "check":
        return f"The blunder allows the opponent's immediate check {critical_reply_san}."
    if critical_reply_san and critical_reply_type_val == "capture":
        return f"The refutation starts with a capture ({critical_reply_san}) that wins material."
    if king_forced_to_move:
        return "The refutation line forces the player's king to move before development is complete."
    if critical_reply_san and refutation_wins_material:
        return f"The played move is quiet, while the opponent has a forcing reply {critical_reply_san}."
    if critical_reply_san:
        return f"The played move is quiet, while the opponent has a forcing reply {critical_reply_san}."
    return f"{played_move_san} is a priority error because it does not solve the most urgent problem."


def _transfer_rule_seed(
    *,
    critical_reply_type_val: str | None,
    categories: list[str],
    played_intent: str,
) -> str:
    """Generate one concrete transfer rule seed."""
    if critical_reply_type_val == "check":
        return "When the opponent has a possible check, solve that problem before making a general improving move."
    if critical_reply_type_val == "capture":
        return "If a piece move leaves material loose, check whether the opponent can win it with tempo."
    if "opening development" in categories:
        return "Before playing a quiet developing move, check the opponent's forcing replies: checks, captures, and threats."
    if played_intent == "castles":
        return "Before castling, check whether the castled king can be attacked immediately by a forcing move."
    if "missed tactic" in categories:
        return "Before playing a natural move, check whether a forcing sequence (check, capture, threat) wins material or creates a decisive threat."
    return "Before playing a quiet developing move, check the opponent's forcing replies: checks, captures, and threats."


def _material_consequence_summary(
    balance_before: int,
    balance_after_blunder: int,
    balance_after_refutation: int | None,
) -> str:
    """Summarize material consequence from the blundering player's perspective."""
    if balance_after_refutation is not None:
        delta = balance_after_refutation - balance_before
    else:
        delta = balance_after_blunder - balance_before

    if delta <= -2:
        return "opponent wins material"
    if delta >= 2:
        return "player wins material"
    if abs(delta) <= 1:
        return "material remains roughly equal"
    return "not available"


def build_lesson_facts(
    board_before: chess.Board,
    blunder_move: chess.Move,
    best_move: chess.Move | None,
    best_line: list[str] | None,
    refutation_line: list[str] | None,
    evals: dict[str, Any] | None = None,
    game_phase: str | None = None,
    tactical_pattern: str | None = None,
    tactical_reason: str | None = None,
) -> dict[str, Any]:
    """Compute structured lesson facts for the LLM prompt.

    Parameters
    ----------
    board_before : chess.Board
        Position before the blunder.
    blunder_move : chess.Move
        The blunder move (UCI).
    best_move : chess.Move | None
        The engine's preferred move.
    best_line : list[str] | None
        Best continuation in SAN (from the best move onward).
    refutation_line : list[str] | None
        Opponent refutation line in SAN (after the blunder).
    evals : dict | None
        Optional eval data (eval_before, eval_after, cp_loss).
    game_phase : str | None
        "opening", "middlegame", or "endgame".
    tactical_pattern : str | None
        Classified tactical motif.
    tactical_reason : str | None
        Reason for the tactical classification.

    Returns
    -------
    dict
        Structured lesson facts.
    """
    facts: dict[str, Any] = {}
    player_color = board_before.turn

    # === 1. Basic position facts ===
    facts["fen_before"] = board_before.fen()
    facts["side_to_move"] = "white" if player_color == chess.WHITE else "black"
    facts["player_color"] = facts["side_to_move"]
    facts["game_phase"] = game_phase or "unknown"
    facts["move_number"] = board_before.fullmove_number

    player_king_sq = board_before.king(player_color)
    opponent_king_sq = board_before.king(not player_color)
    facts["player_king_square"] = chess.square_name(player_king_sq) if player_king_sq is not None else "unknown"
    facts["opponent_king_square"] = chess.square_name(opponent_king_sq) if opponent_king_sq is not None else "unknown"

    # === 2. Played move facts ===
    blunder_san = board_before.san(blunder_move)
    facts["played_move_san"] = blunder_san
    facts["played_move_uci"] = blunder_move.uci()
    facts["played_move_piece"] = _move_piece_name(board_before, blunder_move)
    facts["played_move_is_capture"] = board_before.is_capture(blunder_move)
    facts["played_move_is_castling"] = board_before.is_castling(blunder_move)
    facts["played_move_is_promotion"] = blunder_move.promotion is not None
    facts["played_move_gives_check"] = _gives_check(board_before, blunder_move)
    facts["played_move_intent_heuristic"] = _intent_heuristic(board_before, blunder_move)

    # === 3. Best move facts ===
    if best_move:
        facts["best_move_san"] = board_before.san(best_move)
        facts["best_move_uci"] = best_move.uci()
        facts["best_move_piece"] = _move_piece_name(board_before, best_move)
        facts["best_move_is_capture"] = board_before.is_capture(best_move)
        facts["best_move_is_castling"] = board_before.is_castling(best_move)
        facts["best_move_gives_check"] = _gives_check(board_before, best_move)
        facts["best_move_intent_heuristic"] = _intent_heuristic(board_before, best_move)
    else:
        facts["best_move_san"] = None
        facts["best_move_uci"] = None
        facts["best_move_piece"] = None
        facts["best_move_is_capture"] = None
        facts["best_move_is_castling"] = None
        facts["best_move_gives_check"] = None
        facts["best_move_intent_heuristic"] = None

    # === 4. Critical opponent reply after blunder ===
    board_after_blunder = board_before.copy()
    board_after_blunder.push(blunder_move)

    critical_reply_san: str | None = None
    critical_reply_move: chess.Move | None = None
    critical_reply_type_val: str | None = None

    if refutation_line and len(refutation_line) > 0:
        first_refutation_san = refutation_line[0]
        try:
            critical_reply_move = board_after_blunder.parse_san(first_refutation_san)
            critical_reply_san = first_refutation_san
            critical_reply_type_val = _critical_reply_type(board_after_blunder, critical_reply_move)
        except (chess.InvalidMoveError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            pass

    if critical_reply_move:
        facts["critical_reply_san"] = critical_reply_san
        facts["critical_reply_uci"] = critical_reply_move.uci()
        facts["critical_reply_piece"] = _move_piece_name(board_after_blunder, critical_reply_move)
        facts["critical_reply_is_check"] = _gives_check(board_after_blunder, critical_reply_move)
        facts["critical_reply_is_capture"] = board_after_blunder.is_capture(critical_reply_move)
        facts["critical_reply_captured_piece"] = (
            _piece_name_at(board_after_blunder, critical_reply_move.to_square)
            if board_after_blunder.is_capture(critical_reply_move)
            else None
        )
        facts["critical_reply_is_promotion"] = critical_reply_move.promotion is not None
        facts["critical_reply_type"] = critical_reply_type_val
        facts["pieces_attacked_by_critical_reply"] = _attacked_pieces_by_move(
            board_after_blunder, critical_reply_move
        )
    else:
        facts["critical_reply_san"] = None
        facts["critical_reply_uci"] = None
        facts["critical_reply_piece"] = None
        facts["critical_reply_is_check"] = False
        facts["critical_reply_is_capture"] = False
        facts["critical_reply_captured_piece"] = None
        facts["critical_reply_is_promotion"] = False
        facts["critical_reply_type"] = None
        facts["pieces_attacked_by_critical_reply"] = []

    # === 5. Refutation line facts ===
    if refutation_line:
        ref_stats = _line_stats(board_after_blunder, refutation_line)
        facts["refutation_line_san"] = refutation_line
        facts["first_refutation_move"] = refutation_line[0] if refutation_line else None
        facts["refutation_contains_check"] = ref_stats["contains_check"]
        facts["refutation_contains_capture"] = ref_stats["contains_capture"]
        facts["refutation_contains_promotion"] = ref_stats["contains_promotion"]
        facts["captured_pieces_in_refutation"] = ref_stats["captured_pieces"]
        facts["king_was_forced_to_move"] = ref_stats["king_was_forced_to_move"]
        facts["number_of_checks_in_refutation"] = ref_stats["number_of_checks"]
        facts["number_of_captures_in_refutation"] = ref_stats["number_of_captures"]
    else:
        facts["refutation_line_san"] = None
        facts["first_refutation_move"] = None
        facts["refutation_contains_check"] = False
        facts["refutation_contains_capture"] = False
        facts["refutation_contains_promotion"] = False
        facts["captured_pieces_in_refutation"] = []
        facts["king_was_forced_to_move"] = False
        facts["number_of_checks_in_refutation"] = 0
        facts["number_of_captures_in_refutation"] = 0

    # === 6. Best line facts ===
    if best_line:
        best_stats = _line_stats(board_before, best_line)
        facts["best_line_san"] = best_line
        facts["best_line_first_move"] = best_line[0] if best_line else None
        facts["best_line_contains_check"] = best_stats["contains_check"]
        facts["best_line_contains_capture"] = best_stats["contains_capture"]
    else:
        facts["best_line_san"] = None
        facts["best_line_first_move"] = None
        facts["best_line_contains_check"] = False
        facts["best_line_contains_capture"] = False

    # === 7. Material facts ===
    material_before = _material_balance(board_before, player_color)
    material_after_blunder = _material_balance(board_after_blunder, player_color)

    # Material after refutation line
    material_after_refutation: int | None = None
    if refutation_line:
        board_after_ref, _ = _play_line(board_after_blunder, refutation_line)
        material_after_refutation = _material_balance(board_after_ref, player_color)

    facts["material_balance_before"] = material_before
    facts["material_balance_after_blunder"] = material_after_blunder
    facts["material_balance_after_refutation"] = material_after_refutation
    facts["material_consequence_summary"] = _material_consequence_summary(
        material_before, material_after_blunder, material_after_refutation
    )

    # === 8. King safety facts ===
    facts["player_king_square_before"] = facts["player_king_square"]
    facts["opponent_king_square_before"] = facts["opponent_king_square"]
    facts["player_king_in_check_after_blunder"] = board_after_blunder.is_check()
    facts["critical_reply_checks_player_king"] = facts["critical_reply_is_check"]
    facts["refutation_forces_king_move"] = facts["king_was_forced_to_move"]
    facts["castled_status"] = _castled_status(board_before, player_color)

    # === 9. Loose / attacked pieces ===
    facts["undefended_player_pieces_after_blunder"] = _undefended_pieces(board_after_blunder, player_color)

    # === 10. Mistake categories ===
    played_intent = facts["played_move_intent_heuristic"]
    played_is_quiet_developing = played_intent in ("develops a piece", "moves a central pawn", "castles", "unknown")
    critical_reply_is_forcing = facts["critical_reply_type"] in ("check", "capture", "promotion")
    refutation_wins_material = (
        material_after_refutation is not None and (material_after_refutation - material_before) <= -2
    )
    best_move_develops_or_castles = (
        best_move is not None
        and (facts.get("best_move_intent_heuristic") in ("develops a piece", "castles") or (best_move and board_before.is_castling(best_move)))
    )

    categories = _mistake_categories(
        critical_reply_is_check=facts["critical_reply_is_check"],
        critical_reply_is_capture=facts["critical_reply_is_capture"],
        refutation_wins_material=refutation_wins_material,
        played_is_quiet_developing=played_is_quiet_developing,
        critical_reply_is_forcing=critical_reply_is_forcing,
        game_phase=game_phase,
        best_move_develops_or_castles=best_move_develops_or_castles,
        tactical_pattern=tactical_pattern,
    )
    facts["mistake_categories"] = categories

    # === 11. Best move purpose candidates ===
    if best_move:
        facts["best_move_purpose_candidates"] = _best_move_purpose_candidates(
            board_before, best_move, refutation_line, critical_reply_type_val
        )
    else:
        facts["best_move_purpose_candidates"] = []

    # === 12. Immediate consequence summary ===
    facts["immediate_consequence_summary"] = _immediate_consequence_summary(
        played_move_san=blunder_san,
        critical_reply_san=critical_reply_san,
        critical_reply_type_val=critical_reply_type_val,
        refutation_wins_material=refutation_wins_material,
        best_move_san=facts.get("best_move_san"),
        king_forced_to_move=facts["king_was_forced_to_move"],
    )

    # === 13. Transfer rule seed ===
    facts["transfer_rule_seed"] = _transfer_rule_seed(
        critical_reply_type_val=critical_reply_type_val,
        categories=categories,
        played_intent=played_intent,
    )

    return facts


def format_lesson_facts_for_prompt(facts: dict[str, Any]) -> str:
    """Format lesson facts dict into a text block for the LLM prompt."""
    lines: list[str] = []
    lines.append("Structured lesson facts (use as primary source of truth):")

    # Critical reply
    cr = facts.get("critical_reply_san")
    if cr:
        lines.append(f"- Critical reply: {cr} ({facts.get('critical_reply_type', 'unknown')})")
        if facts.get("critical_reply_is_check"):
            lines.append("- Critical reply gives check: yes")
        if facts.get("critical_reply_is_capture"):
            captured = facts.get("critical_reply_captured_piece") or "piece"
            lines.append(f"- Critical reply captures: {captured}")
        attacked = facts.get("pieces_attacked_by_critical_reply", [])
        if attacked:
            lines.append(f"- Pieces attacked by critical reply: {', '.join(attacked)}")
    else:
        lines.append("- Critical reply: not available")

    # Immediate consequence
    lines.append(f"- Immediate consequence: {facts.get('immediate_consequence_summary', 'not available')}")

    # Material
    lines.append(f"- Material consequence: {facts.get('material_consequence_summary', 'not available')}")
    mb = facts.get("material_balance_before")
    mar = facts.get("material_balance_after_refutation")
    if mb is not None and mar is not None:
        lines.append(f"- Material delta after refutation: {mar - mb:+d} pawns")

    # King safety
    if facts.get("critical_reply_checks_player_king"):
        lines.append("- King safety: critical reply checks the player's king")
    if facts.get("refutation_forces_king_move"):
        lines.append("- King safety: refutation forces king to move")
    castled = facts.get("castled_status")
    if castled and castled != "unknown":
        lines.append(f"- Castled status: {castled}")

    # Best move purpose
    purposes = facts.get("best_move_purpose_candidates", [])
    if purposes:
        lines.append(f"- Best move purpose: {'; '.join(purposes)}")

    # Mistake categories
    categories = facts.get("mistake_categories", [])
    if categories:
        lines.append(f"- Mistake categories: {', '.join(categories)}")

    # Transfer rule seed
    seed = facts.get("transfer_rule_seed")
    if seed:
        lines.append(f"- Transfer rule seed: {seed}")

    # Refutation line stats
    if facts.get("refutation_line_san"):
        ref_details: list[str] = []
        if facts.get("number_of_checks_in_refutation", 0) > 0:
            ref_details.append(f"{facts['number_of_checks_in_refutation']} check(s)")
        if facts.get("number_of_captures_in_refutation", 0) > 0:
            ref_details.append(f"{facts['number_of_captures_in_refutation']} capture(s)")
        captured_pieces = facts.get("captured_pieces_in_refutation", [])
        if captured_pieces:
            ref_details.append(f"captured: {', '.join(captured_pieces)}")
        if ref_details:
            lines.append(f"- Refutation line stats: {'; '.join(ref_details)}")

    # Loose pieces
    undefended = facts.get("undefended_player_pieces_after_blunder", [])
    if undefended:
        lines.append(f"- Undefended player pieces after blunder: {', '.join(undefended[:5])}")

    # Played move intent
    intent = facts.get("played_move_intent_heuristic", "unknown")
    if intent != "unknown":
        lines.append(f"- Played move intent: {intent}")

    # Best move intent
    best_intent = facts.get("best_move_intent_heuristic")
    if best_intent and best_intent != "unknown":
        lines.append(f"- Best move intent: {best_intent}")

    return "\n".join(lines)
