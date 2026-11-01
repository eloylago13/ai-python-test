from __future__ import annotations

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
