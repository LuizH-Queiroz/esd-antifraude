"""Consumidor RabbitMQ para comandos de liberação.

Este módulo implementa o recebimento assíncrono de mensagens do Admin Panel
Service. O objetivo é consumir a fila ``quarantine.comando-liberacao`` e
processar os comandos de liberação com o mesmo fluxo usado pelos endpoints
internos, incluindo a idempotência por ``event_id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aio_pika

from quarantine.config import Settings
from quarantine.processor import process_release_command

LOGGER = logging.getLogger(__name__)


class RabbitMQConsumer:
    """Consumidor simples do RabbitMQ com reconexão básica."""

    def __init__(self, *, repository: Any, broker: Any, settings: Settings) -> None:
        self._repository = repository
        self._broker = broker
        self._settings = settings
        self._connection: aio_pika.abc.AbstractConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._queue: aio_pika.abc.AbstractQueue | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
                self._channel = await self._connection.channel()
                await self._channel.set_qos(prefetch_count=1)
                self._exchange = await self._channel.declare_exchange(
                    self._settings.exchange_name,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )
                self._queue = await self._channel.declare_queue(
                    self._settings.release_command_queue_name,
                    durable=True,
                )
                await self._queue.bind(
                    self._exchange,
                    routing_key=self._settings.release_command_routing_key,
                )

                async with self._queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process(requeue=False):
                            payload = json.loads(message.body.decode("utf-8"))
                            if payload.get("event_type") != "ComandoDeLiberacao":
                                continue
                            processed = await process_release_command(payload, self._repository, self._broker)
                            if processed:
                                LOGGER.info("Comando de liberação processado para %s", payload.get("account_id"))
                            else:
                                LOGGER.info("Comando de liberação ignorado ou já processado para %s", payload.get("account_id"))
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover - proteção defensiva
                LOGGER.exception("Falha no consumidor RabbitMQ: %s", exc)
                await asyncio.sleep(5)
