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
            error_code=ErrorCode.EXTRACTION_FAILED,
            reason=last_reason,
        )
    return ExtractionOutcome(
        error_code=ErrorCode.AI_INVALID_RESPONSE,
        reason=last_reason,
    )


def deterministic_extract(user_input: str) -> ExtractionOutcome:
    """Last-resort extractor used only when the AI response is unusable.

    It never overrides a policy refusal. It is intentionally conservative: it
    needs a valid destination, and it derives the message from explicit markers
    in the request instead of inventing content.
    """

    text = _normalize_text(user_input)
    if not text:
        return ExtractionOutcome(
            error_code=ErrorCode.EXTRACTION_FAILED,
            reason="empty_user_input",
            source="deterministic",
        )

    email = _find_email(text)
    phone = _find_phone(text)
    notification_type = _infer_type_from_text(text, email, phone)
    if notification_type is NotificationType.EMAIL and email:
        destination = email
    elif notification_type is NotificationType.SMS and phone:
        destination = phone
    elif email and not phone:
        destination, notification_type = email, NotificationType.EMAIL
    elif phone and not email:
        destination, notification_type = phone, NotificationType.SMS
    else:
        return ExtractionOutcome(
            error_code=ErrorCode.EXTRACTION_FAILED,
            reason="missing_or_ambiguous_destination",
            source="deterministic",
        )

    message = _derive_message(text)
    if not message:
        return ExtractionOutcome(
            error_code=ErrorCode.EXTRACTION_FAILED,
            reason="missing_message",
            source="deterministic",
        )

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
    if notification_type is None:
        inferred_type = _infer_type_from_destination(
            _coerce_scalar(canonical.get("to"))
        ) or _infer_type_from_text(user_input)
        if inferred_type is not None:
            notification_type = inferred_type
            inferred.append("type")

    destination = _normalize_destination(
        _coerce_scalar(canonical.get("to")), notification_type
    )
    if not destination:
        destination, text_type = _destination_from_text(user_input)
        if destination:
            inferred.append("to")
            if text_type is not None and (
                notification_type is None or notification_type is not text_type
            ):
                notification_type = text_type
                inferred.append("type")

    if not destination:
        return None, tuple(inferred), "missing_or_invalid_destination"

    if notification_type is None:
        notification_type = _infer_type_from_destination(destination)
        if notification_type is None:
            return None, tuple(inferred), "missing_type"
        inferred.append("type")

    message = _normalize_message(_coerce_scalar(canonical.get("message")))
    derived_message = _derive_message(user_input)
    if derived_message and _should_prefer_derived(
        message, derived_message, user_input, destination
    ):
        if message != derived_message:
            inferred.append("message")
        message = derived_message
    if not message:
        message = _normalize_message(user_input)
        if message:
            inferred.append("message")
    if not message:
        return None, tuple(inferred), "missing_message"

    return (
        Notification(to=destination, message=message, type=notification_type),
        tuple(dict.fromkeys(inferred)),
        "",
    )


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


def _destination_from_text(
    text: str,
) -> tuple[str | None, NotificationType | None]:
    email = _find_email(text)
    phone = _find_phone(text)
    inferred = _infer_type_from_text(text, email, phone)

    if inferred is NotificationType.EMAIL and email:
        return email, NotificationType.EMAIL
    if inferred is NotificationType.SMS and phone:
        return phone, NotificationType.SMS
    if email and not phone:
        return email, NotificationType.EMAIL
    if phone and not email:
        return phone, NotificationType.SMS
    if email:
        return email, NotificationType.EMAIL
    if phone:
        return phone, NotificationType.SMS
    return None, None


def _infer_type_from_destination(value: Any) -> NotificationType | None:
    if not isinstance(value, str):
        return None
    if _find_email(value):
        return NotificationType.EMAIL
    if _find_phone(value):
        return NotificationType.SMS
    return None


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
    if len(text) > 1000:
        text = text[:997].rstrip() + "..."
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


def _derive_message(user_input: str) -> str | None:
    text = _normalize_text(user_input)
    if not text:
        return None

    email = _find_email(text)
    phone = _find_phone(text)
    lower = text.lower()

    for marker in ("diciendo", "con el mensaje", "mensaje:", "aviso:", "texto:"):
        index = lower.find(marker)
        if index == -1:
            continue
        candidate = text[index + len(marker) :].lstrip(" :,-")
        candidate = re.sub(r"^que\s+", "", candidate, flags=re.IGNORECASE)
        message = _normalize_message(candidate)
        if message:
            return message

    destination_end = -1
    if email:
        destination_end = max(destination_end, lower.find(email.lower()) + len(email))
    if phone:
        for match in _PHONE_RE.finditer(text):
            destination_end = max(destination_end, match.end())

    # Use the first colon after the destination, not the last one. The latter
    # would turn "cita a las 10:00" into "00".
    colon_index = text.find(":", destination_end if destination_end != -1 else 0)
    if colon_index != -1:
        after = text[colon_index + 1 :]
        if after.strip() and not re.fullmatch(r"\d{1,2}:\d{2}", after.strip()):
            message = _normalize_message(after)
            if message:
                return message

    if destination_end != -1:
        rest = text[destination_end:]
        match = re.search(r"\bque\s+(.+)", rest, re.IGNORECASE | re.DOTALL)
        if match:
            message = _normalize_message(match.group(1))
            if message:
                return message

    return None


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
