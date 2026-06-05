from __future__ import annotations

from dataclasses import dataclass, replace

import chess

from blunder_tutor.services.analysis_service import AnalysisService, PositionAnalysis
from blunder_tutor.trainer import BlunderFilter, BlunderPuzzle, Trainer
from blunder_tutor.utils.chess_utils import format_eval

MIN_BEST_LINE_PLIES = 10
MIN_REFUTATION_PLIES = 16


@dataclass
class PuzzleWithAnalysis:
    puzzle: BlunderPuzzle
    analysis: PositionAnalysis


class PuzzleService:
    def __init__(self, trainer: Trainer, analysis_service: AnalysisService):
        self.trainer = trainer
        self.analysis_service = analysis_service

    async def _enrich_lines(
        self, puzzle: BlunderPuzzle, analysis: PositionAnalysis
    ) -> tuple[PositionAnalysis, BlunderPuzzle]:
        """Re-fetch best/refutation lines from engine if stored ones are too short."""
        # Enrich best line
        if len(analysis.best_line) < MIN_BEST_LINE_PLIES:
            fresh = await self.analysis_service.analyze_position(
                fen=puzzle.fen, player_color=puzzle.player_color
            )
            if fresh.best_move_uci == analysis.best_move_uci and len(fresh.best_line) > len(analysis.best_line):
                analysis = PositionAnalysis(
                    eval_cp=analysis.eval_cp,
                    eval_display=analysis.eval_display,
                    best_move_uci=analysis.best_move_uci,
                    best_move_san=analysis.best_move_san,
                    best_line=fresh.best_line,
                )

        # Enrich refutation line
        refutation = puzzle.refutation_line_san
        if refutation is not None and len(refutation) < MIN_REFUTATION_PLIES:
            try:
                board = chess.Board(puzzle.fen)
                board.push_uci(puzzle.blunder_uci)
                fresh_ref = await self.analysis_service.analyze_position(
                    fen=board.fen(), player_color=puzzle.player_color
                )
                if fresh_ref.best_line and len(fresh_ref.best_line) > len(refutation):
                    puzzle = replace(puzzle, refutation_line_san=fresh_ref.best_line)
            except (ValueError, chess.InvalidMoveError):
                pass

        return analysis, puzzle

    async def get_puzzle_with_analysis(
        self,
        criteria: BlunderFilter | None = None,
    ) -> PuzzleWithAnalysis:
        puzzle = await self.trainer.pick_random_blunder(criteria or BlunderFilter())

        if puzzle.best_move_uci and puzzle.best_move_san and puzzle.best_line:
            best_line_list = puzzle.best_line.split()
            analysis = PositionAnalysis(
                eval_cp=puzzle.eval_before,
                eval_display=self._format_eval(puzzle.eval_before, puzzle.player_color),
                best_move_uci=puzzle.best_move_uci,
                best_move_san=puzzle.best_move_san,
                best_line=best_line_list,
            )
        else:
            analysis = await self.analysis_service.analyze_position(
                fen=puzzle.fen, player_color=puzzle.player_color
            )

        analysis, puzzle = await self._enrich_lines(puzzle, analysis)
        return PuzzleWithAnalysis(puzzle=puzzle, analysis=analysis)

    async def get_specific_puzzle(self, game_id: str, ply: int) -> PuzzleWithAnalysis:
        puzzle = await self.trainer.get_specific_blunder(game_id, ply)

        if puzzle.best_move_uci and puzzle.best_move_san and puzzle.best_line:
            best_line_list = puzzle.best_line.split()
            analysis = PositionAnalysis(
                eval_cp=puzzle.eval_before,
                eval_display=self._format_eval(puzzle.eval_before, puzzle.player_color),
                best_move_uci=puzzle.best_move_uci,
                best_move_san=puzzle.best_move_san,
                best_line=best_line_list,
            )
        else:
            analysis = await self.analysis_service.analyze_position(
                fen=puzzle.fen, player_color=puzzle.player_color
            )

        analysis, puzzle = await self._enrich_lines(puzzle, analysis)
        return PuzzleWithAnalysis(puzzle=puzzle, analysis=analysis)

    def _format_eval(self, eval_cp: int, player_color: str) -> str:
        return format_eval(eval_cp, player_color)
