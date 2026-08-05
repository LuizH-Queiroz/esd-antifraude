"""Fábrica da aplicação FastAPI do Admin Panel Service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from admin_panel.broker import MessageBroker, RabbitMQMessageBroker
from admin_panel.config import Settings
from admin_panel.processor import QuarantineEventProcessor
from admin_panel.repository import CaseRepository, PostgresCaseRepository
from admin_panel.routes import cases, health

LOGGER = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(
    case_repository: CaseRepository | None = None,
    message_broker: MessageBroker | None = None,
) -> FastAPI:
    settings = Settings.from_environment()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repository = case_repository or PostgresCaseRepository(settings.database_url)
        broker = message_broker or RabbitMQMessageBroker(
            amqp_url=settings.rabbitmq_url,
            exchange_name=settings.exchange_name,
            incoming_queue_name=settings.incoming_queue_name,
            quarantine_routing_key=settings.quarantine_routing_key,
            released_routing_key=settings.released_routing_key,
            release_command_queue_name=settings.release_command_queue_name,
            release_command_routing_key=settings.release_command_routing_key,
        )
        processor = QuarantineEventProcessor(repository)

        await repository.initialize()
        app.state.case_repository = repository
        app.state.message_broker = broker
        try:
            await broker.initialize(processor.handle)
        except Exception:
            await repository.close()
            raise

        LOGGER.info("Admin Panel Service iniciado.")
        try:
            yield
        finally:
            await broker.close()
            await repository.close()
            LOGGER.info("Admin Panel Service encerrado.")

    app = FastAPI(
        title="Sistema Antifraude — Admin Panel Service",
        description=(
            "Mantém a projeção dos casos de quarentena, permite consultas "
            "administrativas e publica comandos de liberação manual."
        ),
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(cases.router)
    return app


app = create_app()
