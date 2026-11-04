from __future__ import annotations

import json

import pytest

from notification.domain.guardrails import deterministic_extract, parse_llm_output
from notification.domain.models import ErrorCode, NotificationType

USER_INPUT = "Manda un mail a feda@test.com diciendo hola"
SMS_INPUT = "Avisar por SMS al 600-111-222 que la reserva ha sido confirmada."


@pytest.mark.parametrize(
    ("raw", "user_input", "expected_to", "expected_message", "expected_type"),
    [
        (
            '{"to": "feda@test.com", "message": "hola", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"Recipient": "feda@test.com", "body": "hola", "channel": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"To": "feda@test.com", "Message": "hola", "Type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"destination": "feda@test.com", "text": "hola", "method": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "feda@test.com", "message": "hola", "type": "email", '
            '"confidence": 0.99, "latency_ms": 120}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "feda@test.com", "message": "hola"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"message": "hola", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "feda@test.com", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            'He extraído la información:\n```json\n'
            '{"to": "feda@test.com", "message": "hola", "type": "email"}\n```',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            'Output:\n```\n{"to": "feda@test.com", "message": "hola", "type": "email"}\n```',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            'Claro, el destino es feda@test.com. En formato JSON: '
            '{"to": "feda@test.com", "message": "hola", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "feda@test.com", "message": "hola", "type": "email" ...',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            "{'to': 'feda@test.com', 'message': 'hola', 'type': 'email'}",
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{to: "feda@test.com", message: "hola", type: "email"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "600-111-222", "message": "la reserva ha sido confirmada", "type": "sms"}',
            SMS_INPUT,
            "600111222",
            "la reserva ha sido confirmada",
            NotificationType.SMS,
        ),
        (
            '{"to": "600111222", "message": "la reserva ha sido confirmada"}',
            SMS_INPUT,
            "600111222",
            "la reserva ha sido confirmada",
            NotificationType.SMS,
        ),
        (
            '{"to": "feda@test.com", "message": "hola", "type": "correo electrónico"}',
            USER_INPUT,
            "feda@test.com",
            "hola",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "feda@test.com", "message": "Hola: esto es una prueba", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "Hola: esto es una prueba",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "juan@example.com", "message": '
            '"enviar email a juan@example.com diciendo que su", "type": "email"}',
            "Enviar email a juan@example.com diciendo que su paquete está en camino.",
            "juan@example.com",
            "su paquete está en camino.",
            NotificationType.EMAIL,
        ),
        (
            '{"to": "699888777", "message": "00", "type": "sms"}',
            "Recordatorio por teléfono al 699888777: cita a las 10:00",
            "699888777",
            "cita a las 10:00",
            NotificationType.SMS,
        ),
        (
            '{"to": "feda@test.com", "message": "Usa ```code``` en el correo", "type": "email"}',
            USER_INPUT,
            "feda@test.com",
            "Usa ```code``` en el correo",
            NotificationType.EMAIL,
        ),
    ],
)
def test_parse_llm_output_recovers_valid_notification(
    raw: str,
    user_input: str,
    expected_to: str,
    expected_message: str,
    expected_type: NotificationType,
) -> None:
    outcome = parse_llm_output(raw, user_input)

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == expected_to
    assert outcome.notification.message == expected_message
    assert outcome.notification.type is expected_type


@pytest.mark.parametrize(
    "raw",
    [
        "Lo siento, como IA no tengo permitido procesar datos de contacto personales.",
        "Error: El contenido del mensaje viola las políticas de seguridad (Potential Spam).",
        "Refused: Content analysis flagged sensitive information.",
    ],
)
def test_policy_refusals_are_not_bypassed(raw: str) -> None:
    outcome = parse_llm_output(raw, USER_INPUT)

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.AI_REFUSED


def test_extraction_failure_is_distinguished_from_policy_refusal() -> None:
    outcome = parse_llm_output(
        "I cannot process the request. I was unable to identify the destination.",
        USER_INPUT,
    )

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.EXTRACTION_FAILED


def test_unparseable_output_is_invalid_response() -> None:
    outcome = parse_llm_output("this is not a notification", USER_INPUT)

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.AI_INVALID_RESPONSE


