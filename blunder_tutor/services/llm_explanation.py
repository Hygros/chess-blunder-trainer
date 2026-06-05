from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
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

# Bump this when the coach prompt/output contract changes materially.
LLM_EXPLANATION_VERSION = 7

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
    "next time",
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
        return "This is a major evaluation swing, not a small inaccuracy."
    if cp_loss >= 300:
        return "This is a serious mistake with a clear practical impact."
    if cp_loss >= 150:
        return "This is a meaningful mistake that changes the evaluation noticeably."
    return "This is a smaller but still relevant evaluation loss."


def _refutation_hint(refutation_line_san: list[str] | None) -> str:
    if not refutation_line_san:
        return "No concrete engine refutation line is available. Do not invent one."
    if len(refutation_line_san) == 1:
        return "The engine gives only the first opponent resource. Explain the limitation instead of inventing a full continuation."
    if len(refutation_line_san) <= 3:
        return "The refutation line is short. Explain the first opponent resource and the general practical point."
    return "Use the first move as the main refutation and summarize the rest of the line as the resulting continuation."


def _tactical_hint(tactical_pattern: str | None, tactical_reason: str | None) -> str:
    if tactical_pattern and tactical_pattern.lower() != "none":
        reason = tactical_reason or "No additional tactic reason was provided."
        return f"The classifier labels this as {tactical_pattern}. Reason: {reason}"
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
        "Do not repeat the visible move lists. "
        "Explain the human learning point: "
        "1. Explain the critical reply first if available. "
        "2. Explain why the played move failed in terms of priority, forcing move, check, capture, material, king safety, or loose piece. "
        "3. Explain why the best move solves or reduces the immediate problem, but only if best_move_purpose_candidates support that. "
        "4. End with one specific transfer sentence starting with 'When you see' or 'If you see'. "
        "The transfer sentence must mention a concrete check such as forcing reply, check, capture, threat, loose piece, tempo, king safety, coordination, or move order. "
        "Output 2 to 4 concise sentences as one plain paragraph. "
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
    pawn_loss = _cp_loss_as_pawns(cp_loss)
    best_line_text = _format_line(best_line)
    refutation_line_text = _format_line(refutation_line_san)
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
        "Engine facts you may use:\n"
        f"- Side that made the mistake: {player_color}\n"
        f"- Position FEN: {fen}\n"
        f"- Played move: {blunder_san or 'not available'} ({blunder_uci})\n"
        f"- Evaluation before the move: {eval_before_display}\n"
        f"- Evaluation after the move: {eval_after_display}\n"
        f"- Evaluation loss: {cp_loss} centipawns, about {pawn_loss} pawns\n"
        f"- Better move: {best_move_san or 'not available'} ({best_move_uci or 'not available'})\n"
        f"- Better engine line: {best_line_text}\n"
        f"- Opponent refutation line after the played move: {refutation_line_text}\n"
        "\n"
        f"{lesson_facts_block}"
        "Safe learning hints:\n"
        f"- {_severity_hint(cp_loss)}\n"
        f"- {_refutation_hint(refutation_line_san)}\n"
        f"- {_tactical_hint(tactical_pattern, tactical_reason)}\n"
        f"- {_phase_hint(game_phase)}\n"
        f"- Learning focus: {learning_focus}\n\n"
        "Reasoning order to use internally, but do not output headings:\n"
        "1. Consequence: what changed after the blunder?\n"
        "2. Cause: what decision error likely caused it?\n"
        "3. Better idea: what practical problem does the better move solve?\n"
        "4. Transfer rule: what should the learner check next time?\n\n"
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
        "- Do not say 'significant advantage', 'strong counterattack', or 'king safety' without concrete supporting facts.\n\n"
        f"{strict_block}"
        "Output contract:\n"
        "- 2 to 4 concise sentences in one paragraph.\n"
        "- Include one specific transfer sentence starting with 'When you see' or 'If you see'.\n"
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
    lowered = [sentence.lower() for sentence in sentences]
    return any(marker in sentence for sentence in lowered for marker in TRANSFER_RULE_MARKERS)


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

    sentences = _extract_sentences(cleaned)
    if len(sentences) < MIN_SENTENCES:
        return None

    if len(sentences) > MAX_SENTENCES:
        sentences = sentences[:MAX_SENTENCES]

    if not _has_transfer_rule(sentences):
        return None

    if _has_generic_transfer_rule(sentences):
        return None

    cleaned = _trim_to_limit(" ".join(sentences), MAX_RESPONSE_CHARS)
    if not _has_learning_concept(cleaned):
        return None

    return cleaned or None


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


