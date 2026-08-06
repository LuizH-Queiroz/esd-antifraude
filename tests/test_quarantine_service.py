"""Testes do Quarantine Service usando o contrato do Admin Panel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from quarantine.main import create_app
from quarantine.broker import InMemoryBroker
from quarantine.repository import InMemoryRepository


def make_client(repository=None, broker=None):
    repository = repository or InMemoryRepository()
    broker = broker or InMemoryBroker()
    app = create_app(repository=repository, broker=broker)
    from fastapi.testclient import TestClient

    return TestClient(app), repository, broker


def test_health_responde_ok() -> None:
    client, _, _ = make_client()
    with client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "quarantine-service"}


def test_aplica_quarentena_e_publica_evento() -> None:
    repository = InMemoryRepository()
    broker = InMemoryBroker()
    client, _, _ = make_client(repository=repository, broker=broker)

    with client:
        payload = {
            "event_id": "score-1",
            "event_type": "ScoreAltoRisco",
            "occurred_at": "2026-08-04T18:00:00Z",
            "account_id": "C1",
            "risk_score": 0.91,
        }
        response = client.post("/internal/quarantine", json=payload)

    assert response.status_code == 202
    assert repository.get("C1").status == "EM_QUARENTENA"
    assert len(broker.published) == 1
    assert broker.published[0]["event_type"] == "ContaEmQuarentena"
    assert broker.published[0]["account_id"] == "C1"


def test_liberacao_publica_conta_liberada() -> None:
    repository = InMemoryRepository()
    broker = InMemoryBroker()
    client, _, _ = make_client(repository=repository, broker=broker)

    with client:
        client.post(
            "/internal/quarantine",
            json={
                "event_id": "score-1",
                "event_type": "ScoreAltoRisco",
                "occurred_at": "2026-08-04T18:00:00Z",
                "account_id": "C1",
                "risk_score": 0.91,
            },
        )
        response = client.post(
            "/internal/release",
            json={
                "account_id": "C1",
                "requested_by": "admin",
                "motivo": "Revisão manual",
            },
        )

    assert response.status_code == 202
    assert repository.get("C1").status == "LIBERADA"
    assert broker.published[-1]["event_type"] == "ContaLiberada"
    assert broker.published[-1]["released_by"] == "quarantine-service"


def test_comando_de_liberacao_e_consumido() -> None:
    repository = InMemoryRepository()
    broker = InMemoryBroker()
    client, _, _ = make_client(repository=repository, broker=broker)

    with client:
        response = client.post(
            "/internal/commands",
            json={
                "event_id": "command-1",
                "event_type": "ComandoDeLiberacao",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "account_id": "C1",
                "requested_by": "admin",
                "motivo": "Revisão manual",
            },
        )

    assert response.status_code == 202
    assert repository.get("C1").status == "LIBERADA"
    assert broker.published[-1]["event_type"] == "ContaLiberada"
    assert broker.published[-1]["account_id"] == "C1"


def test_processamento_de_comando_e_idempotente() -> None:
    repository = InMemoryRepository()
    repository.set_quarantined("C1", 0.91, "score alto")

    first_result = repository.process_release_command(
        {
            "event_id": "command-1",
            "event_type": "ComandoDeLiberacao",
            "account_id": "C1",
            "requested_by": "admin",
            "motivo": "Revisão manual",
        }
    )
    second_result = repository.process_release_command(
        {
            "event_id": "command-1",
            "event_type": "ComandoDeLiberacao",
            "account_id": "C1",
            "requested_by": "admin",
            "motivo": "Revisão manual",
        }
    )

    assert first_result is True
    assert second_result is False
    assert repository.get("C1").status == "LIBERADA"
