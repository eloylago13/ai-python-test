from __future__ import annotations

import ast
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from .models import ErrorCode, Notification, NotificationType

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"(?![A-Za-z0-9.\-])"
)
_PHONE_RE = re.compile(r"(?<![\w+])(\+?\d[\d\s().\-]{5,}\d)(?![\w])")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_ELLIPSIS_RE = re.compile(r"(?:\.{3}|…)+\s*$")
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_DATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
_UNQUOTED_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")

_POLICY_REFUSAL_PATTERNS = (
    re.compile(r"\bno\s+tengo\s+permitido\b", re.IGNORECASE),
    re.compile(r"\blo\s+siento\b", re.IGNORECASE),
    re.compile(r"\bviola\s+las\s+pol[ií]ticas\b", re.IGNORECASE),
    re.compile(r"\bpotential\s+spam\b", re.IGNORECASE),
    re.compile(r"\brefused\b", re.IGNORECASE),
    re.compile(r"\bflagged\s+sensitive\b", re.IGNORECASE),
)
_EXTRACTION_FAILURE_PATTERNS = (
    re.compile(r"\bunable\s+to\s+identify\b", re.IGNORECASE),
    re.compile(r"\bcannot\s+process\b", re.IGNORECASE),
    re.compile(r"\bno\s+user\s+message\b", re.IGNORECASE),
    re.compile(r"\bno\s+puedo\s+procesar\b", re.IGNORECASE),
)

_EMAIL_WORDS = ("email", "e-mail", "correo", "mail")
_SMS_WORDS = ("sms", "texto", "mensaje de texto", "telefono", "phone", "movil")

_ALIASES_RAW: dict[str, Iterable[str]] = {
    "to": (
        "to",
        "recipient",
        "destination",
        "destino",
        "destinatario",
        "receiver",
        "target",
        "para",
        "email",
        "correo",
        "phone",
        "telefono",
        "number",
        "numero",
    ),
    "message": (
        "message",
        "body",
        "text",
        "content",
        "msg",
        "mensaje",
        "texto",
        "cuerpo",
        "contenido",
    ),
    "type": (
        "type",
        "channel",
        "method",
        "kind",
        "tipo",
        "canal",
        "metodo",
        "medio",
    ),
}

_EMAIL_TYPES = {
    "email",
    "mail",
    "correo",
    "correoelectronico",
    "gmail",
    "outlook",
}
_SMS_TYPES = {
    "sms",
    "text",
    "texto",
    "mensajedetexto",
    "telefono",
    "phone",
    "mobile",
    "movil",
}

_MAX_MESSAGE_CHARS = 2_000

_REASON_TO_ERROR = {
    "ambiguous_destination": ErrorCode.AMBIGUOUS_DESTINATION,
    "message_incomplete": ErrorCode.MESSAGE_INCOMPLETE,
    "message_too_long": ErrorCode.MESSAGE_TOO_LONG,
}


@dataclass(frozen=True, slots=True)
class _DestinationCandidate:
    value: str
    type: NotificationType
    start: int
    end: int
    explicit: bool


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    notification: Notification | None = None
    error_code: ErrorCode | None = None
    reason: str = ""
    source: str = "llm"
    repaired: bool = False
    inferred_fields: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.notification is not None