def _ollama_generate(prompt: str) -> str | None:
    payload = {
        "model": _model(),
        "system": _system_prompt(),
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "top_p": 0.75,
            "num_predict": 200,
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

    return _clean_response(str(body.get("response", "")))


def _groq_generate(prompt: str) -> str | None:
    api_key = _api_key()
    if not api_key:
        return None

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
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

    return _clean_response(str(text))


def _cache_key(fen: str, blunder_uci: str, prompt: str) -> str:
    prompt_digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    return f"{fen}|{blunder_uci}|{_model()}|{prompt_digest}"


def _cached_generate(fen: str, blunder_uci: str, prompt: str) -> str | None:
    key = _cache_key(fen, blunder_uci, prompt)
    cached = _explanation_cache.get(key)
    if cached is not None:
        return cached

    provider = _provider()
    if provider == "ollama":
        result = _ollama_generate(prompt)
    elif provider == "groq":
        result = _groq_generate(prompt)
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

    # Use lesson_facts for richer deterministic fallback
    if lesson_facts:
        cr_san = lesson_facts.get("critical_reply_san")
        cr_type = lesson_facts.get("critical_reply_type")

        if cr_san and cr_type == "check":
            first = (
                f"{played} is a priority error because it allows the immediate forcing check {cr_san}."
            )
            second = (
                f"{better} was preferred because it addresses the immediate problem before slower improvements."
                if best_move_san
                else "The safer habit is to solve the immediate check threat before making a general improvement."
            )
            third = (
                "When you see a quiet move, first check whether the opponent has a forcing reply with check, capture, or threat."
            )
            return f"{first} {second} {third}"

        if cr_san and cr_type == "capture":
            first = (
                f"{played} allows {cr_san}, so the opponent can win material before the player solves the main problem."
            )
            second = (
                f"{better} was preferred because it avoids that immediate material loss or keeps the position coordinated."
                if best_move_san
                else "The safer habit is to avoid leaving material loose before making a general improvement."
            )
            third = (
                "When you see a natural move, first check whether any piece becomes loose or tactically vulnerable."
            )
            return f"{first} {second} {third}"

        if cr_san:
            first = (
                f"{played} is a priority error because it allows the opponent's immediate resource {cr_san}."
            )
            second = (
                f"{better} was preferred because it keeps the position more coordinated and addresses the immediate issue."
                if best_move_san
                else "The safer habit is to solve the immediate problem before making a general improvement."
            )
            third = (
                "When you see a quiet move, first ask what forcing reply the opponent would have if you passed the turn."
            )
            return f"{first} {second} {third}"

    # Original fallback without lesson_facts
    first_refutation = next((move for move in (refutation_line_san or []) if move), None)
    has_tactic = bool(tactical_pattern and tactical_pattern.lower() != "none")

    if first_refutation:
        first = (
            f"{played} is a priority error because it allows the opponent's immediate resource {first_refutation} before the main problem is solved."
        )
    else:
        first = (
            f"{played} is a priority error because it does not clearly solve the most urgent practical problem in the position."
        )

    if has_tactic:
        second = (
            f"The classified motif is {tactical_pattern}, so the better practical habit is to look for the forcing resource before choosing a natural-looking move."
        )
    elif best_move_san:
        second = (
            f"{better} was preferred because it keeps the position more coordinated and addresses the immediate issue before slower improvements."
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

    def is_acceptable(candidate: str | None) -> bool:
        if candidate is None:
            return False
        if _is_too_similar(candidate, reference_texts):
            return False
        if _has_unsupported_square_reference(candidate, allowed_squares):
            return False
        if _has_conflicting_piece_square_claim(candidate, square_piece_hints):
            return False
        return True

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
    if is_acceptable(primary_text):
        return primary_text

    retry_prompt = _build_prompt(
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
    retry_text = _cached_generate(fen, blunder_uci, retry_prompt)
    if is_acceptable(retry_text):
        return retry_text

    fallback = _fallback_explanation(
        blunder_san=blunder_san,
        best_move_san=best_move_san,
        refutation_line_san=refutation_line_san,
        tactical_pattern=tactical_pattern,
        game_phase=game_phase,
        lesson_facts=lesson_facts,
    )
    if is_acceptable(fallback):
        return fallback
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
