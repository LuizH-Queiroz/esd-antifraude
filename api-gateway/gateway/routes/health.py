"""Rotas de verificação de saúde (health check) do API Gateway.

Duas rotas, com propósitos diferentes:

- `/health`: liveness simples — "o processo do Gateway está de pé?". Não
  depende de nenhum outro serviço, responde instantaneamente. É a que faria
  sentido usar num healthcheck do Docker Compose.
- `/health/dependencies`: útil especificamente nesta fase inicial do
  projeto, em que Ingestion Service e Admin Panel Service ainda não existem.
  Deixa explícito, sem precisar consultar logs, se o Gateway está conseguindo
  (ou não) alcançar cada serviço interno — o que hoje será sempre "down",
  mas passa a "up" automaticamente assim que cada serviço for implementado,
  sem precisar de nenhuma mudança aqui.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict:
    """Confirma apenas que o processo do Gateway está no ar."""
    return {"status": "ok", "service": "api-gateway"}


@router.get("/health/dependencies")
async def dependencies_health(request: Request) -> dict:
    """Verifica, com timeout curto, se cada serviço interno está alcançável."""
    settings = request.app.state.settings
    http_client: httpx.AsyncClient = request.app.state.http_client

    dependencies = {
        "ingestion-service": settings.ingestion_service_url,
        "admin-panel-service": settings.admin_panel_service_url,
    }

    results: dict[str, dict] = {}
    for name, base_url in dependencies.items():
        results[name] = await _ping(http_client, base_url)

    overall_status = (
        "ok" if all(result["reachable"] for result in results.values()) else "degraded"
    )
    return {"status": overall_status, "dependencies": results}


async def _ping(http_client: httpx.AsyncClient, base_url: str) -> dict:
    """Faz uma checagem best-effort de alcançabilidade de um serviço interno.

    Não importa qual status HTTP volte (mesmo um 404, por exemplo) — o que
    importa é conseguir estabelecer a conexão, ou seja, que o serviço existe
    e está de pé. Erros de conexão/timeout são tratados aqui mesmo (não
    propagados), já que esta rota deve sempre responder algo útil, mesmo com
    tudo fora do ar.
    """
    try:
        response = await http_client.get(f"{base_url}/health", timeout=1.5)
        return {"reachable": True, "status_code": response.status_code}
    except httpx.RequestError as exc:
        return {"reachable": False, "error": str(exc)}