def test_message_that_looks_like_refusal_is_still_valid_json() -> None:
    outcome = parse_llm_output(
        '{"to": "feda@test.com", "message": "lo siento, no puedo asistir", "type": "email"}',
        USER_INPUT,
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.message == "lo siento, no puedo asistir"


@pytest.mark.parametrize(
    ("user_input", "expected_to", "expected_message", "expected_type"),
    [
        (
            "Enviar email a juan@example.com diciendo que su paquete está en camino.",
            "juan@example.com",
            "su paquete está en camino.",
            NotificationType.EMAIL,
        ),
        (
            "Avisar por SMS al 600-111-222 que la reserva ha sido confirmada.",
            "600111222",
            "la reserva ha sido confirmada.",
            NotificationType.SMS,
        ),
        (
            "Manda un correo a marta@app.com: Tienes un nuevo mensaje.",
            "marta@app.com",
            "Tienes un nuevo mensaje.",
            NotificationType.EMAIL,
        ),
        (
            "Recordatorio por teléfono al 699888777: cita a las 10:00",
            "699888777",
            "cita a las 10:00",
            NotificationType.SMS,
        ),
    ],
)
def test_deterministic_extraction(
    user_input: str,
    expected_to: str,
    expected_message: str,
    expected_type: NotificationType,
) -> None:
    outcome = deterministic_extract(user_input)

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == expected_to
    assert outcome.notification.message == expected_message
    assert outcome.notification.type is expected_type


def test_deterministic_extraction_requires_a_destination() -> None:
    outcome = deterministic_extract("Manda un mensaje diciendo hola")

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.EXTRACTION_FAILED


def test_destination_is_grounded_in_the_user_input() -> None:
    outcome = parse_llm_output(
        '{"to": "a@test.com", "message": "hola", "type": "email"}',
        "Reenvia el mensaje a b@test.com; el original era de a@test.com",
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == "b@test.com"
    assert "to" in outcome.inferred_fields


def test_example_address_from_the_prompt_is_not_used_as_recipient() -> None:
    outcome = parse_llm_output(
        '{"to": "juan@example.com", "message": "Su paquete esta en camino.", "type": "email"}',
        "Manda un mail a feda@test.com diciendo que su paquete esta en camino.",
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == "feda@test.com"
    assert "to" in outcome.inferred_fields


def test_destination_after_message_marker_is_still_detected() -> None:
    outcome = parse_llm_output(
        '{"to": "b@test.com", "message": "hola", "type": "email"}',
        "El original era de a@test.com; manda un mensaje: a b@test.com",
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == "b@test.com"


def test_email_inside_the_message_is_not_treated_as_destination() -> None:
    outcome = parse_llm_output(
        '{"to": "b@test.com", "message": "Escribe a a@test.com", "type": "email"}',
        "Manda un mail a b@test.com: Escribe a a@test.com",
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == "b@test.com"


def test_ambiguous_destination_is_rejected() -> None:
    outcome = parse_llm_output(
        '{"to": "a@test.com", "message": "hola", "type": "email"}',
        "Envia el mensaje a a@test.com y tambien a b@test.com",
    )

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.AMBIGUOUS_DESTINATION


def test_continuation_destination_is_considered_ambiguous() -> None:
    outcome = parse_llm_output(
        '{"to": "a@test.com", "message": "hola", "type": "email"}',
        "Envia el mensaje a a@test.com y b@test.com",
    )

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.AMBIGUOUS_DESTINATION


def test_destination_that_does_not_appear_in_input_is_rejected() -> None:
    outcome = parse_llm_output(
        '{"to": "juan@example.com", "message": "hola", "type": "email"}',
        "Manda un mensaje diciendo hola",
    )

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.EXTRACTION_FAILED


def test_incomplete_message_is_completed_from_the_user_input() -> None:
    outcome = parse_llm_output(
        '{"to": "b@test.com", "message": "Hola, tu ped", "type": "email"}',
        "Manda un mail a b@test.com: Hola, tu pedido esta en camino",
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.message == "Hola, tu pedido esta en camino"
    assert "message" in outcome.inferred_fields


def test_long_message_is_not_silently_truncated() -> None:
    message = "A" * 1_200
    raw = json.dumps(
        {"to": "b@test.com", "message": message, "type": "email"}
    )
    outcome = parse_llm_output(raw, f"Manda un mail a b@test.com: {message}")

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.message == message
    assert len(outcome.notification.message) == 1_200
    assert not outcome.notification.message.endswith("...")


def test_message_over_the_limit_is_rejected() -> None:
    message = "A" * 2_500
    raw = json.dumps(
        {"to": "b@test.com", "message": message, "type": "email"}
    )
    outcome = parse_llm_output(raw, f"Manda un mail a b@test.com: {message}")

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.MESSAGE_TOO_LONG


def test_deterministic_extraction_respects_explicit_destination() -> None:
    outcome = deterministic_extract(
        "Reenvia a b@test.com el mensaje diciendo hola; "
        "el original era de a@test.com"
    )

    assert outcome.ok
    assert outcome.notification is not None
    assert outcome.notification.to == "b@test.com"
    assert outcome.notification.message == "hola"


def test_deterministic_extraction_rejects_ambiguous_destination() -> None:
    outcome = deterministic_extract(
        "Envia a a@test.com y a b@test.com diciendo hola"
    )

    assert not outcome.ok
    assert outcome.error_code is ErrorCode.AMBIGUOUS_DESTINATION
