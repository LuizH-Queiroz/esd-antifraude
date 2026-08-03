"""Testes automatizados do Ingestion Service.

Usam InMemoryEventStore e InMemoryEventPublisher — não dependem de
PostgreSQL nem RabbitMQ reais. Isso permite testar a lógica de negócio
(persistir -> publicar -> responder) isoladamente, e também simular
cenários que seriam difíceis de provocar de propósito contra uma
infraestrutura real, como uma falha ao publicar.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from ingestion.event_store import InMemoryEventStore
from ingestion.main import create_app
from ingestion.publisher import InMemoryEventPublisher

VALID_TRANSACTION_EVENT = {
    "event_id": "11111111-1111-1111-1111-111111111111",
    "event_type": "TRANSACTION_CREATED",
    "occurred_at": "2026-08-01T12:00:00Z",
    "source": "test-suite",
    "transaction": {
        "step": 1,
        "type": "PAYMENT",
        "amount": 100.0,
        "origin_account": "C1",
        "destination_account": "M1",
    },
}


class FailingEventPublisher:
    """Publisher fake que sempre falha — para testar o caminho de erro."""

    async def initialize(self) -> None:
        pass

    async def publish(self, routing_key: str, payload: dict) -> None:
        raise ConnectionError("RabbitMQ indisponível (simulado para teste).")

    async def close(self) -> None:
        pass


def _client(event_store=None, event_publisher=None) -> TestClient:
    app = create_app(
        event_store=event_store or InMemoryEventStore(),
        event_publisher=event_publisher or InMemoryEventPublisher(),
    )
    return TestClient(app)


def test_liveness_health_check_responde_ok() -> None:
    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ingestion-service"}


def test_evento_valido_e_persistido_e_publicado() -> None:
    store = InMemoryEventStore()
    publisher = InMemoryEventPublisher()

    with _client(event_store=store, event_publisher=publisher) as client:
        response = client.post("/internal/transactions", json=VALID_TRANSACTION_EVENT)

    assert response.status_code == 202
    assert response.json()["event_id"] == VALID_TRANSACTION_EVENT["event_id"]

    # Persistiu no Event Store.
    assert VALID_TRANSACTION_EVENT["event_id"] in store.events

    # Publicou no broker (fake), com a routing key e o formato esperados.
    assert len(publisher.published) == 1
    routing_key, payload = publisher.published[0]
    assert routing_key == "transacao.registrada"
    assert payload["event_type"] == "TransacaoRegistrada"
    assert payload["transaction"]["origin_account"] == "C1"


def test_evento_duplicado_nao_e_persistido_duas_vezes() -> None:
    store = InMemoryEventStore()
    publisher = InMemoryEventPublisher()

    with _client(event_store=store, event_publisher=publisher) as client:
        client.post("/internal/transactions", json=VALID_TRANSACTION_EVENT)
        second_response = client.post("/internal/transactions", json=VALID_TRANSACTION_EVENT)

    # A segunda tentativa ainda responde 202 (idempotente do ponto de vista
    # de quem chama), mas o Event Store continua com um único registro.
    assert second_response.status_code == 202
    assert len(store.events) == 1

    # Republicamos mesmo em caso de duplicata (ver comentário em
    # routes/transactions.py sobre semântica at-least-once) — por isso
    # esperamos 2 publicações, não 1.
    assert len(publisher.published) == 2


def test_evento_sem_campos_obrigatorios_retorna_422() -> None:
    payload_invalido = {"event_id": "abc"}

    with _client() as client:
        response = client.post("/internal/transactions", json=payload_invalido)

    assert response.status_code == 422


def test_falha_ao_publicar_retorna_503_mas_evento_fica_persistido() -> None:
    store = InMemoryEventStore()

    with _client(event_store=store, event_publisher=FailingEventPublisher()) as client:
        response = client.post("/internal/transactions", json=VALID_TRANSACTION_EVENT)

    assert response.status_code == 503
    # O ponto mais importante deste teste: mesmo com a publicação falhando,
    # o evento não se perde — continua persistido no Event Store.
    assert VALID_TRANSACTION_EVENT["event_id"] in store.events