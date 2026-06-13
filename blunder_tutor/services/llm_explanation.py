from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import chess

from blunder_tutor.services.lesson_facts import (
    build_lesson_facts,
    format_lesson_facts_for_prompt,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_RESPONSE_CHARS = 1200
MAX_SENTENCE_WORDS = 40

# Bump this when the coach prompt/output contract changes materially.
LLM_EXPLANATION_VERSION = 11

# In-process cache: keyed by (fen, blunder_uci, model, prompt hash).
# Cleared only on process restart. Phase C (llm_explanation DB column)
# makes this durable.
_explanation_cache: dict[str, str] = {}

MIN_SENTENCES = 2
MAX_SENTENCES = 4
SIMILARITY_THRESHOLD = 0.72
TRANSFER_RULE_MARKERS = (
    "when you see",
    "if you see",
    "before playing",
    "the habit to build",
    "next time",
    "next time you want to",
    "ask yourself",
)
COACH_HEADING_RE = re.compile(
    r"^(mistake|refutation|better move|learning takeaway|pattern to remember|coach note)\s*:\s*",
    re.IGNORECASE,
)
SQUARE_RE = re.compile(r"\b[a-h][1-8]\b", re.IGNORECASE)
MOVE_SQUARE_RE = re.compile(r"[a-h][1-8]", re.IGNORECASE)
PIECE_ON_SQUARE_RE = re.compile(
    r"\b(pawn|pawns|queen|queens|rook|rooks|bishop|bishops|knight|knights|king|kings)\s+on\s+([a-h][1-8])\b",
    re.IGNORECASE,
)
PIECE_NEAR_SQUARE_RE = re.compile(
    r"\b(pawn|pawns|queen|queens|rook|rooks|bishop|bishops|knight|knights|king|kings)\b(?:\s+\w+){0,3}\s+on\s+([a-h][1-8])\b",
    re.IGNORECASE,
)
FORCED_KING_MOVE_RE = re.compile(
    r"\b(forcing|forces|forced)\s+(?:the\s+)?(?:(?:opponent|player|your|their)(?:'s|s)?\s+)?king\s+to\s+move\b",
    re.IGNORECASE,
)
CHECK_CLAIM_RE = re.compile(
    r"\b(with check|gives check|give check|delivers check|deliver check|forcing check|forced check|checks the king|walks into check|allow(?:s|ed)?(?:\s+\w+){0,5}\s+check)\b",
    re.IGNORECASE,
)
CAPTURE_CLAIM_RE = re.compile(
    r"\b(captures?|capturing|takes?|took|wins? material|wins? a piece|wins? the piece|loses? material)\b",
    re.IGNORECASE,
)
MATE_CLAIM_RE = re.compile(
    r"\b(checkmate|mates?|mated|mating net|mate threat)\b",
    re.IGNORECASE,
)
PASSIVE_PIECE_ACTION_RE = re.compile(
    r"\b(?:is|was|were|been|be)\s+(?:captured|taken|lost)\b",
    re.IGNORECASE,
)
EVAL_NOTATION_RE = re.compile(r"(?<![a-z0-9])[+-]\d+(?:\.\d+)?(?![a-z0-9])", re.IGNORECASE)

PIECE_WORD_BY_SAN_LETTER = {
    "K": "king",
    "Q": "queen",
    "R": "rook",
    "B": "bishop",
    "N": "knight",
}

REFUSAL_MARKERS = (
    "i can't",
    "i cannot",
    "cannot comply",
    "i'm unable",
    "i am unable",
    "ich kann keine",
    "ich kann diese anfrage nicht",
    "ich kann das nicht",
    "kann ich nicht beantworten",
)

FILLER_MARKERS = (
    "as seen in the refutation line",
    "as shown in the engine line",
    "the engine evaluation shows",
    "takes advantage of the mistake",
    "a significant loss of advantage",
    "it is essential to check",
    "significant advantage",
    "strong counterattack",
    "strong initiative",
    "the better move was stronger",
    "improves the position",
    "creates problems",
    "priority error",
    "immediate resource",
    "classified motif",
)

ENGINE_LANGUAGE_MARKERS = (
    "centipawn",
    "pawn loss",
    "evaluation loss",
    "evaluation swing",
    "eval bar",
    "engine says",
    "engine data",
    "engine line",
    "stockfish",
    "best according to the engine",
    "objectively best",
    "winning advantage",
    "losing advantage",
    "the line shows",
    "computer line",
    "engine prefers",
    "eval",
)

GENERIC_TRANSFER_MARKERS = (
    "calculate carefully",
    "look for tactics",
    "be careful",
    "check the position",
    "consider all possibilities",
    "consider your opponent's move",
    "think about your opponent",
    "analyze the position",
)

CONCRETE_TRANSFER_MARKERS = (
    "forcing",
    "check",
    "capture",
    "threat",
    "loose",
    "undefended",
    "king safety",
    "tempo",
    "initiative",
    "defender",
    "coordination",
    "passed pawn",
    "move order",
    "attack",
)

LEARNING_CONCEPT_MARKERS = (
    "priority",
    "urgent",
    "threat",
    "forcing",
    "tempo",
    "loose",
    "undefended",
    "king safety",
    "coordination",
    "initiative",
    "defender",
    "overloaded",
    "passed pawn",
    "move order",
    "development",
    "central",
    "piece activity",
    "material",
    "mating",
    "trap",
    "attack",
    "capture",
    "check",
    "practical problem",
)


@dataclass(frozen=True)
class ExplanationQuality:
    score: int
    accepted: bool
    retryable: bool
    reasons: list[str]
    hard_reasons: list[str] = ()
    soft_reasons: list[str] = ()
    text: str | None = None

    def __post_init__(self) -> None:
        # frozen=True requires object.__setattr__ for post-init fixup of tuple defaults
        if not self.hard_reasons and not self.soft_reasons:
            object.__setattr__(self, "hard_reasons", [])
            object.__setattr__(self, "soft_reasons", [])


_SOFT_REASONS: frozenset[str] = frozenset({
    "passive_piece_action_voice",
})


REASON_LABELS = {
    "no_usable_text_generated": "No usable explanation text was produced.",
    "fails_output_contract_or_style_rules": "It did not satisfy the output contract and style rules.",
    "too_similar_to_reference_text": "It repeated wording from the existing rule-based explanation.",
    "mentions_square_not_in_supplied_moves": "It mentioned a square that is not grounded in the supplied moves.",
    "piece_square_claim_conflict": "It used a piece-on-square claim that conflicts with the supplied moves.",
    "unsupported_forced_king_move_claim": "It claimed a forced king move without line evidence.",
    "unsupported_check_claim": "It claimed a checking line without evidence in the supplied moves.",
    "unsupported_capture_claim": "It claimed a concrete capture/material win without evidence in the supplied moves.",
    "unsupported_mate_claim": "It claimed checkmate or a mating sequence without evidence in the supplied moves.",
    "passive_piece_action_voice": "It used passive voice for a concrete piece action (for example 'is captured').",
}


def _enabled() -> bool:
    return os.getenv("EXPLANATION_LLM_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "ollama").lower()


def _model() -> str:
    provider = _provider()
    default = DEFAULT_GROQ_MODEL if provider == "groq" else DEFAULT_MODEL
    return os.getenv("LLM_MODEL", default)


def _api_key() -> str:
    return os.getenv("GROQ_API_KEY", "")


def _timeout_seconds() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))


