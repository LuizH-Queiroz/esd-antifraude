"""Testes automatizados do Admin Panel Service sem infraestrutura real."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from admin_panel.broker import InMemoryMessageBroker
from admin_panel.main import create_app
from admin_panel.repository import InMemoryCaseRepository, PostgresCaseRepository
from fastapi.testclient import TestClient

QUARANTINED_EVENT = {
    "event_id": "q-1",
    "event_type": "ContaEmQuarentena",
    "occurred_at": "2026-08-04T18:00:00Z",
    "account_id": "C1",
    "risk_score": 0.91,
    "motivo": "Padrão suspeito",
}

RELEASED_EVENT = {
    "event_id": "l-1",
    "event_type": "ContaLiberada",
    "occurred_at": "2026-08-04T18:20:00Z",
    "account_id": "C1",
    "released_by": "quarantine-service",
}


class FailingBroker(InMemoryMessageBroker):
    async def publish_release_command(self, command) -> None:
        raise ConnectionError("RabbitMQ indisponível (simulado).")


def _client(repository=None, broker=None):
    repository = repository or InMemoryCaseRepository()
    broker = broker or InMemoryMessageBroker()
    return TestClient(create_app(repository, broker)), repository, broker


def test_health_responde_ok() -> None:
    client, _, _ = _client()
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "admin-panel-service"}


def test_evento_de_quarentena_cria_caso_consultavel() -> None:
    client, _, broker = _client()
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        response = client.get("/cases")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["account_id"] == "C1"
    assert body["items"][0]["status"] == "EM_QUARENTENA"
    assert body["items"][0]["risk_score"] == 0.91


def test_detalhe_exibe_historico() -> None:
    client, _, broker = _client()
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        response = client.get("/cases/C1")

    assert response.status_code == 200
    body = response.json()
    assert body["motivo"] == "Padrão suspeito"
    assert [event["tipo"] for event in body["eventos"]] == ["ContaEmQuarentena"]


def test_detalhe_de_caso_inexistente_retorna_404() -> None:
    client, _, _ = _client()
    with client:
        response = client.get("/cases/NAO-EXISTE")

    assert response.status_code == 404


def test_filtros_e_paginacao() -> None:
    client, _, broker = _client()
    second_event = {
        **QUARANTINED_EVENT,
        "event_id": "q-2",
        "account_id": "C2",
        "risk_score": 0.70,
        "occurred_at": "2026-08-04T19:00:00Z",
    }
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        asyncio.run(broker.deliver(second_event))
        response = client.get("/cases?min_score=0.8&page=1&page_size=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["account_id"] == "C1"


def test_release_publica_comando_e_mantem_estado_ate_confirmacao() -> None:
    client, _, broker = _client()
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        response = client.post(
            "/cases/C1/release",
            headers={"Idempotency-Key": "command-1"},
            json={"requested_by": "reuben", "motivo": "Revisão concluída"},
        )
        detail_before = client.get("/cases/C1")
        asyncio.run(broker.deliver(RELEASED_EVENT))
        detail_after = client.get("/cases/C1")

    assert response.status_code == 202
    assert response.json()["command_id"] == "command-1"
    assert len(broker.published) == 1
    assert broker.published[0].to_message()["event_type"] == "ComandoDeLiberacao"
    assert detail_before.json()["status"] == "EM_QUARENTENA"
    assert [event["tipo"] for event in detail_before.json()["eventos"]] == [
        "ContaEmQuarentena",
        "ComandoDeLiberacao",
    ]
    assert detail_after.json()["status"] == "LIBERADA"


def test_release_de_conta_inexistente_retorna_404() -> None:
    client, _, _ = _client()
    with client:
        response = client.post("/cases/NAO-EXISTE/release")
    assert response.status_code == 404


def test_release_de_conta_liberada_retorna_409() -> None:
    client, _, broker = _client()
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        asyncio.run(broker.deliver(RELEASED_EVENT))
        response = client.post("/cases/C1/release")
    assert response.status_code == 409


def test_falha_no_broker_retorna_503_mas_auditoria_permanece() -> None:
    repository = InMemoryCaseRepository()
    broker = FailingBroker()
    client, _, _ = _client(repository, broker)

    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        response = client.post(
            "/cases/C1/release",
            headers={"Idempotency-Key": "command-fail"},
        )
        detail = client.get("/cases/C1")

    assert response.status_code == 503
    assert "command-fail" in repository.events
    assert "ComandoDeLiberacao" in [event["tipo"] for event in detail.json()["eventos"]]


def test_evento_duplicado_nao_duplica_historico() -> None:
    client, _, broker = _client()
    with client:
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        asyncio.run(broker.deliver(QUARANTINED_EVENT))
        detail = client.get("/cases/C1")

    assert len(detail.json()["eventos"]) == 1


def test_intervalo_de_datas_invalido_retorna_422() -> None:
    client, _, _ = _client()
    date_from = datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat()
    date_to = datetime(2026, 8, 4, tzinfo=timezone.utc).isoformat()
    with client:
        response = client.get(f"/cases?date_from={date_from}&date_to={date_to}")
    assert response.status_code == 422


def test_evento_do_historico_aceita_payload_json_string() -> None:
    row = {
        "event_id": "event-1",
        "event_type": "ComandoDeLiberacao",
        "payload": '{"requested_by":"ana","motivo":"ok","score":0.9}',
        "occurred_at": datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
    }

    event = PostgresCaseRepository._audit_event_from_row(row)

    assert event.actor == "ana"
    assert event.motivo == "ok"
    assert event.payload == {
        "requested_by": "ana",
        "motivo": "ok",
        "score": 0.9,
    }


def test_evento_do_historico_aceita_payload_dict() -> None:
    row = {
        "event_id": "event-2",
        "event_type": "ContaLiberada",
        "payload": {"released_by": "quarantine-service", "motivo": "analise"},
        "occurred_at": datetime(2026, 8, 5, 10, 30, tzinfo=timezone.utc),
    }

    event = PostgresCaseRepository._audit_event_from_row(row)

    assert event.actor == "quarantine-service"
    assert event.motivo == "analise"
    assert event.payload == {
        "released_by": "quarantine-service",
        "motivo": "analise",
    }