def parse_llm_output(raw: str, user_input: str) -> ExtractionOutcome:
    """Parse a stochastic LLM response into a validated notification.

    The parser is deliberately layered: try strict JSON, fenced JSON, embedded
    objects, Python literals, and finally a small repair pass for the malformed
    shapes we see in production. A valid object always wins over refusal
    detection, so a message that literally says "lo siento" is not rejected.
    """

    text = _normalize_text(raw or "")
    if not text:
        return ExtractionOutcome(
            error_code=ErrorCode.AI_INVALID_RESPONSE,
            reason="empty_response",
        )

    saw_mapping = False
    last_reason = "no_json_object"

    for candidate in _candidates(text):
        mapping, repaired = _load_mapping(candidate)
        if mapping is None:
            continue
        saw_mapping = True
        canonical = _canonicalize(mapping)
        if not canonical:
            last_reason = "no_recognized_fields"
            continue

        notification, inferred, reason = _build_notification(canonical, user_input)
        if notification is not None:
            return ExtractionOutcome(
                notification=notification,
                source="llm_repaired" if repaired else "llm",
                repaired=repaired,
                inferred_fields=inferred,
            )
        last_reason = reason

    if _matches_any(text, _POLICY_REFUSAL_PATTERNS):
        return ExtractionOutcome(
            error_code=ErrorCode.AI_REFUSED,
            reason="policy_refusal",
        )
    if _matches_any(text, _EXTRACTION_FAILURE_PATTERNS):
        return ExtractionOutcome(
            error_code=ErrorCode.EXTRACTION_FAILED,
            reason="model_could_not_extract",
        )
    if saw_mapping:
        return ExtractionOutcome(
            error_code=_error_for_reason(last_reason),
            reason=last_reason,
        )
    return ExtractionOutcome(
        error_code=ErrorCode.AI_INVALID_RESPONSE,
        reason=last_reason,
    )


def deterministic_extract(user_input: str) -> ExtractionOutcome:
    """Last-resort extractor used only when the AI response is unusable.

    It never overrides a policy refusal. It is intentionally conservative: it
    needs a destination explicitly present in the user input, and it derives the
    message from that input instead of inventing content.
    """

    text = _normalize_text(user_input)
    if not text:
        return _failure("empty_user_input", source="deterministic")

    guessed_type = _infer_type_from_text(text)
    destination, notification_type, reason = _resolve_requested_destination(
        text,
        guessed_type,
    )
    if destination is None:
        return _failure(reason, source="deterministic")

    message = _derive_message(text, destination)
    if not message:
        return _failure("missing_message", source="deterministic")
    if len(message) > _MAX_MESSAGE_CHARS:
        return _failure("message_too_long", source="deterministic")
    if _message_is_incomplete(message, text, None):
        return _failure("message_incomplete", source="deterministic")

    return ExtractionOutcome(
        notification=Notification(
            to=destination,
            message=message,
            type=notification_type,
        ),
        source="deterministic",
        inferred_fields=("to", "message", "type"),
    )


def _build_notification(
    canonical: dict[str, Any], user_input: str
) -> tuple[Notification | None, tuple[str, ...], str]:
    inferred: list[str] = []

    type_raw = _coerce_scalar(canonical.get("type"))
    notification_type = _normalize_type(type_raw)

    # The destination is resolved from the original request, never from the
    # model alone. This prevents a hallucinated address, or an example copied
    # from the system prompt, from becoming the recipient.
    destination, resolved_type, reason = _resolve_requested_destination(
        user_input,
        notification_type,
    )
    if destination is None:
        return None, tuple(inferred), reason

    if notification_type is None:
        notification_type = resolved_type
        inferred.append("type")
    elif notification_type is not resolved_type:
        notification_type = resolved_type
        inferred.append("type")

    llm_destination = _normalize_destination(
        _coerce_scalar(canonical.get("to")),
        notification_type,
    )
    if llm_destination is None or not _same_destination(
        llm_destination,
        destination,
        notification_type,
    ):
        inferred.append("to")

    message = _normalize_message(_coerce_scalar(canonical.get("message")))
    derived_message = _derive_message(user_input, destination)
    if derived_message and _should_prefer_derived(
        message, derived_message, user_input, destination
    ):
        if message != derived_message:
            inferred.append("message")
        message = derived_message

    if not message:
        message = derived_message or _normalize_message(user_input)
        if message:
            inferred.append("message")
    if not message:
        return None, tuple(inferred), "missing_message"

    if _message_is_incomplete(message, user_input, derived_message):
        if derived_message and message != derived_message:
            message = derived_message
            inferred.append("message")
        else:
            return None, tuple(inferred), "message_incomplete"

    if len(message) > _MAX_MESSAGE_CHARS:
        return None, tuple(inferred), "message_too_long"

    return (
        Notification(to=destination, message=message, type=notification_type),
        tuple(dict.fromkeys(inferred)),
        "",
    )


