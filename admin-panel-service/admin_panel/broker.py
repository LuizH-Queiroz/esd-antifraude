"""Consumo e publicação de mensagens no RabbitMQ."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError

from admin_panel.domain import ReleaseCommand

LOGGER = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MessageBroker(Protocol):
    async def initialize(self, event_handler: EventHandler) -> None: ...

    async def publish_release_command(self, command: ReleaseCommand) -> None: ...

    async def close(self) -> None: ...


class RabbitMQMessageBroker:
    """Cliente RabbitMQ robusto, compartilhado entre consumidor e produtor."""

    def __init__(
        self,
        *,
        amqp_url: str,
        exchange_name: str,
        incoming_queue_name: str,
        quarantine_routing_key: str,
        released_routing_key: str,
        release_command_queue_name: str,
        release_command_routing_key: str,
    ) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._incoming_queue_name = incoming_queue_name
        self._quarantine_routing_key = quarantine_routing_key
        self._released_routing_key = released_routing_key
        self._release_command_queue_name = release_command_queue_name
        self._release_command_routing_key = release_command_routing_key
        self._connection: Any | None = None
        self._channel: Any | None = None
        self._exchange: Any | None = None

    async def initialize(self, event_handler: EventHandler) -> None:
        import aio_pika

        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=20)
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        incoming_queue = await self._channel.declare_queue(
            self._incoming_queue_name,
            durable=True,
        )
        await incoming_queue.bind(
            self._exchange,
            routing_key=self._quarantine_routing_key,
        )
        await incoming_queue.bind(
            self._exchange,
            routing_key=self._released_routing_key,
        )

        # A fila do futuro consumidor é declarada também pelo produtor para
        # impedir que comandos sejam descartados enquanto o Quarantine Service
        # ainda não estiver conectado.
        command_queue = await self._channel.declare_queue(
            self._release_command_queue_name,
            durable=True,
        )
        await command_queue.bind(
            self._exchange,
            routing_key=self._release_command_routing_key,
        )

        async def on_message(message: Any) -> None:
            try:
                payload = json.loads(message.body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("A mensagem precisa ser um objeto JSON.")
                await event_handler(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError):
                LOGGER.exception("Mensagem inválida recebida; descartando sem requeue.")
                await message.reject(requeue=False)
            except Exception:
                LOGGER.exception("Falha transitória ao processar mensagem; reenfileirando.")
                await message.nack(requeue=True)
            else:
                await message.ack()

        await incoming_queue.consume(on_message)
        LOGGER.info(
            "RabbitMQ conectado: exchange=%s, fila=%s.",
            self._exchange_name,
            self._incoming_queue_name,
        )

    async def publish_release_command(self, command: ReleaseCommand) -> None:
        import aio_pika

        assert self._exchange is not None, "initialize() precisa ser chamado antes."
        message = aio_pika.Message(
            body=json.dumps(command.to_message()).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=command.command_id,
            type="ComandoDeLiberacao",
        )
        await self._exchange.publish(
            message,
            routing_key=self._release_command_routing_key,
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()


class InMemoryMessageBroker:
    """Fake em memória usado nos testes automatizados."""

    def __init__(self) -> None:
        self.published: list[ReleaseCommand] = []
        self._event_handler: EventHandler | None = None

    async def initialize(self, event_handler: EventHandler) -> None:
        self._event_handler = event_handler

    async def publish_release_command(self, command: ReleaseCommand) -> None:
        self.published.append(command)

    async def deliver(self, payload: dict[str, Any]) -> None:
        assert self._event_handler is not None, "initialize() precisa ser chamado antes."
        await self._event_handler(payload)

    async def close(self) -> None:
        pass