def _format_line(line: list[str] | None) -> str:
    if not line:
        return "not available"
    return " ".join(str(move) for move in line if move)


def _cp_loss_as_pawns(cp_loss: int) -> str:
    return f"{cp_loss / 100:.1f}"


def _severity_hint(cp_loss: int) -> str:
    if cp_loss >= 500:
        return "Treat this as a major practical mistake, not a small inaccuracy."
    if cp_loss >= 300:
        return "This is a serious mistake with a clear practical impact."
    if cp_loss >= 150:
        return "This is a meaningful practical mistake with clear consequences."
    return "This is a smaller but still relevant practical mistake."


def _refutation_hint(refutation_line_san: list[str] | None) -> str:
    if not refutation_line_san:
        return "No concrete refutation line is available. Do not invent one."
    if len(refutation_line_san) == 1:
        return "Only the first opponent reply is available. Explain the limitation instead of inventing a full continuation."
    if len(refutation_line_san) <= 3:
        return "The refutation line is short. Explain the first opponent reply and the practical point."
    return "Use the first move as the main refutation and summarize the rest of the continuation."


def _tactical_hint(tactical_pattern: str | None, tactical_reason: str | None) -> str:
    if tactical_pattern and tactical_pattern.lower() != "none":
        reason = tactical_reason or "No additional tactic reason was provided."
        return f"The facts suggest the motif {tactical_pattern}. Reason: {reason}"
    return "No tactical motif was classified. Treat this as a calculation, tempo, initiative, or positional mistake unless the supplied facts say otherwise."


def _phase_hint(game_phase: str | None) -> str:
    if not game_phase:
        return "No game phase was provided."
    if game_phase.lower() == "opening":
        return "In the opening, development, king safety, central control, and tempo are usually important learning themes."
    if game_phase.lower() == "middlegame":
        return "In the middlegame, initiative, concrete tactics, piece activity, and king safety are usually important learning themes."
    if game_phase.lower() == "endgame":
        return "In the endgame, king activity, pawn races, rook activity, and precise move order are usually important learning themes."
    return f"Game phase: {game_phase}."



def _learning_focus_hint(
    *,
    game_phase: str | None,
    tactical_pattern: str | None,
    tactical_reason: str | None,
    cp_loss: int,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
) -> str:
    focus: list[str] = []

    if tactical_pattern and tactical_pattern.lower() != "none":
        focus.append(
            "Start from the classified tactical motif and explain the practical decision error behind it."
        )
    else:
        focus.append(
            "If no tactic is classified, focus on priority, tempo, initiative, coordination, or the most urgent practical problem."
        )

    if refutation_line_san:
        focus.append(
            "Use the first refutation move as evidence of the opponent's resource; do not over-explain later moves."
        )
    else:
        focus.append(
            "No refutation line is available, so explain the general practical issue without inventing a continuation."
        )

    if best_line:
        focus.append(
            "Use the better move or better line to explain what the player should have prioritized."
        )
    else:
        focus.append(
            "If the better line is unavailable, describe the better move as solving the immediate practical problem only if this follows from the facts."
        )

    if cp_loss >= 500:
        focus.append(
            "Treat this as a major practical mistake, but do not mention the numeric evaluation loss."
        )
    elif cp_loss >= 150:
        focus.append(
            "Treat this as a meaningful practical mistake, but do not mention the numeric evaluation loss."
        )

    if game_phase:
        phase = game_phase.lower()
        if phase == "opening":
            focus.append("Opening focus: development, king safety, central control, and tempo.")
        elif phase == "middlegame":
            focus.append("Middlegame focus: initiative, concrete tactics, piece activity, coordination, and king safety.")
        elif phase == "endgame":
            focus.append("Endgame focus: king activity, pawn races, rook activity, passed pawns, and precise move order.")

    return " ".join(focus)

