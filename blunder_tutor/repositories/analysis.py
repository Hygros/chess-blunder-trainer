from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime

from blunder_tutor.constants import CLASSIFICATION_BLUNDER
from blunder_tutor.repositories.base import BaseDbRepository
from blunder_tutor.utils.time_control import classify_game_type


def _json_dumps(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _json_loads(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value.split()
    return parsed if isinstance(parsed, list) else None


def _decode_refutation_fields(row: object) -> dict[str, object]:
    result = dict(row)
    result["refutation_line"] = _json_loads(result.get("refutation_line"))
    result["refutation_line_san"] = _json_loads(result.get("refutation_line_san"))
    return result


class AnalysisRepository(BaseDbRepository):

    async def analysis_exists(self, game_id: str) -> bool:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT 1 FROM analysis_games WHERE game_id = ? LIMIT 1",
            (game_id,),  # noqa: WPS204 — sqlite parameter tuple, repeated per query.
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def write_analysis(  # noqa: WPS211 — args mirror analysis_games + analysis_moves SQL row width.
        self,
        *,
        game_id: str,
        pgn_path: str,
        analyzed_at: str,
        engine_path: str,
        depth: int | None,
        time_limit: float | None,
        thresholds: dict[str, int],
        moves: Iterable[dict[str, object]],
        eco_code: str | None = None,
        eco_name: str | None = None,
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO analysis_games (
                    game_id, pgn_path, analyzed_at, engine_path, depth, time_limit,
                    inaccuracy, mistake, blunder, eco_code, eco_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    pgn_path,
                    analyzed_at,
                    engine_path,
                    depth,
                    time_limit,
                    thresholds["inaccuracy"],
                    thresholds["mistake"],
                    thresholds["blunder"],
                    eco_code,
                    eco_name,
                ),
            )

            await conn.execute(
                "DELETE FROM analysis_moves WHERE game_id = ?", (game_id,)
            )
            await conn.executemany(
                """
                INSERT INTO analysis_moves (
                    game_id, ply, move_number, player, uci, san,
                    eval_before, eval_after, delta, cp_loss, classification,
                    best_move_uci, best_move_san, best_line, best_move_eval,
                    refutation_line, refutation_line_san, refutation_eval,
                    game_phase, tactical_pattern, tactical_reason, difficulty,
                    missed_mate_depth, llm_explanation, llm_explanation_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        game_id,
                        int(move["ply"]),
                        int(move["move_number"]),
                        0 if move["player"] == "white" else 1,
                        str(move["uci"]),
                        move.get("san"),
                        int(move["eval_before"]),
                        int(move["eval_after"]),
                        int(move["delta"]),
                        int(move["cp_loss"]),
                        int(move["classification"]),
                        move.get("best_move_uci"),
                        move.get("best_move_san"),
                        move.get("best_line"),
                        move.get("best_move_eval"),
                        _json_dumps(move.get("refutation_line")),
                        _json_dumps(move.get("refutation_line_san")),
                        move.get("refutation_eval"),
                        move.get("game_phase"),
                        move.get("tactical_pattern"),
                        move.get("tactical_reason"),
                        move.get("difficulty"),
                        move.get("missed_mate_depth"),
                        move.get("llm_explanation"),
                        move.get("llm_explanation_version"),
                    )
                    for move in moves
                ],
            )

    async def fetch_blunders(
        self, game_phases: list[int] | None = None
    ) -> list[dict[str, object]]:
        conn = await self.get_connection()
        if game_phases:
            placeholders = ",".join("?" * len(game_phases))
            query = f"""
                SELECT game_id, ply, player, uci, san, eval_before, eval_after, cp_loss,
                       best_move_uci, best_move_san, best_line, best_move_eval,
                       refutation_line, refutation_line_san, refutation_eval, game_phase,
                       tactical_pattern, tactical_reason, difficulty, missed_mate_depth,
                      llm_explanation, llm_explanation_version
                FROM analysis_moves
                WHERE classification = ? AND game_phase IN ({placeholders})
            """
            params = (CLASSIFICATION_BLUNDER, *game_phases)
        else:
            query = """
                SELECT game_id, ply, player, uci, san, eval_before, eval_after, cp_loss,
                       best_move_uci, best_move_san, best_line, best_move_eval,
                       refutation_line, refutation_line_san, refutation_eval, game_phase,
                       tactical_pattern, tactical_reason, difficulty, missed_mate_depth,
                      llm_explanation, llm_explanation_version
                FROM analysis_moves
                WHERE classification = ?
            """
            params = (CLASSIFICATION_BLUNDER,)
        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [_decode_refutation_fields(row) for row in rows]

    async def get_move_analysis(
        self, game_id: str, ply: int
    ) -> dict[str, object] | None:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT game_id, ply, player, uci, san, eval_before, eval_after, cp_loss,
                   best_move_uci, best_move_san, best_line, best_move_eval,
                   refutation_line, refutation_line_san, refutation_eval,
                   game_phase, tactical_pattern, tactical_reason,
                     difficulty, missed_mate_depth, llm_explanation,
                     llm_explanation_version
            FROM analysis_moves
            WHERE game_id = ? AND ply = ?
            """,
            (game_id, ply),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        return _decode_refutation_fields(row)

    async def fetch_moves(self, game_id: str) -> list[dict[str, object]]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT ply, move_number, player, uci, san, eval_before, eval_after,
                delta, cp_loss, classification, game_phase
            FROM analysis_moves
            WHERE game_id = ?
            ORDER BY ply
            """,
            (game_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_game_ids_missing_phase(self) -> list[str]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT DISTINCT game_id FROM analysis_moves WHERE game_phase IS NULL
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["game_id"] for row in rows]

    async def fetch_moves_for_phase_backfill(
        self, game_id: str
    ) -> list[dict[str, object]]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT ply, move_number
            FROM analysis_moves
            WHERE game_id = ? AND game_phase IS NULL
            ORDER BY ply
            """,
            (game_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_move_phase(self, game_id: str, ply: int, game_phase: int) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                "UPDATE analysis_moves SET game_phase = ? WHERE game_id = ? AND ply = ?",
                (game_phase, game_id, ply),
            )

    async def update_moves_phases_batch(
        self, updates: list[tuple[int, str, int]]
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.executemany(
                "UPDATE analysis_moves SET game_phase = ? WHERE game_id = ? AND ply = ?",
                updates,
            )

    async def get_game_ids_missing_eco(self) -> list[str]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT ag.game_id FROM analysis_games ag
            WHERE ag.eco_code IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM analysis_step_status ass
                WHERE ass.game_id = ag.game_id AND ass.step_id = 'eco'
            )
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["game_id"] for row in rows]

    async def get_all_analyzed_game_ids(self) -> list[str]:
        conn = await self.get_connection()
        async with conn.execute("SELECT game_id FROM analysis_games") as cursor:
            rows = await cursor.fetchall()
        return [row["game_id"] for row in rows]

    async def update_game_eco(
        self, game_id: str, eco_code: str | None, eco_name: str | None
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                "UPDATE analysis_games SET eco_code = ?, eco_name = ? WHERE game_id = ?",
                (eco_code, eco_name, game_id),
            )

    async def get_game_eco(self, game_id: str) -> dict[str, str | None]:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT eco_code, eco_name FROM analysis_games WHERE game_id = ?",
            (game_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return {"eco_code": row["eco_code"], "eco_name": row["eco_name"]}
        return {"eco_code": None, "eco_name": None}

    async def mark_step_completed(self, game_id: str, step_id: str) -> None:
        await self.mark_steps_completed(game_id, [step_id])

    async def mark_steps_completed(self, game_id: str, step_ids: list[str]) -> None:
        completed_at = datetime.now(UTC).isoformat()
        async with self.write_transaction() as conn:
            await conn.executemany(
                """
                INSERT OR REPLACE INTO analysis_step_status (game_id, step_id, completed_at)
                VALUES (?, ?, ?)
                """,
                [(game_id, step_id, completed_at) for step_id in step_ids],
            )

    async def get_completed_steps(self, game_id: str) -> set[str]:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT step_id FROM analysis_step_status WHERE game_id = ?",
            (game_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["step_id"] for row in rows}

    async def is_step_completed(self, game_id: str, step_id: str) -> bool:
        conn = await self.get_connection()
        async with conn.execute(
            "SELECT 1 FROM analysis_step_status WHERE game_id = ? AND step_id = ? LIMIT 1",
            (game_id, step_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None

    async def clear_step_status(self, game_id: str) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                "DELETE FROM analysis_step_status WHERE game_id = ?",
                (game_id,),
            )

    async def get_game_ids_missing_tactics(self) -> list[str]:
        """Get game IDs where blunders don't have tactical patterns classified."""
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT DISTINCT game_id FROM analysis_moves
            WHERE classification = 3 AND tactical_pattern IS NULL
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["game_id"] for row in rows]

    async def fetch_blunders_for_tactics_backfill(
        self, game_id: str
    ) -> list[dict[str, object]]:
        """Fetch blunders that need tactical pattern classification."""
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT ply, uci, best_move_uci
            FROM analysis_moves
            WHERE game_id = ? AND classification = 3 AND tactical_pattern IS NULL
            ORDER BY ply
            """,
            (game_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_move_tactics(
        self,
        game_id: str,
        ply: int,
        tactical_pattern: int | None,
        tactical_reason: str | None,
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                """
                UPDATE analysis_moves
                SET tactical_pattern = ?, tactical_reason = ?
                WHERE game_id = ? AND ply = ?
                """,
                (tactical_pattern, tactical_reason, game_id, ply),
            )

    async def update_moves_tactics_batch(
        self, updates: list[tuple[int | None, str | None, str, int]]
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.executemany(
                """
                UPDATE analysis_moves
                SET tactical_pattern = ?, tactical_reason = ?
                WHERE game_id = ? AND ply = ?
                """,
                updates,
            )

    async def fetch_blunders_with_tactics(
        self,
        game_phases: list[int] | None = None,
        tactical_patterns: list[int] | None = None,
        player_colors: list[int] | None = None,
        game_types: list[int] | None = None,
    ) -> list[dict[str, object]]:
        """Fetch blunders with optional filtering by phase, tactical pattern, color, and game type.

        Args:
            game_phases: Filter by game phase (0=opening, 1=middlegame, 2=endgame)
            tactical_patterns: Filter by tactical pattern
            player_colors: Filter by player color (0=white, 1=black)
            game_types: Filter by game type - requires post-fetch filtering since
                        game_type is computed from time_control
        """
        conn = await self.get_connection()
        conditions = ["am.classification = ?"]
        params: list = [CLASSIFICATION_BLUNDER]

        if game_phases:
            placeholders = ",".join("?" * len(game_phases))
            conditions.append(f"am.game_phase IN ({placeholders})")
            params.extend(game_phases)

        if tactical_patterns:
            placeholders = ",".join("?" * len(tactical_patterns))
            conditions.append(f"am.tactical_pattern IN ({placeholders})")
            params.extend(tactical_patterns)

        if player_colors:
            placeholders = ",".join("?" * len(player_colors))
            conditions.append(f"am.player IN ({placeholders})")
            params.extend(player_colors)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT am.game_id, am.ply, am.player, am.uci, am.san,
                   am.eval_before, am.eval_after, am.cp_loss,
                   am.best_move_uci, am.best_move_san, am.best_line, am.best_move_eval,
                   am.refutation_line, am.refutation_line_san, am.refutation_eval,
                   am.game_phase, am.tactical_pattern, am.tactical_reason,
                     g.time_control, am.difficulty, am.missed_mate_depth,
                     am.llm_explanation, am.llm_explanation_version
            FROM analysis_moves am
            JOIN game_index_cache g ON am.game_id = g.game_id
            WHERE {where_clause}
        """

        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        results = []
        game_types_set = set(game_types) if game_types else None

        for row in rows:
            time_control = row["time_control"]
            game_type = int(classify_game_type(time_control))

            if game_types_set and game_type not in game_types_set:
                continue

            results.append({**_decode_refutation_fields(row), "game_type": game_type})

        return results

    async def update_move_llm_explanation(
        self,
        game_id: str,
        ply: int,
        text: str,
        *,
        version: int,
    ) -> None:
        async with self.write_transaction() as conn:
            await conn.execute(
                """
                UPDATE analysis_moves
                SET llm_explanation = ?, llm_explanation_version = ?
                WHERE game_id = ? AND ply = ?
                """,
                (text, version, game_id, ply),
            )

    async def get_game_ids_missing_or_outdated_llm_explanation(
        self, *, expected_version: int
    ) -> list[str]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT DISTINCT game_id FROM analysis_moves
            WHERE classification = ?
              AND (
                llm_explanation IS NULL
                OR llm_explanation_version IS NULL
                OR llm_explanation_version != ?
              )
            """,
            (CLASSIFICATION_BLUNDER, expected_version),
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["game_id"] for row in rows]

    async def fetch_blunders_for_game(self, game_id: str) -> list[dict[str, object]]:
        conn = await self.get_connection()
        async with conn.execute(
            """
            SELECT ply, player, uci, san, eval_before, eval_after, cp_loss,
                   best_move_uci, best_move_san, best_line, best_move_eval,
                   refutation_line, refutation_line_san, refutation_eval,
                   game_phase, tactical_pattern, tactical_reason,
                     difficulty, missed_mate_depth, llm_explanation,
                     llm_explanation_version
            FROM analysis_moves
            WHERE game_id = ? AND classification = ?
            ORDER BY ply
            """,
            (game_id, CLASSIFICATION_BLUNDER),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_decode_refutation_fields(row) for row in rows]
