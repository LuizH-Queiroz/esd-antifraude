"""Publicação de eventos de risco alto para o Quarantine Service."""

from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika

LOGGER = logging.getLogger(__name__)


class RabbitMQPublisher:
    def __init__(self, *, amqp_url: str, exchange_name: str, routing_key: str) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._routing_key = routing_key
        self._connection: Any | None = None
        self._channel: Any | None = None
        self._exchange: Any | None = None

    async def initialize(self) -> None:
        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

    async def publish(self, payload: dict[str, Any]) -> None:
        assert self._exchange is not None
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(payload.get("event_id", "")),
            type=payload.get("event_type", "Evento"),
        )
        await self._exchange.publish(message, routing_key=self._routing_key)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
