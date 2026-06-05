from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import chess

from blunder_tutor.analysis.pipeline.context import StepResult
from blunder_tutor.analysis.pipeline.steps.base import AnalysisStep
from blunder_tutor.analysis.tactics import PATTERN_LABELS
from blunder_tutor.constants import COLOR_LABELS, PHASE_LABELS
from blunder_tutor.services.llm_explanation import (
    LLM_EXPLANATION_VERSION,
    explain_training_lesson,
)
from blunder_tutor.utils.chess_utils import format_eval

if TYPE_CHECKING:
    from blunder_tutor.analysis.pipeline.context import StepContext

log = logging.getLogger(__name__)


def _board_at_ply(game: chess.pgn.Game) -> dict[int, str]:
    """Return a mapping of ply → FEN (position before the move at that ply)."""
    board = game.board()
    move_number = 1
    result: dict[int, str] = {}
    for move in game.mainline_moves():
        ply = (board.fullmove_number - 1) * 2 + (1 if board.turn == chess.WHITE else 2)  # noqa: WPS509
        result[ply] = board.fen()
        board.push(move)
        if board.turn == chess.WHITE:
            move_number += 1
    return result


class LLMExplanationStep(AnalysisStep):
    @property
    def step_id(self) -> str:
        return "llm"

    @property
    def depends_on(self) -> frozenset[str]:
        return frozenset({"write"})

    async def execute(self, ctx: StepContext) -> StepResult:
        blunders = await ctx.analysis_repo.fetch_blunders_for_game(ctx.game_id)
        if not blunders:
            return StepResult(step_id=self.step_id, success=True, data={"explanations_generated": 0})

        fen_at_ply = _board_at_ply(ctx.game)
        generated = 0

        for blunder in blunders:
            ply = int(blunder["ply"])
            fen = fen_at_ply.get(ply)
            if not fen:
                continue

            player_int = int(blunder["player"])
            player_color = COLOR_LABELS.get(player_int, "white")
            phase_int = blunder.get("game_phase")
            phase_label = PHASE_LABELS.get(phase_int) if phase_int is not None else None
            pattern_int = blunder.get("tactical_pattern")
            pattern_label = PATTERN_LABELS.get(pattern_int) if pattern_int is not None else None
            cp_loss = int(blunder.get("cp_loss", 0))

            best_line_str = blunder.get("best_line")
            best_line = best_line_str.split() if isinstance(best_line_str, str) and best_line_str else None
            refutation_line_san = blunder.get("refutation_line_san")

            try:
                text = explain_training_lesson(
                    fen=fen,
                    player_color=player_color,
                    blunder_san=str(blunder.get("san") or blunder["uci"]),
                    blunder_uci=str(blunder["uci"]),
                    best_move_san=blunder.get("best_move_san"),
                    best_move_uci=blunder.get("best_move_uci"),
                    eval_before_display=format_eval(int(blunder.get("eval_before", 0)), player_color),
                    eval_after_display=format_eval(int(blunder.get("eval_after", 0)), player_color),
                    cp_loss=cp_loss,
                    game_phase=phase_label,
                    tactical_pattern=pattern_label,
                    tactical_reason=blunder.get("tactical_reason"),
                    best_line=best_line,
                    refutation_line_san=refutation_line_san,
                )
            except Exception:
                log.exception("LLM explanation failed for game %s ply %d", ctx.game_id, ply)
                continue

            if text:
                await ctx.analysis_repo.update_move_llm_explanation(
                    ctx.game_id,
                    ply,
                    text,
                    version=LLM_EXPLANATION_VERSION,
                )
                generated += 1

        return StepResult(
            step_id=self.step_id,
            success=True,
            data={"explanations_generated": generated},
        )