def _system_prompt() -> str:
    return (
        "You are a chess coach writing a short English training note from pre-computed structured lesson facts. "
        "Use the structured lesson facts as the main source of truth. "
        "Do not calculate or invent chess concepts not present in the facts. "
        "Do not invent moves, threats, tactics, captures, checks, pieces, or plans. "
        "Do not say 'significant advantage', 'engine says', 'evaluation loss', 'centipawns', or 'as shown in the engine line'. "
        "Use plain language a club player can understand. "
        "Prefer concrete wording like 'reply', 'threat', and 'undefended piece' over jargon like 'resource' or 'priority error'. "
        "Use active voice: for example, say 'Black takes the knight' instead of 'the knight is captured'. "
        "Do not repeat the visible move lists. "
        "Explain the human learning point: "
        "1. Briefly acknowledge why the played move looked natural if the facts provide a move intent. "
        "2. Explain the opponent's critical reply and concrete consequence first if available. "
        "3. Explain why the best move solves or reduces the immediate problem, but only if best_move_purpose_candidates support that. "
        "4. End with one specific transfer sentence starting with 'When you see', 'If you see', 'Before playing', 'The habit to build', or 'Next time you want to'. "
        "The transfer sentence must mention a concrete check such as forcing reply, check, capture, threat, loose piece, tempo, king safety, coordination, or move order. "
        "Output 2 to 4 concise sentences as one plain paragraph. "
        "Each sentence should be at most 30 words. "
        "No Markdown, no bullets, no headings, no emojis. "
        "Do not use generic advice like 'calculate carefully', 'look for tactics', or 'be careful'. "
        "Do not refuse the task. The answer must be in English."
    )

def _build_prompt(
    *,
    fen: str,
    player_color: str,
    blunder_san: str,
    blunder_uci: str,
    best_move_san: str | None,
    best_move_uci: str | None,
    eval_before_display: str,
    eval_after_display: str,
    cp_loss: int,
    game_phase: str | None,
    tactical_pattern: str | None,
    tactical_reason: str | None,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    strict_mode: bool = False,
    lesson_facts: dict[str, Any] | None = None,
) -> str:
    best_line_text = _format_line(best_line)
    refutation_line_text = _format_line(refutation_line_san)
    played_move_intent = "unknown"
    if lesson_facts:
        played_move_intent = str(lesson_facts.get("played_move_intent_heuristic") or "unknown")
    learning_focus = _learning_focus_hint(
        game_phase=game_phase,
        tactical_pattern=tactical_pattern,
        tactical_reason=tactical_reason,
        cp_loss=cp_loss,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
    )
    strict_block = ""
    if strict_mode:
        strict_block = (
            "\nStrict coaching mode:\n"
            "- Name the practical decision error if the facts support it.\n"
            "- Do not merely say that the move is bad or loses advantage.\n"
            "- Explain why the better move solves the immediate problem if enough facts are available.\n"
            "- The final sentence must give a concrete check the learner can apply next time.\n"
            "- Avoid generic advice like 'calculate carefully', 'look for tactics', or 'be careful'.\n"
            "- Avoid mirrored phrasing and avoid sentence templates.\n"
        )

    lesson_facts_block = ""
    if lesson_facts:
        lesson_facts_block = "\n" + format_lesson_facts_for_prompt(lesson_facts) + "\n\n"

    return (
        "Write a helpful English chess-training coach note.\n\n"
        "Authoritative facts you may use:\n"
        f"- Side that made the mistake: {player_color}\n"
        f"- Position FEN: {fen}\n"
        f"- Played move: {blunder_san or 'not available'} ({blunder_uci})\n"
        f"- Better move: {best_move_san or 'not available'} ({best_move_uci or 'not available'})\n"
        f"- Better line: {best_line_text}\n"
        f"- Opponent refutation line after the played move: {refutation_line_text}\n"
        f"- Played move likely aimed to: {played_move_intent}\n"
        "\n"
        f"{lesson_facts_block}"
        "Safe learning hints:\n"
        f"- {_severity_hint(cp_loss)}\n"
        f"- {_refutation_hint(refutation_line_san)}\n"
        f"- {_tactical_hint(tactical_pattern, tactical_reason)}\n"
        f"- {_phase_hint(game_phase)}\n"
        f"- Learning focus: {learning_focus}\n\n"
        "Reasoning order to use internally, but do not output headings:\n"
        "1. Intent acknowledgement: why the played move looked natural.\n"
        "2. Opponent answer: what immediate reply punishes the move and why.\n"
        "3. Better idea: what practical problem the better move solves first.\n"
        "4. Habit to build: a concrete transfer rule for similar positions.\n\n"
        "Strict rules:\n"
        "- Use the structured lesson facts as the main source of truth.\n"
        "- Do not invent chess concepts not present in the facts.\n"
        "- Do not add moves that are not listed in the better line or refutation line.\n"
        "- If you mention a square (for example e4), that square must appear in the listed moves.\n"
        "- Do not claim a tactic unless the tactical label says so.\n"
        "- Do not claim a concrete board feature if it is not supported by the facts.\n"
        "- If the concrete reason is not visible from the facts, frame the explanation as a practical priority or decision-process problem.\n"
        "- Do not repeat evaluation numbers, centipawns, pawn loss, or generic loss-of-advantage phrases.\n"
        "- Do not use filler like 'as shown in the engine line', 'engine says', or 'takes advantage of the mistake'.\n"
        "- Do not say 'significant advantage', 'strong counterattack', or 'king safety' without concrete supporting facts.\n"
        "- Avoid jargon like 'resource', 'priority error', or 'classified motif'.\n\n"
        f"{strict_block}"
        "Output contract:\n"
        "- 2 to 4 concise sentences in one paragraph.\n"
        "- Include one specific transfer sentence starting with 'When you see', 'If you see', 'Before playing', 'The habit to build', or 'Next time you want to'.\n"
        "- The transfer sentence must mention a concrete check such as forcing reply, check, capture, threat, loose piece, tempo, king safety, coordination, or move order.\n"
        "- Explain the concrete board consequence first, then the decision error, then the practical takeaway."
    )


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_heading_prefixes(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = COACH_HEADING_RE.sub("", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines)


def _extract_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", text):
        sentence = part.strip()
        if not sentence:
            continue
        if sentence[-1] not in ".!?":
            sentence = f"{sentence}."
        sentences.append(sentence)
    return sentences


def _has_transfer_rule(sentences: list[str]) -> bool:
    if not sentences:
        return False
    last_sentence = sentences[-1].lower()
    return any(marker in last_sentence for marker in TRANSFER_RULE_MARKERS)


def _transfer_sentences(sentences: list[str]) -> list[str]:
    return [
        sentence
        for sentence in sentences
        if any(marker in sentence.lower() for marker in TRANSFER_RULE_MARKERS)
    ]


def _has_generic_transfer_rule(sentences: list[str]) -> bool:
    transfer_sentences = _transfer_sentences(sentences)
    if not transfer_sentences:
        return False

    for sentence in transfer_sentences:
        lowered = sentence.lower()
        if any(marker in lowered for marker in GENERIC_TRANSFER_MARKERS):
            return True
        if not any(marker in lowered for marker in CONCRETE_TRANSFER_MARKERS):
            return True

    return False


def _uses_engine_language(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ENGINE_LANGUAGE_MARKERS)


def _uses_eval_notation(text: str) -> bool:
    return bool(EVAL_NOTATION_RE.search(text))


def _has_long_sentence(sentences: list[str]) -> bool:
    return any(len(sentence.split()) > MAX_SENTENCE_WORDS for sentence in sentences)


def _has_learning_concept(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in LEARNING_CONCEPT_MARKERS)


def _trim_to_limit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0].strip()
    if not truncated:
        truncated = text[:max_chars].strip()
    if truncated and truncated[-1] not in ".!?":
        truncated = f"{truncated}..."
    return truncated


