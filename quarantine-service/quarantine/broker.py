"""Broker para o Quarantine Service com suporte a RabbitMQ e fallback em memória.

A implementação foi desenhada para seguir o contrato do Admin Panel Service:
publica os eventos de quarentena e liberação na exchange compartilhada do
projeto e usa routing keys explícitas para cada tipo de evento. Em modo de
teste, o fallback em memória preserva a simplicidade e a previsibilidade.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from quarantine.config import Settings

LOGGER = logging.getLogger(__name__)


class MessageBroker(Protocol):
    async def initialize(self) -> None: ...

    async def publish(self, payload: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class InMemoryBroker:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        return None

    async def publish(self, payload: dict[str, Any]) -> None:
        self.published.append(payload)

    async def close(self) -> None:
        return None


class RabbitMQBroker:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._connection: Any | None = None
        self._channel: Any | None = None
        self._exchange: Any | None = None

    async def initialize(self) -> None:
        import aio_pika

        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            self._settings.exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

    async def publish(self, payload: dict[str, Any]) -> None:
        import aio_pika

        assert self._exchange is not None
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(payload.get("event_id", "")),
            type=payload.get("event_type", "Evento"),
        )
        routing_key = self._settings.quarantine_routing_key
        if payload.get("event_type") == "ContaLiberada":
            routing_key = self._settings.released_routing_key
        await self._exchange.publish(message, routing_key=routing_key)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
