from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
MAX_RESPONSE_CHARS = 1200

# Bump this when the coach prompt/output contract changes materially.
LLM_EXPLANATION_VERSION = 5

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


def _system_prompt() -> str:
    return (
        "You are a chess coach writing a short English training note from pre-computed engine facts. "
        "The engine facts are the source of truth. Do not calculate or invent anything yourself. "
        "Do not invent moves, threats, tactics, captures, checks, pieces, or plans. "
        "Explain concretely what happens on the board: material gains/losses, passed pawns, mating threats, piece traps, etc. "
        "Never use filler phrases like 'as shown in the engine line', 'the engine evaluation shows', "
        "'a significant loss of advantage', 'it is essential to check', or 'takes advantage of the mistake'. "
        "Do not repeat evaluation numbers because the user already sees the eval bar. "
        "Output 2 to 4 concise sentences as one plain paragraph without headings or bullet points. "
        "Include one transfer sentence that starts with 'When you see' or 'If you see'. "
        "Do not refuse the task. Do not use Markdown, bullets, emojis, tables, or asterisks. "
        "The answer must be in English."
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
) -> str:
    pawn_loss = _cp_loss_as_pawns(cp_loss)
    best_line_text = _format_line(best_line)
    refutation_line_text = _format_line(refutation_line_san)
    strict_block = ""
    if strict_mode:
        strict_block = (
            "\nAnti-duplication mode:\n"
            "- Focus on decision process and practical checks, not on rephrasing factual labels.\n"
            "- Add one explicit transfer sentence that starts with 'When you see' or 'If you see'.\n"
            "- Avoid mirrored phrasing and avoid sentence templates.\n"
        )

    return (
        "Write a helpful English chess-training coach note.\n\n"
        "Engine facts:\n"
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
        "Safe learning hints:\n"
        f"- {_severity_hint(cp_loss)}\n"
        f"- {_refutation_hint(refutation_line_san)}\n"
        f"- {_tactical_hint(tactical_pattern, tactical_reason)}\n"
        f"- {_phase_hint(game_phase)}\n\n"
        "Strict rules:\n"
        "- Use only the facts and safe learning hints above.\n"
        "- Do not add moves that are not listed in the better line or refutation line.\n"
        "- If you mention a square (for example e4), that square must appear in the listed moves.\n"
        "- Do not claim a tactic unless the tactical label says so.\n"
        "- If the concrete reason is not visible from the facts, say what the engine data shows instead of guessing.\n"
        "- Do not just say 'evaluation got worse'. Explain what the opponent gains concretely (material, passed pawn, mating attack, piece trapped, etc.).\n"
        "- Do not repeat evaluation numbers or generic loss-of-advantage phrases.\n"
        "- Do not use filler like 'as shown in the engine line' or 'takes advantage of the mistake'.\n\n"
        f"{strict_block}"
        "Output contract:\n"
        "- 2 to 4 concise sentences in one paragraph.\n"
        "- Include one transfer sentence starting with 'When you see' or 'If you see'.\n"
        "- Explain the concrete board consequence, then the practical takeaway."
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

    sentences = _extract_sentences(cleaned)
    if len(sentences) < MIN_SENTENCES:
        return None

    if len(sentences) > MAX_SENTENCES:
        sentences = sentences[:MAX_SENTENCES]

    if not _has_transfer_rule(sentences):
        return None

    cleaned = _trim_to_limit(" ".join(sentences), MAX_RESPONSE_CHARS)

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
    )
    primary_text = _cached_generate(
        fen,
        blunder_uci,
        prompt,
    )
    if primary_text is None:
        return None

    if (
        not _is_too_similar(primary_text, reference_texts)
        and not _has_unsupported_square_reference(primary_text, allowed_squares)
        and not _has_conflicting_piece_square_claim(
            primary_text,
            square_piece_hints,
        )
    ):
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
    )
    retry_text = _cached_generate(fen, blunder_uci, retry_prompt)
    if retry_text is None:
        return None
    if _is_too_similar(retry_text, reference_texts):
        return None
    if _has_unsupported_square_reference(retry_text, allowed_squares):
        return None
    if _has_conflicting_piece_square_claim(retry_text, square_piece_hints):
        return None
    return retry_text


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
