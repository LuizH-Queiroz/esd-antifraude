"""Monta a aplicação FastAPI do Ingestion Service.

create_app() aceita event_store/event_publisher opcionais justamente para
permitir testes automatizados injetarem as versões em memória (ver
tests/test_ingestion_service.py na raiz do repositório), sem precisar de
PostgreSQL nem RabbitMQ reais.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from ingestion.config import Settings
from ingestion.event_store import EventStore, PostgresEventStore
from ingestion.publisher import EventPublisher, RabbitMQEventPublisher
from ingestion.routes import health, transactions

LOGGER = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(
    event_store: EventStore | None = None,
    event_publisher: EventPublisher | None = None,
) -> FastAPI:
    """Fábrica da aplicação.

    Em produção (Docker Compose), chamada sem argumentos — usa PostgreSQL
    e RabbitMQ reais, construídos a partir de Settings.from_environment().
    Em testes, chamada com InMemoryEventStore/InMemoryEventPublisher.
    """
    settings = Settings.from_environment()
    _configure_logging(settings.log_level)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = event_store or PostgresEventStore(settings.database_url)
        publisher = event_publisher or RabbitMQEventPublisher(
            settings.rabbitmq_url,
            settings.exchange_name,
            settings.queue_name,
            settings.routing_key,
        )

        await store.initialize()
        await publisher.initialize()

        app.state.event_store = store
        app.state.event_publisher = publisher

        LOGGER.info("Ingestion Service iniciado.")
        try:
            yield
        finally:
            await publisher.close()
            await store.close()
            LOGGER.info("Ingestion Service encerrado.")

    app = FastAPI(
        title="Sistema Antifraude — Ingestion Service",
        description=(
            "Recebe eventos de transação do API Gateway, aplica a "
            "Anti-corruption Layer, persiste no Event Store e publica no "
            "RabbitMQ para consumo pelo Risk Scoring Service."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(transactions.router)

    return app


app = create_app()