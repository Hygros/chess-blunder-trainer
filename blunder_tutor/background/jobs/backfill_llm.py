from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from blunder_tutor.analysis.pipeline.executor import PipelineExecutor
from blunder_tutor.analysis.pipeline.pipeline import (
    AnalysisPipeline,
    PipelineConfig,
    PipelinePreset,
)
from blunder_tutor.analysis.pipeline.steps import get_all_steps
from blunder_tutor.background.base import BaseJob
from blunder_tutor.background.registry import register_job
from blunder_tutor.services.llm_explanation import LLM_EXPLANATION_VERSION

if TYPE_CHECKING:
    from blunder_tutor.repositories.analysis import AnalysisRepository
    from blunder_tutor.repositories.game_repository import GameRepository
    from blunder_tutor.services.job_service import JobService, ProgressCallback

logger = logging.getLogger(__name__)


@register_job
class BackfillLLMJob(BaseJob):
    job_identifier: ClassVar[str] = "backfill_llm"

    def __init__(
        self,
        job_service: JobService,
        analysis_repo: AnalysisRepository,
        game_repo: GameRepository,
        engine_path: str,
    ) -> None:
        self.job_service = job_service
        self.analysis_repo = analysis_repo
        self.game_repo = game_repo
        self._executor = PipelineExecutor(
            analysis_repo=analysis_repo,
            game_repo=game_repo,
            engine_path=engine_path,
        )

    async def execute(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        game_ids = (
            await self.analysis_repo.get_game_ids_missing_or_outdated_llm_explanation(
                expected_version=LLM_EXPLANATION_VERSION
            )
        )

        if not game_ids:
            empty = {"games_processed": 0, "explanations_generated": 0}
            await self.job_service.complete_job(job_id, empty)
            return empty

        return await self.job_service.run_with_lifecycle(
            job_id,
            len(game_ids),
            lambda progress: self._backfill(job_id, game_ids, progress),
        )

    async def _backfill(
        self,
        job_id: str,
        game_ids: list[str],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        config = PipelineConfig.from_preset(PipelinePreset.LLM_BACKFILL)
        pipeline = AnalysisPipeline(config, get_all_steps())
        games_processed = 0
        explanations_generated = 0

        for i, game_id in enumerate(game_ids):
            if await self._is_cancelled(job_id):
                break
            report = await self._executor.execute_pipeline(pipeline, game_id)
            if report.success and "llm" in report.steps_executed:
                explanations_generated += report.step_durations.get("llm", 0) and 1 or 0
            games_processed += 1
            await progress(i + 1)

        return {"games_processed": games_processed, "explanations_generated": explanations_generated}

    async def _is_cancelled(self, job_id: str) -> bool:
        try:
            status = await self.job_service.get_job_status(job_id)
            return status == "cancelled"
        except Exception:
            return False