def _error_for_reason(reason: str) -> ErrorCode:
    return _REASON_TO_ERROR.get(reason, ErrorCode.EXTRACTION_FAILED)


def _failure(reason: str, *, source: str) -> ExtractionOutcome:
    return ExtractionOutcome(
        error_code=_error_for_reason(reason),
        reason=reason,
        source=source,
    )


def _destination_candidates(text: str) -> list[_DestinationCandidate]:
    candidates: list[_DestinationCandidate] = []

    for match in _EMAIL_RE.finditer(text):
        email = match.group(1).strip().strip(".,;:")
        if len(email) > 254:
            continue
        candidates.append(
            _DestinationCandidate(
                value=email,
                type=NotificationType.EMAIL,
                start=match.start(1),
                end=match.end(1),
                explicit=_is_explicit_destination(text, match.start(1)),
            )
        )

    for match in _PHONE_RE.finditer(text):
        raw = match.group(1).strip()
        if _DATE_RE.fullmatch(raw):
            continue
        phone = _normalize_phone(raw)
        if phone is None:
            continue
        candidates.append(
            _DestinationCandidate(
                value=phone,
                type=NotificationType.SMS,
                start=match.start(1),
                end=match.end(1),
                explicit=_is_explicit_destination(text, match.start(1)),
            )
        )

    boundary = _message_boundary(text, candidates)
    if boundary is not None:
        before_message = [
            candidate for candidate in candidates if candidate.start < boundary
        ]
        if before_message and any(
            candidate.explicit for candidate in before_message
        ):
            candidates = before_message

    deduped: dict[tuple[NotificationType, str], _DestinationCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.type,
            candidate.value.casefold()
            if candidate.type is NotificationType.EMAIL
            else candidate.value,
        )
        previous = deduped.get(key)
        if previous is None or (candidate.explicit and not previous.explicit):
            deduped[key] = candidate
    return list(deduped.values())


def _message_boundary(
    text: str,
    candidates: list[_DestinationCandidate],
) -> int | None:
    lower = text.lower()
    marker_indexes = [
        lower.find(marker)
        for marker in ("diciendo", "con el mensaje", "mensaje:", "aviso:", "texto:")
        if lower.find(marker) != -1
    ]
    if marker_indexes:
        return min(marker_indexes)

    colon_index = text.find(":")
    if colon_index != -1 and any(
        candidate.end <= colon_index for candidate in candidates
    ):
        return colon_index
    return None


def _is_explicit_destination(text: str, start: int) -> bool:
    prefix = text[max(0, start - 60) : start].lower()

    if re.search(r"\b(?:no|nunca)\s+(?:a|al|para|to)\s*$", prefix):
        return False
    if re.search(
        r"\b(?:de|desde|from|original|anterior|remitente|origen)\s*:?\s*$",
        prefix,
    ):
        return False
    if re.search(r"\b(?:destinatario|destino)\s*:?\s*$", prefix):
        return True
    if re.search(r"(?:,|;|/|\b(?:y|e|o|u))\s*$", prefix):
        return True
    return bool(
        re.search(
            r"\b(?:a|al|para|to)\s+(?:[a-z0-9]+\s+){0,3}$",
            prefix,
        )
    )


def _resolve_requested_destination(
    user_input: str,
    requested_type: NotificationType | None,
) -> tuple[str | None, NotificationType | None, str]:
    text = _normalize_text(user_input)
    candidates = _destination_candidates(text)
    if not candidates:
        return None, None, "missing_destination"

    if requested_type is not None:
        typed = [
            candidate
            for candidate in candidates
            if candidate.type is requested_type
        ]
        if typed:
            candidates = typed
        elif len(candidates) == 1:
            requested_type = candidates[0].type
        else:
            return None, None, "destination_type_mismatch"

    explicit = [candidate for candidate in candidates if candidate.explicit]
    if explicit:
        unique_explicit = {
            (
                candidate.type,
                candidate.value.casefold()
                if candidate.type is NotificationType.EMAIL
                else candidate.value,
            ): candidate
            for candidate in explicit
        }
        if len(unique_explicit) == 1:
            candidate = next(iter(unique_explicit.values()))
            return candidate.value, candidate.type, ""
        return None, None, "ambiguous_destination"

    unique = {
        (
            candidate.type,
            candidate.value.casefold()
            if candidate.type is NotificationType.EMAIL
            else candidate.value,
        ): candidate
        for candidate in candidates
    }
    if len(unique) == 1:
        candidate = next(iter(unique.values()))
        return candidate.value, candidate.type, ""
    return None, None, "ambiguous_destination"


