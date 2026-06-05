# Add these fields to class PuzzleResponse in blunder_tutor/web/api/_analysis_schemas.py
# They are required because response_model=schemas.PuzzleResponse otherwise filters them out.

explanation_consequence: str | None = None
explanation_refutation: str | None = None
explanation_comparison: str | None = None
explanation_llm: str | None = None
refutation_line: list[str] | None = None
refutation_line_san: list[str] | None = None
refutation_eval: int | None = None
