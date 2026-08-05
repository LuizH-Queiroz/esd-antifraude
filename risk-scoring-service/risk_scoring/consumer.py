"""Consumo de eventos do RabbitMQ (fila já publicada pelo Ingestion Service)."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

LOGGER = logging.getLogger(__name__)

MessageHandler = Callable[[dict], Awaitable[None]]


class EventConsumer(Protocol):
    async def initialize(self) -> None: ...
    async def consume(self, handler: MessageHandler) -> None: ...
    async def close(self) -> None: ...


class RabbitMQEventConsumer:
    """Implementação real, sobre RabbitMQ via aio-pika.

    Declara a MESMA exchange e fila que o Ingestion Service já declara ao
    publicar (ver ingestion-service/ingestion/publisher.py) — é uma
    operação segura de repetir, e é assim que este serviço passa a
    consumir o que o Ingestion Service já vinha publicando antes mesmo
    deste consumidor existir.
    """

    def __init__(
        self, amqp_url: str, exchange_name: str, queue_name: str, routing_key: str
    ) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
        self._routing_key = routing_key

        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._queue = None

    async def initialize(self) -> None:
        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()

        # prefetch_count=1: nunca mais de uma mensagem "em voo" por vez.
        # Combinado com o fato de consume() processar cada mensagem até o
        # fim antes de buscar a próxima (o `async for` abaixo é sequencial
        # por construção, sem disparar tasks concorrentes), isso garante
        # processamento estritamente sequencial — pré-requisito para o
        # cálculo causal correto das features (ver account_stats.py).
        await self._channel.set_qos(prefetch_count=1)

        exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True,
        )
        self._queue = await self._channel.declare_queue(self._queue_name, durable=True)
        await self._queue.bind(exchange, routing_key=self._routing_key)

        LOGGER.info(
            "Consumidor RabbitMQ pronto: exchange=%s, fila=%s, routing_key=%s.",
            self._exchange_name, self._queue_name, self._routing_key,
        )

    async def consume(self, handler: MessageHandler) -> None:
        assert self._queue is not None, "initialize() precisa ser chamado antes."

        async with self._queue.iterator() as queue_iterator:
            async for message in queue_iterator:
                # message.process() confirma (ack) a mensagem automaticamente
                # se o bloco terminar sem exceção, ou a rejeita (sem
                # reenfileirar, por padrão) se uma exceção escapar. Uma
                # mensagem rejeitada sem dead-letter-queue configurada é
                # simplesmente descartada pelo RabbitMQ — uma limitação
                # conhecida (ver README): uma falha transitória (ex.: queda
                # momentânea da conexão com o Postgres) perde o evento em
                # vez de tentar de novo. Aceitável para esta POC; uma DLQ
                # com retry seria o próximo passo natural de robustez.
                async with message.process():
                    payload = json.loads(message.body.decode("utf-8"))
                    try:
                        await handler(payload)
                    except Exception:
                        LOGGER.exception(
                            "Falha ao processar evento %s; mensagem será descartada.",
                            payload.get("event_id"),
                        )
                        raise  # repropaga para que message.process() rejeite a mensagem

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()


class InMemoryEventConsumer:
    """Fake em memória, usado apenas em testes automatizados.

    Em vez de consumir de uma fila real, processa uma lista pré-definida
    de mensagens (injetadas no construtor) e termina — suficiente para
    testar a integração consumer -> scoring sem RabbitMQ.
    """

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    async def initialize(self) -> None:
        pass

    async def consume(self, handler: MessageHandler) -> None:
        for message in self._messages:
            await handler(message)

    async def close(self) -> None:
        pass