def _same_destination(
    left: str,
    right: str,
    notification_type: NotificationType,
) -> bool:
    if notification_type is NotificationType.EMAIL:
        return left.casefold() == right.casefold()
    return left == right


def _message_is_incomplete(
    message: str,
    user_input: str,
    derived_message: str | None,
) -> bool:
    if not message:
        return True

    normalized_input = _normalize_message(user_input) or ""
    if (
        len(message) < len(normalized_input)
        and normalized_input.startswith(message)
    ):
        next_char = normalized_input[len(message)]
        if message[-1].isalnum() and next_char.isalnum():
            return True

    if (
        len(message) < len(normalized_input)
        and message.endswith(("...", "…"))
    ):
        return True

    if (
        derived_message
        and len(message) < len(derived_message)
        and derived_message.startswith(message)
    ):
        next_char = derived_message[len(message)]
        if message[-1].isalnum() and (
            next_char.isalnum() or len(derived_message) > len(message) + 1
        ):
            return True

    if (
        len(user_input) > 50
        and message == _normalize_message(user_input[:50])
    ):
        return True

    return False


def _candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in _FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    candidates.extend(_balanced_objects(text))
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _balanced_objects(text: str) -> list[str]:
    objects: list[str] = []
    start: int | None = None
    depth = 0
    quote: str | None = None
    escaped = False

    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            if depth > 0:
                quote = char
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start : index + 1])
                start = None

    if depth > 0 and start is not None:
        objects.append(text[start:])
    return objects


def _load_mapping(candidate: str) -> tuple[dict[str, Any] | None, bool]:
    text = _normalize_text(candidate).strip().strip("`").strip()
    if not text:
        return None, False

    repaired_text = _repair_json(text)
    attempts = [text]
    if repaired_text != text:
        attempts.append(repaired_text)

    for attempt in attempts:
        for loader in (_json_load, _literal_eval):
            try:
                value = loader(attempt)
            except (ValueError, SyntaxError, TypeError):
                continue
            if isinstance(value, dict):
                return value, repaired_text != text or attempt != text
    return None, False


def _json_load(value: str) -> Any:
    return json.loads(value)


def _literal_eval(value: str) -> Any:
    return ast.literal_eval(value)


def _repair_json(text: str) -> str:
    fixed = _normalize_text(text).strip().strip("`").strip()
    # Only unwrap a fence when the candidate itself is fenced. Applying the
    # regex unconditionally would corrupt a message that legitimately contains
    # triple backticks.
    if fixed.startswith("```"):
        fenced = _FENCE_RE.search(fixed)
        if fenced is not None:
            fixed = fenced.group(1).strip()
    fixed = _TRAILING_ELLIPSIS_RE.sub("", fixed).strip()
    fixed = _quote_unquoted_keys(fixed)
    fixed = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    fixed = _balance_json(fixed)
    fixed = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    return fixed


