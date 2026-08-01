"""Monta a aplicação FastAPI do API Gateway.

Responsabilidades deste módulo, e só deste módulo:
  - criar a aplicação e configurar logging;
  - gerenciar o ciclo de vida do cliente HTTP compartilhado (criado uma vez,
    reutilizado por todas as requisições, fechado ao encerrar);
  - incluir os roteadores definidos em gateway/routes/.

A lógica de cada rota fica em gateway/routes/; a lógica de encaminhamento
HTTP fica em gateway/proxy.py. Este arquivo não deveria crescer muito além
disso.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from gateway.config import Settings
from gateway.routes import admin, events, health

LOGGER = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cria/fecha, uma única vez, os recursos compartilhados por toda a app.

    Reutilizar um único `httpx.AsyncClient` (em vez de um por requisição)
    reaproveita conexões TCP com os serviços internos, reduzindo a latência
    de cada chamada roteada.
    """
    settings = Settings.from_environment()
    _configure_logging(settings.log_level)

    app.state.settings = settings
    app.state.http_client = httpx.AsyncClient(timeout=settings.downstream_timeout_seconds)

    LOGGER.info(
        "API Gateway iniciado: ingestion_service=%s, admin_panel_service=%s.",
        settings.ingestion_service_url,
        settings.admin_panel_service_url,
    )

    try:
        yield
    finally:
        await app.state.http_client.aclose()
        LOGGER.info("API Gateway encerrado.")


def create_app() -> FastAPI:
    """Fábrica da aplicação — facilita testes (cada teste pode criar a sua)."""
    app = FastAPI(
        title="Sistema Antifraude — API Gateway",
        description=(
            "Ponto único de entrada e saída do Sistema Antifraude. Roteia "
            "eventos de transação do Sistema Bancário ao Ingestion Service e "
            "requisições do Administrador ao Admin Panel Service."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(admin.router)

    return app


app = create_app()