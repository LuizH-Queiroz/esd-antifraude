"""Testes automatizados do API Gateway.

Executados sem Docker Compose e sem nenhum serviço interno de verdade no ar
— o que é possível justamente porque o Gateway trata a ausência desses
serviços como um caso esperado (503), e não como uma falha de teste. Isso
também serve como uma prova em código do comportamento descrito no README do
Gateway: "o simulador volta a funcionar sem nenhuma mudança assim que o
Ingestion Service existir".
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from gateway.main import create_app

VALID_TRANSACTION_EVENT = {
    "event_id": "11111111-1111-1111-1111-111111111111",
    "event_type": "TRANSACTION_CREATED",
    "occurred_at": "2026-07-30T12:00:00Z",
    "source": "test-suite",
    "transaction": {
        "step": 1,
        "type": "PAYMENT",
        "amount": 100.0,
        "origin_account": "C1",
        "destination_account": "M1",
    },
}


def _client() -> TestClient:
    # Uma app nova por teste evita que estado do lifespan vaze entre testes.
    return TestClient(create_app())


def test_liveness_health_check_responde_ok() -> None:
    with _client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api-gateway"}


def test_dependencies_health_check_reporta_servicos_fora_do_ar() -> None:
    # Nenhum Ingestion Service ou Admin Panel Service real está no ar durante
    # os testes (nem deveria estar) — o esperado é "degraded", não um erro.
    with _client() as client:
        response = client.get("/health/dependencies")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["ingestion-service"]["reachable"] is False
    assert body["dependencies"]["admin-panel-service"]["reachable"] is False


def test_evento_valido_retorna_503_quando_ingestion_service_esta_fora_do_ar() -> None:
    # O Ingestion Service ainda não existe (ver Issue #5); o Gateway deve
    # aceitar estruturalmente o evento e falhar apenas ao tentar roteá-lo.
    with _client() as client:
        response = client.post("/events/transactions", json=VALID_TRANSACTION_EVENT)

    assert response.status_code == 503


def test_evento_sem_campos_obrigatorios_retorna_422() -> None:
    payload_invalido = {"event_id": "abc"}  # faltam vários campos obrigatórios

    with _client() as client:
        response = client.post("/events/transactions", json=payload_invalido)

    assert response.status_code == 422


def test_rota_admin_generica_retorna_503_quando_admin_panel_esta_fora_do_ar() -> None:
    with _client() as client:
        response = client.get("/admin/contas-em-quarentena")

    assert response.status_code == 503