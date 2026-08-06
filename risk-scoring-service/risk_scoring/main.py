"""Monta a aplicação do Risk Scoring Service: API HTTP leve + consumidor RabbitMQ.

O consumidor roda como uma asyncio.Task, iniciada no lifespan da aplicação
FastAPI, dentro do mesmo event loop que o uvicorn já gerencia — sem
precisar de um processo/thread separado.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from risk_scoring.account_stats import AccountStatsStore, PostgresAccountStatsStore
from risk_scoring.config import Settings
from risk_scoring.consumer import EventConsumer, RabbitMQEventConsumer
from risk_scoring.model import FraudModel
from risk_scoring.publisher import RabbitMQPublisher
from risk_scoring.routes import accounts, health
from risk_scoring.scoring import process_transacao_registrada

LOGGER = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app(
    account_stats_store: AccountStatsStore | None = None,
    event_consumer: EventConsumer | None = None,
    model: FraudModel | None = None,
) -> FastAPI:
    settings = Settings.from_environment()
    _configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = account_stats_store or PostgresAccountStatsStore(settings.database_url)
        consumer = event_consumer or RabbitMQEventConsumer(
            settings.rabbitmq_url,
            settings.exchange_name,
            settings.queue_name,
            settings.routing_key,
        )
        fraud_model = model or FraudModel(settings.model_path)

        await store.initialize()
        fraud_model.load()
        await consumer.initialize()

        publisher = RabbitMQPublisher(
            amqp_url=settings.rabbitmq_url,
            exchange_name=settings.exchange_name,
            routing_key="conta.em-quarentena",
        )
        await publisher.initialize()

        app.state.account_stats_store = store

        async def handle_message(payload: dict) -> None:
            await process_transacao_registrada(
                payload,
                store,
                fraud_model,
                settings.high_risk_threshold,
                publisher=publisher,
            )

        consumer_task = asyncio.create_task(consumer.consume(handle_message))

        LOGGER.info("Risk Scoring Service iniciado; consumindo fila %s.", settings.queue_name)
        try:
            yield
        finally:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task
            await consumer.close()
            await publisher.close()
            await store.close()
            LOGGER.info("Risk Scoring Service encerrado.")

    app = FastAPI(
        title="Sistema Antifraude — Risk Scoring Service",
        description=(
            "Consome eventos TransacaoRegistrada do RabbitMQ, calcula o "
            "risk score via o modelo treinado em ml/, e mantém o histórico "
            "incremental por conta em risk-scoring-db."
        ),
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(accounts.router)
    return app


app = create_app()