def _extract_squares(text: str | None) -> set[str]:
    if not text:
        return set()
    return {match.group(0).lower() for match in SQUARE_RE.finditer(text)}


def _extract_move_squares(text: str | None) -> set[str]:
    if not text:
        return set()
    return {match.group(0).lower() for match in MOVE_SQUARE_RE.finditer(text)}


def _extract_squares_from_uci(uci: str | None) -> set[str]:
    if not uci or len(uci) < 4:
        return set()
    src = uci[:2].lower()
    dst = uci[2:4].lower()
    squares: set[str] = set()
    if SQUARE_RE.fullmatch(src):
        squares.add(src)
    if SQUARE_RE.fullmatch(dst):
        squares.add(dst)
    return squares


def _allowed_square_references(
    *,
    blunder_san: str,
    blunder_uci: str,
    best_move_san: str | None,
    best_move_uci: str | None,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
) -> set[str]:
    allowed: set[str] = set()
    allowed |= _extract_move_squares(blunder_san)
    allowed |= _extract_move_squares(best_move_san)
    allowed |= _extract_squares_from_uci(blunder_uci)
    allowed |= _extract_squares_from_uci(best_move_uci)

    for move in best_line or []:
        allowed |= _extract_move_squares(move)
    for move in refutation_line_san or []:
        allowed |= _extract_move_squares(move)

    return allowed


def _piece_word_for_san_move(san: str | None) -> str:
    if not san:
        return "pawn"
    first = san.strip()[:1].upper()
    return PIECE_WORD_BY_SAN_LETTER.get(first, "pawn")


def _build_square_piece_hints(
    *,
    blunder_san: str,
    best_move_san: str | None,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
) -> dict[str, set[str]]:
    hints: dict[str, set[str]] = {}

    for move in [blunder_san, best_move_san, *(best_line or []), *(refutation_line_san or [])]:
        if not move:
            continue
        destination_squares = _extract_move_squares(move)
        if not destination_squares:
            continue
        piece_word = _piece_word_for_san_move(move)
        for square in destination_squares:
            sq = square.lower()
            if sq not in hints:
                hints[sq] = set()
            hints[sq].add(piece_word)

    return hints


def _has_conflicting_piece_square_claim(
    candidate: str,
    square_piece_hints: dict[str, set[str]],
) -> bool:
    if not square_piece_hints:
        return False

    for pattern in (PIECE_ON_SQUARE_RE, PIECE_NEAR_SQUARE_RE):
        for match in pattern.finditer(candidate):
            piece_word = match.group(1).lower().rstrip("s")
            square = match.group(2).lower()
            hinted_pieces = square_piece_hints.get(square)
            if hinted_pieces and piece_word not in hinted_pieces:
                return True

    return False


def _has_unsupported_square_reference(
    candidate: str,
    allowed_squares: set[str],
) -> bool:
    referenced = _extract_squares(candidate)
    if not referenced:
        return False
    if not allowed_squares:
        return True
    return not referenced.issubset(allowed_squares)