def _quote_unquoted_keys(text: str) -> str:
    """Quote object keys without touching characters inside strings."""

    output: list[str] = []
    index = 0
    in_string = False
    quote = ""
    escaped = False
    expect_key = False

    while index < len(text):
        char = text[index]

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue

        if char in "\"'":
            if expect_key:
                expect_key = False
            in_string = True
            quote = char
            output.append(char)
            index += 1
            continue

        if char == "{":
            expect_key = True
            output.append(char)
            index += 1
            continue

        if char in "[}]":
            expect_key = False
            output.append(char)
            index += 1
            continue

        if char == ",":
            expect_key = True
            output.append(char)
            index += 1
            continue

        if expect_key:
            if char.isspace():
                output.append(char)
                index += 1
                continue

            end = index
            while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
                end += 1
            colon = end
            while colon < len(text) and text[colon].isspace():
                colon += 1

            if end > index and colon < len(text) and text[colon] == ":":
                key = text[index:end]
                if _UNQUOTED_KEY_RE.match(key):
                    output.append(f'"{key}"')
                    index = end
                    expect_key = False
                    continue

            expect_key = False

        output.append(char)
        index += 1

    return "".join(output)


def _balance_json(text: str) -> str:
    output = list(text)
    depth = 0
    in_string = False
    quote = ""
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in "\"'":
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1

    if in_string:
        output.append(quote)
    if depth > 0:
        output.extend("}" * depth)
    return "".join(output)


def _canonicalize(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    aliases = {
        target: {_normalize_key(alias) for alias in values}
        for target, values in _ALIASES_RAW.items()
    }

    for key, value in data.items():
        normalized_key = _normalize_key(key)
        for target, target_aliases in aliases.items():
            if normalized_key in target_aliases and target not in canonical:
                canonical[target] = value

    if canonical or depth >= 3:
        return canonical

    for value in data.values():
        if isinstance(value, dict):
            nested = _canonicalize(value, depth + 1)
            if nested:
                return nested
    return {}


def _coerce_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "address", "email", "phone", "number", "text", "content"):
            if key in value:
                return value[key]
        for nested in value.values():
            if isinstance(nested, str):
                return nested
        return None
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _normalize_type(value: Any) -> NotificationType | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^a-z0-9]", "", _fold(value))
    if not compact:
        return None
    if compact in _EMAIL_TYPES:
        return NotificationType.EMAIL
    if compact in _SMS_TYPES:
        return NotificationType.SMS

    has_email = "email" in compact or "correo" in compact
    has_sms = "sms" in compact or "telefono" in compact
    if has_email and not has_sms:
        return NotificationType.EMAIL
    if has_sms and not has_email:
        return NotificationType.SMS
    return None


