"""Entry point do Quarantine Service."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from quarantine.broker import InMemoryBroker, MessageBroker, RabbitMQBroker
from quarantine.config import Settings
from quarantine.consumer import RabbitMQConsumer, RiskScoreConsumer
from quarantine.repository import InMemoryRepository
from quarantine.routes import health_router, internal_router


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(
    *,
    repository: InMemoryRepository | None = None,
    broker: MessageBroker | None = None,
    use_rabbitmq: bool | None = None,
) -> FastAPI:
    _configure_logging()

    app = FastAPI(title="Quarantine Service")

    app.state.repository = (
        repository if repository is not None else InMemoryRepository()
    )
    app.state.broker = broker if broker is not None else InMemoryBroker()
    app.state.use_rabbitmq = (
        use_rabbitmq if use_rabbitmq is not None else broker is None
    )

    app.include_router(health_router)
    app.include_router(internal_router)

    @app.on_event("startup")
    async def startup_event() -> None:
        settings = Settings.from_environment()
        app.state.settings = settings

        if (
            app.state.use_rabbitmq
            and isinstance(app.state.broker, InMemoryBroker)
        ):
            app.state.broker = RabbitMQBroker(settings=settings)
            await app.state.broker.initialize()

            app.state.consumer = RabbitMQConsumer(
                repository=app.state.repository,
                broker=app.state.broker,
                settings=settings,
            )
            await app.state.consumer.start()

            app.state.risk_score_consumer = RiskScoreConsumer(
                repository=app.state.repository,
                broker=app.state.broker,
                settings=settings,
            )
            await app.state.risk_score_consumer.start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        consumer = getattr(app.state, "consumer", None)
        if consumer is not None:
            await consumer.stop()

        risk_score_consumer = getattr(app.state, "risk_score_consumer", None)
        if risk_score_consumer is not None:
            await risk_score_consumer.stop()

        broker_instance = app.state.broker
        if hasattr(broker_instance, "close"):
            await broker_instance.close()

    return app


app = create_app()