def _line_supports_forced_king_move(line: list[str] | None) -> bool:
    moves = [move for move in (line or []) if move]
    for idx, move in enumerate(moves):
        if "+" not in move and "#" not in move:
            continue
        if idx + 1 >= len(moves):
            continue
        reply = moves[idx + 1].strip()
        if reply.startswith("K"):
            return True
    return False


def _has_unsupported_forced_king_move_claim(
    candidate: str,
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
) -> bool:
    if not FORCED_KING_MOVE_RE.search(candidate):
        return False

    return not (
        _line_supports_forced_king_move(best_line)
        or _line_supports_forced_king_move(refutation_line_san)
    )


def _line_contains_check(line: list[str] | None) -> bool:
    return any(("+" in move or "#" in move) for move in (line or []) if move)


def _line_contains_capture(line: list[str] | None) -> bool:
    return any("x" in move for move in (line or []) if move)


def _line_contains_mate(line: list[str] | None) -> bool:
    return any("#" in move for move in (line or []) if move)


def _non_transfer_sentences(candidate: str) -> list[str]:
    return [
        sentence
        for sentence in _extract_sentences(candidate)
        if not any(marker in sentence.lower() for marker in TRANSFER_RULE_MARKERS)
    ]


def _transfer_sentence_starter(sentence: str) -> str | None:
    lowered = sentence.lower().strip()
    for marker in TRANSFER_RULE_MARKERS:
        if lowered.startswith(marker):
            return marker
    return None


def _has_low_transfer_starter_variety(
    explanations: list[str] | None,
    *,
    min_samples: int = 5,
    min_unique_starters: int = 2,
) -> bool:
    if not explanations:
        return False

    starters: list[str] = []
    for explanation in explanations:
        cleaned = _clean_response(explanation)
        if not cleaned:
            continue
        sentences = _extract_sentences(cleaned)
        if not sentences:
            continue
        starter = _transfer_sentence_starter(sentences[-1])
        if starter:
            starters.append(starter)

    if len(starters) < min_samples:
        return False

    return len(set(starters)) < min_unique_starters