def _normalize_destination(
    value: Any, notification_type: NotificationType | None
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = _normalize_text(value)
    text = re.sub(r"^mailto:", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'<>[]() ").strip()

    if notification_type is NotificationType.EMAIL:
        return _find_email(text)
    if notification_type is NotificationType.SMS:
        return _find_phone(text)
    return _find_email(text) or _find_phone(text)


def _infer_type_from_text(
    text: str,
    email: str | None = None,
    phone: str | None = None,
) -> NotificationType | None:
    if email is None:
        email = _find_email(text)
    if phone is None:
        phone = _find_phone(text)

    folded = _fold(text)
    has_email_word = any(word in folded for word in _EMAIL_WORDS)
    has_sms_word = any(word in folded for word in _SMS_WORDS)

    if has_email_word and not has_sms_word:
        return NotificationType.EMAIL
    if has_sms_word and not has_email_word:
        return NotificationType.SMS
    if email and not phone:
        return NotificationType.EMAIL
    if phone and not email:
        return NotificationType.SMS
    return None


def _find_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _EMAIL_RE.search(_normalize_text(value))
    if not match:
        return None
    email = match.group(1).strip().strip(".,;:")
    if len(email) > 254:
        return None
    return email


def _find_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _normalize_text(value)
    for match in _PHONE_RE.finditer(text):
        raw = match.group(1).strip()
        if _DATE_RE.fullmatch(raw):
            continue
        normalized = _normalize_phone(raw)
        if normalized:
            return normalized
    return None


def _normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if not 7 <= len(digits) <= 15:
        return None
    return f"+{digits}" if raw.strip().startswith("+") else digits


def _normalize_message(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None

    text = _normalize_text(value)
    text = text.strip().strip("\"'").strip()
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        return None
    return text


def _should_prefer_derived(
    message: str | None,
    derived: str,
    user_input: str,
    destination: str,
) -> bool:
    """Prefer a clean deterministic message over a verbatim prompt fragment.

    Small or literal models often return the first 50 characters of the prompt,
    a suffix after the last colon (which breaks on "10:00"), or a value that
    still contains the destination. Those are technically valid strings but poor
    notifications. A clear, deterministic extraction wins in those cases; a
    genuinely well-formed model message is preserved.
    """

    if not message:
        return True
    if message == derived:
        return False
    if len(message) <= 3 and len(derived) > len(message):
        return True
    if derived.startswith(message) and len(derived) > len(message) + 1:
        return True
    if message.endswith(("...", "…")) and len(derived) > len(message):
        return True
    if message == _normalize_message(user_input[:50]):
        return True
    if destination and destination in message:
        return True

    folded_message = _fold(message)
    if folded_message.startswith(
        ("enviar ", "envia ", "mandar ", "manda ", "avisar ", "avisa ", "recordatorio ", "recordar ")
    ):
        return True
    return False


def _derive_message(
    user_input: str,
    destination: str | None = None,
) -> str | None:
    text = _normalize_text(user_input)
    if not text:
        return None

    lower = text.lower()
    for marker in ("diciendo", "con el mensaje", "mensaje:", "aviso:", "texto:"):
        index = lower.find(marker)
        if index == -1:
            continue
        candidate = text[index + len(marker) :].lstrip(" :,-")
        candidate = re.sub(r"^que\s+", "", candidate, flags=re.IGNORECASE)
        message = _clean_derived_message(candidate)
        if message:
            return message

    destination_end = -1
    if destination:
        if "@" in destination:
            index = lower.find(destination.casefold())
            if index != -1:
                destination_end = index + len(destination)
        else:
            for match in _PHONE_RE.finditer(text):
                if _normalize_phone(match.group(1)) == destination:
                    destination_end = match.end()
                    break
            if destination_end == -1:
                index = text.find(destination)
                if index != -1:
                    destination_end = index + len(destination)
    else:
        email = _find_email(text)
        phone = _find_phone(text)
        if email:
            destination_end = max(
                destination_end,
                lower.find(email.lower()) + len(email),
            )
        if phone:
            for match in _PHONE_RE.finditer(text):
                destination_end = max(destination_end, match.end())

    # Use the first colon after the selected destination, not the last one. The
    # latter would turn "cita a las 10:00" into "00".
    colon_index = text.find(":", destination_end if destination_end != -1 else 0)
    if colon_index != -1:
        after = text[colon_index + 1 :]
        if after.strip() and not re.fullmatch(r"\d{1,2}:\d{2}", after.strip()):
            message = _clean_derived_message(after)
            if message:
                return message

    if destination_end != -1:
        rest = text[destination_end:]
        match = re.search(r"\bque\s+(.+)", rest, re.IGNORECASE | re.DOTALL)
        if match:
            message = _clean_derived_message(match.group(1))
            if message:
                return message

        rest = rest.lstrip(" :,-")
        rest = re.sub(
            r"^(?:diciendo|que|con el mensaje|mensaje|aviso|texto)\s+",
            "",
            rest,
            flags=re.IGNORECASE,
        )
        message = _clean_derived_message(rest)
        if message:
            return message

    return None


def _clean_derived_message(value: str) -> str | None:
    """Remove meta clauses such as "el original era de otro@test.com"."""

    parts = re.split(r"\s*;\s*", value)
    if len(parts) > 1:
        kept = [parts[0]]
        for part in parts[1:]:
            folded = _fold(part)
            if (
                "original" in folded
                or "remitente" in folded
                or _find_email(part) is not None
                or _find_phone(part) is not None
            ):
                continue
            kept.append(part)
        value = "; ".join(kept)

    return _normalize_message(value)


def _normalize_key(key: Any) -> str:
    folded = _fold(str(key))
    return re.sub(r"[^a-z0-9]", "", folded)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value)
    replacements = {
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u00a0": " ",
        "\u200b": "",
        "\ufeff": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.replace("\x00", "").strip()


def _matches_any(text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)