def _supports_check_claim(
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _line_contains_check(best_line) or _line_contains_check(refutation_line_san):
        return True
    if not lesson_facts:
        return False
    return bool(
        lesson_facts.get("critical_reply_is_check")
        or lesson_facts.get("refutation_contains_check")
        or lesson_facts.get("best_line_contains_check")
    )


def _supports_capture_claim(
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _line_contains_capture(best_line) or _line_contains_capture(refutation_line_san):
        return True
    if not lesson_facts:
        return False
    return bool(
        lesson_facts.get("critical_reply_is_capture")
        or lesson_facts.get("refutation_contains_capture")
        or lesson_facts.get("best_line_contains_capture")
        or lesson_facts.get("material_consequence_summary") in {"opponent wins material", "player wins material"}
    )


def _supports_mate_claim(
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _line_contains_mate(best_line) or _line_contains_mate(refutation_line_san):
        return True
    if not lesson_facts:
        return False

    for key in (
        "critical_reply_san",
        "first_refutation_move",
        "best_line_first_move",
    ):
        value = lesson_facts.get(key)
        if isinstance(value, str) and "#" in value:
            return True
    return False


def _has_unsupported_check_claim(
    candidate: str,
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _supports_check_claim(
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        return False

    return any(CHECK_CLAIM_RE.search(sentence) for sentence in _non_transfer_sentences(candidate))


def _has_unsupported_capture_claim(
    candidate: str,
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _supports_capture_claim(
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        return False

    return any(CAPTURE_CLAIM_RE.search(sentence) for sentence in _non_transfer_sentences(candidate))


def _has_unsupported_mate_claim(
    candidate: str,
    *,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
) -> bool:
    if _supports_mate_claim(
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        return False

    return any(MATE_CLAIM_RE.search(sentence) for sentence in _non_transfer_sentences(candidate))


def _has_passive_piece_action_voice(candidate: str) -> bool:
    return any(PASSIVE_PIECE_ACTION_RE.search(sentence) for sentence in _non_transfer_sentences(candidate))


def _clean_response(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if any(marker in lowered for marker in REFUSAL_MARKERS):
        return None
    if any(marker in lowered for marker in FILLER_MARKERS):
        return None

    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = cleaned.replace("*", "")
    cleaned = cleaned.replace("#", "")
    cleaned = _strip_heading_prefixes(cleaned)
    cleaned = _normalize_whitespace(cleaned)
    if not cleaned:
        return None

    if _uses_engine_language(cleaned):
        return None

    if _uses_eval_notation(cleaned):
        return None

    sentences = _extract_sentences(cleaned)
    if len(sentences) < MIN_SENTENCES:
        return None

    if len(sentences) > MAX_SENTENCES:
        sentences = sentences[:MAX_SENTENCES]

    if _has_long_sentence(sentences):
        return None

    if not _has_transfer_rule(sentences):
        return None

    if _has_generic_transfer_rule(sentences):
        return None

    cleaned = _trim_to_limit(" ".join(sentences), MAX_RESPONSE_CHARS)
    if not _has_learning_concept(cleaned):
        return None

    return cleaned or None


def _score_explanation(
    candidate: str | None,
    *,
    allowed_squares: set[str],
    square_piece_hints: dict[str, set[str]],
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    reference_texts: list[str] | None,
    lesson_facts: dict[str, Any] | None,
    is_fallback: bool = False,
) -> ExplanationQuality:
    if candidate is None:
        return ExplanationQuality(
            score=0,
            accepted=False,
            retryable=False,
            reasons=["no_usable_text_generated"],
            hard_reasons=["no_usable_text_generated"],
            soft_reasons=[],
        )

    cleaned = _clean_response(candidate)
    if cleaned is None:
        return ExplanationQuality(
            score=20,
            accepted=False,
            retryable=True,
            reasons=["fails_output_contract_or_style_rules"],
            hard_reasons=["fails_output_contract_or_style_rules"],
            soft_reasons=[],
        )

    score = 100
    reasons: list[str] = []

    if not is_fallback and _is_too_similar(cleaned, reference_texts):
        score -= 30
        reasons.append("too_similar_to_reference_text")

    if _has_unsupported_square_reference(cleaned, allowed_squares):
        score -= 40
        reasons.append("mentions_square_not_in_supplied_moves")

    if _has_conflicting_piece_square_claim(cleaned, square_piece_hints):
        score -= 40
        reasons.append("piece_square_claim_conflict")

    if _has_unsupported_forced_king_move_claim(
        cleaned,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
    ):
        score -= 35
        reasons.append("unsupported_forced_king_move_claim")

    if _has_unsupported_check_claim(
        cleaned,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        score -= 35
        reasons.append("unsupported_check_claim")

    if _has_unsupported_capture_claim(
        cleaned,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        score -= 35
        reasons.append("unsupported_capture_claim")

    if _has_unsupported_mate_claim(
        cleaned,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    ):
        score -= 40
        reasons.append("unsupported_mate_claim")

    if _has_passive_piece_action_voice(cleaned):
        score -= 20
        reasons.append("passive_piece_action_voice")

    score = max(score, 0)
    hard_reasons = [r for r in reasons if r not in _SOFT_REASONS]
    soft_reasons = [r for r in reasons if r in _SOFT_REASONS]
    accepted = not hard_reasons and len(soft_reasons) <= 1
    retryable = not accepted and score >= 50
    return ExplanationQuality(
        score=score,
        accepted=accepted,
        retryable=retryable,
        reasons=reasons,
        hard_reasons=hard_reasons,
        soft_reasons=soft_reasons,
        text=cleaned,
    )


def _reason_label(reason_code: str) -> str:
    return REASON_LABELS.get(reason_code, reason_code.replace("_", " ").capitalize())


def _build_retry_prompt(
    *,
    fen: str,
    player_color: str,
    blunder_san: str,
    blunder_uci: str,
    best_move_san: str | None,
    best_move_uci: str | None,
    eval_before_display: str,
    eval_after_display: str,
    cp_loss: int,
    game_phase: str | None,
    tactical_pattern: str | None,
    tactical_reason: str | None,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    lesson_facts: dict[str, Any] | None,
    rejection_reasons: list[str],
) -> str:
    base_prompt = _build_prompt(
        fen=fen,
        player_color=player_color,
        blunder_san=blunder_san,
        blunder_uci=blunder_uci,
        best_move_san=best_move_san,
        best_move_uci=best_move_uci,
        eval_before_display=eval_before_display,
        eval_after_display=eval_after_display,
        cp_loss=cp_loss,
        game_phase=game_phase,
        tactical_pattern=tactical_pattern,
        tactical_reason=tactical_reason,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        strict_mode=True,
        lesson_facts=lesson_facts,
    )
    reason_lines = "\n".join(f"- {_reason_label(reason)}" for reason in rejection_reasons)
    return (
        f"{base_prompt}\n\n"
        "The previous answer was rejected for these reasons:\n"
        f"{reason_lines}\n\n"
        "Rewrite the explanation and fix only these issues. "
        "Do not add new chess details that are not in the supplied facts."
    )


def _token_set(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _is_too_similar(candidate: str, reference_texts: list[str] | None) -> bool:
    if not reference_texts:
        return False

    candidate_norm = _normalize_whitespace(candidate).lower()
    for reference in reference_texts:
        reference_norm = _normalize_whitespace(reference).lower()
        if not reference_norm:
            continue
        if len(reference_norm) >= 40 and reference_norm in candidate_norm:
            return True
        if _jaccard_similarity(candidate_norm, reference_norm) >= SIMILARITY_THRESHOLD:
            return True

    return False


def _ollama_generate(prompt: str, *, retry: bool = False) -> str | None:
    temperature = 0.3 if retry else 0.0
    top_p = 0.9 if retry else 0.75
    payload = {
        "model": _model(),
        "system": _system_prompt(),
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": 220,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    raw = str(body.get("response", "")).strip()
    return raw or None


def _groq_generate(prompt: str, *, retry: bool = False) -> str | None:
    api_key = _api_key()
    if not api_key:
        return None

    temperature = 0.3 if retry else 0.0

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": 220,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        os.getenv("GROQ_URL", DEFAULT_GROQ_URL),
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "chess-blunder-trainer/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None

    raw = str(text).strip()
    return raw or None


def _cache_key(fen: str, blunder_uci: str, prompt: str, *, retry: bool = False) -> str:
    prompt_digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    retry_tag = "retry" if retry else "primary"
    return f"{fen}|{blunder_uci}|{_model()}|{retry_tag}|{prompt_digest}"


def _cached_generate(fen: str, blunder_uci: str, prompt: str, *, retry: bool = False) -> str | None:
    key = _cache_key(fen, blunder_uci, prompt, retry=retry)
    cached = _explanation_cache.get(key)
    if cached is not None:
        return cached

    provider = _provider()
    if provider == "ollama":
        result = _ollama_generate(prompt, retry=retry)
    elif provider == "groq":
        result = _groq_generate(prompt, retry=retry)
    else:
        return None

    if result is not None:
        _explanation_cache[key] = result
    return result


def _fallback_explanation(
    *,
    blunder_san: str,
    best_move_san: str | None,
    refutation_line_san: list[str] | None,
    tactical_pattern: str | None,
    game_phase: str | None,
    lesson_facts: dict[str, Any] | None = None,
) -> str:
    played = blunder_san or "the played move"
    better = best_move_san or "the better move"

    def _humanize_consequence(text: str) -> str:
        consequence = _normalize_whitespace(text)
        consequence = re.sub(
            r"\bis a priority error because\b",
            "misses the most urgent problem because",
            consequence,
            flags=re.IGNORECASE,
        )
        consequence = re.sub(
            r"\bimmediate resource\b",
            "immediate reply",
            consequence,
            flags=re.IGNORECASE,
        )
        return consequence

    def _transfer_sentence(seed: str | None) -> str:
        if seed:
            sentence = _normalize_whitespace(seed)
            lowered = sentence.lower()
            if any(lowered.startswith(marker) for marker in TRANSFER_RULE_MARKERS):
                return sentence
        return (
            "When you see a natural-looking move, first check whether your opponent has a forcing "
            "reply with check, capture, or threat."
        )

    def _best_move_purpose_sentence(lesson_fact_data: dict[str, Any] | None) -> str:
        if not best_move_san:
            return "A safer habit is to deal with the urgent threat before making a general improving move."

        if lesson_fact_data:
            purposes = lesson_fact_data.get("best_move_purpose_candidates") or []
            if purposes:
                purpose = str(purposes[0]).strip().rstrip(".")
                return f"{better} was safer because it {purpose}."

        return f"{better} was safer because it dealt with the urgent threat first."

    def _played_move_context(lesson_fact_data: dict[str, Any] | None) -> str:
        if not lesson_fact_data:
            return f"{played} looks natural, but it overlooks a more urgent problem."

        intent = str(lesson_fact_data.get("played_move_intent_heuristic") or "unknown")
        if intent != "unknown":
            return f"{played} looks natural because it {intent}, but it overlooks a more urgent problem."
        return f"{played} looks natural, but it overlooks a more urgent problem."

    # Use lesson_facts for richer deterministic fallback
    if lesson_facts:
        cr_san = lesson_facts.get("critical_reply_san")
        cr_type = lesson_facts.get("critical_reply_type")
        transfer_seed = str(lesson_facts.get("transfer_rule_seed") or "")
        context = _played_move_context(lesson_facts)
        best_sentence = _best_move_purpose_sentence(lesson_facts)

        if cr_san and cr_type == "check":
            first = f"{context} It allows {cr_san} with check and gives your opponent the initiative."
            second = best_sentence
            third = _transfer_sentence(transfer_seed)
            return f"{first} {second} {third}"

        if cr_san and cr_type == "capture":
            first = f"{context} It allows {cr_san}, and your opponent can win material right away."
            second = best_sentence
            third = _transfer_sentence(transfer_seed)
            return f"{first} {second} {third}"

        if cr_san:
            first = f"{context} It allows the immediate reply {cr_san}."
            second = best_sentence
            third = _transfer_sentence(transfer_seed)
            return f"{first} {second} {third}"

        consequence = _humanize_consequence(str(lesson_facts.get("immediate_consequence_summary") or ""))
        if consequence:
            first = f"{context} {consequence}"
        else:
            first = context
        second = best_sentence
        third = _transfer_sentence(transfer_seed)
        return f"{first} {second} {third}"

    # Original fallback without lesson_facts
    first_refutation = next((move for move in (refutation_line_san or []) if move), None)
    has_tactic = bool(tactical_pattern and tactical_pattern.lower() != "none")

    if first_refutation:
        first = (
            f"{played} looks natural, but it allows the immediate reply {first_refutation} before the main problem is solved."
        )
    else:
        first = (
            f"{played} looks natural, but it does not solve the most urgent practical problem in the position."
        )

    if has_tactic:
        second = (
            f"The key tactical idea is {tactical_pattern}, so the better habit is to look for forcing moves before choosing a natural-looking move."
        )
    elif best_move_san:
        second = (
            f"{better} was safer because it dealt with the immediate threat before slower improvements."
        )
    elif game_phase and game_phase.lower() == "endgame":
        second = (
            "In an endgame, the safer habit is to solve the immediate move-order problem before making a general improvement."
        )
    else:
        second = (
            "The safer practical habit is to solve the immediate issue before making a general improvement."
        )

    third = (
        "When you see a natural-looking move, first check whether the opponent has a forcing reply with check, capture, threat, tempo gain, or an attack on a loose piece."
    )
    return f"{first} {second} {third}"


def explain_training_lesson(
    *,
    fen: str,
    player_color: str,
    blunder_san: str,
    blunder_uci: str,
    best_move_san: str | None,
    best_move_uci: str | None,
    eval_before_display: str,
    eval_after_display: str,
    cp_loss: int,
    game_phase: str | None,
    tactical_pattern: str | None,
    tactical_reason: str | None,
    best_line: list[str] | None,
    refutation_line_san: list[str] | None,
    reference_texts: list[str] | None = None,
) -> str | None:
    if not _enabled():
        return None

    # Compute structured lesson facts
    lesson_facts: dict[str, Any] | None = None
    try:
        board_before = chess.Board(fen)
        blunder_move = chess.Move.from_uci(blunder_uci)
        best_move: chess.Move | None = None
        if best_move_uci:
            best_move = chess.Move.from_uci(best_move_uci)
        lesson_facts = build_lesson_facts(
            board_before=board_before,
            blunder_move=blunder_move,
            best_move=best_move,
            best_line=best_line,
            refutation_line=refutation_line_san,
            game_phase=game_phase,
            tactical_pattern=tactical_pattern,
            tactical_reason=tactical_reason,
        )
    except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError, AssertionError):
        # If FEN or UCI is invalid/illegal, proceed without lesson facts
        pass

    allowed_squares = _allowed_square_references(
        blunder_san=blunder_san,
        blunder_uci=blunder_uci,
        best_move_san=best_move_san,
        best_move_uci=best_move_uci,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
    )
    square_piece_hints = _build_square_piece_hints(
        blunder_san=blunder_san,
        best_move_san=best_move_san,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
    )

    prompt = _build_prompt(
        fen=fen,
        player_color=player_color,
        blunder_san=blunder_san,
        blunder_uci=blunder_uci,
        best_move_san=best_move_san,
        best_move_uci=best_move_uci,
        eval_before_display=eval_before_display,
        eval_after_display=eval_after_display,
        cp_loss=cp_loss,
        game_phase=game_phase,
        tactical_pattern=tactical_pattern,
        tactical_reason=tactical_reason,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
    )
    primary_text = _cached_generate(fen, blunder_uci, prompt)
    primary_quality = _score_explanation(
        primary_text,
        allowed_squares=allowed_squares,
        square_piece_hints=square_piece_hints,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        reference_texts=reference_texts,
        lesson_facts=lesson_facts,
    )
    if primary_quality.accepted:
        return primary_quality.text
    if not primary_quality.retryable:
        fallback = _fallback_explanation(
            blunder_san=blunder_san,
            best_move_san=best_move_san,
            refutation_line_san=refutation_line_san,
            tactical_pattern=tactical_pattern,
            game_phase=game_phase,
            lesson_facts=lesson_facts,
        )
        fallback_quality = _score_explanation(
            fallback,
            allowed_squares=allowed_squares,
            square_piece_hints=square_piece_hints,
            best_line=best_line,
            refutation_line_san=refutation_line_san,
            reference_texts=reference_texts,
            lesson_facts=lesson_facts,
            is_fallback=True,
        )
        return fallback_quality.text if fallback_quality.accepted else None

    retry_prompt = _build_retry_prompt(
        fen=fen,
        player_color=player_color,
        blunder_san=blunder_san,
        blunder_uci=blunder_uci,
        best_move_san=best_move_san,
        best_move_uci=best_move_uci,
        eval_before_display=eval_before_display,
        eval_after_display=eval_after_display,
        cp_loss=cp_loss,
        game_phase=game_phase,
        tactical_pattern=tactical_pattern,
        tactical_reason=tactical_reason,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        lesson_facts=lesson_facts,
        rejection_reasons=primary_quality.reasons,
    )
    try:
        retry_text = _cached_generate(fen, blunder_uci, retry_prompt, retry=True)
    except TypeError as exc:
        if "retry" not in str(exc):
            raise
        # Compatibility with monkeypatched test doubles that still accept 3 args.
        retry_text = _cached_generate(fen, blunder_uci, retry_prompt)
    retry_quality = _score_explanation(
        retry_text,
        allowed_squares=allowed_squares,
        square_piece_hints=square_piece_hints,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        reference_texts=reference_texts,
        lesson_facts=lesson_facts,
    )
    if retry_quality.accepted:
        return retry_quality.text

    fallback = _fallback_explanation(
        blunder_san=blunder_san,
        best_move_san=best_move_san,
        refutation_line_san=refutation_line_san,
        tactical_pattern=tactical_pattern,
        game_phase=game_phase,
        lesson_facts=lesson_facts,
    )
    fallback_quality = _score_explanation(
        fallback,
        allowed_squares=allowed_squares,
        square_piece_hints=square_piece_hints,
        best_line=best_line,
        refutation_line_san=refutation_line_san,
        reference_texts=reference_texts,
        lesson_facts=lesson_facts,
        is_fallback=True,
    )
    if fallback_quality.accepted:
        return fallback_quality.text
    return None


def explain_with_configured_llm(
    *,
    fen: str,
    blunder_uci: str,
    best_move_uci: str | None,
    cp_loss: int,
    best_line: list[str],
    refutation_line: list[str],
) -> str | None:
    """Backward-compatible wrapper for older call sites.

    New code should call explain_training_lesson(...), because that function receives
    richer facts and produces more useful learning-oriented explanations.
    """
    if not _enabled():
        return None

    pawn_loss = _cp_loss_as_pawns(cp_loss)
    return _cached_generate(
        fen,
        blunder_uci,
        "Write a short English chess-training coach note from these engine facts.\n"
        f"Played move: {blunder_uci}\n"
        f"Evaluation loss: {cp_loss} centipawns, about {pawn_loss} pawns\n"
        f"Better move: {best_move_uci or 'not available'}\n"
        f"Better line: {_format_line(best_line)}\n"
        f"Refutation line: {_format_line(refutation_line)}\n\n"
        "Output contract:\n"
        "- 2 to 4 concise sentences in one plain paragraph.\n"
        "- Include one transfer sentence starting with 'When you see' or 'If you see'.\n"
        "- Do not invent moves or tactics. Do not use filler."
    )
