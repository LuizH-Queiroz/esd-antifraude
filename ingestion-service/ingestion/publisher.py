"""Publicação de eventos no Message Broker (RabbitMQ).

Duas implementações do mesmo contrato (EventPublisher), pelo mesmo motivo
descrito em event_store.py: uma real (RabbitMQEventPublisher) e uma fake
em memória (InMemoryEventPublisher), usada só em testes.

IMPORTANTE — limitação conhecida (dual-write problem): persistir no
Event Store e publicar no RabbitMQ são duas operações separadas, não
atômicas. Se o processo cair exatamente entre as duas, o evento fica
persistido mas nunca publicado. A forma "correta" de resolver isso em um
sistema de produção é o padrão Transactional Outbox (gravar a intenção de
publicar na mesma transação do banco, e um processo separado só então
publica e marca como enviado). Não implementamos isso aqui por ser um
salto de complexidade desproporcional ao escopo desta POC — mas é
importante que o grupo saiba que essa lacuna existe conscientemente, e
não por descuido.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection

LOGGER = logging.getLogger(__name__)


class EventPublisher(Protocol):
    """Contrato: qualquer coisa que saiba publicar uma mensagem serve aqui."""

    async def initialize(self) -> None: ...

    async def publish(self, routing_key: str, payload: dict) -> None: ...

    async def close(self) -> None: ...


class RabbitMQEventPublisher:
    """Implementação real, sobre RabbitMQ via aio-pika."""

    def __init__(
        self, amqp_url: str, exchange_name: str, queue_name: str, routing_key: str
    ) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
        self._routing_key = routing_key

        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def initialize(self) -> None:
        # connect_robust() (em vez de connect()) reconecta automaticamente
        # se a conexão cair, redeclarando exchange/fila sozinho — evita
        # termos que escrever manualmente uma lógica de retry, como fizemos
        # para o cliente HTTP do simulador.
        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()

        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,  # sobrevive a um restart do RabbitMQ
        )

        # O Ingestion Service é apenas produtor — quem vai efetivamente
        # consumir desta fila é o futuro Risk Scoring Service. Ainda assim,
        # declaramos a fila e a vinculamos aqui, e não só a exchange, por
        # dois motivos: (1) sem nenhuma fila vinculada, uma exchange do tipo
        # topic simplesmente descarta as mensagens publicadas (elas não
        # ficam "esperando" em lugar nenhum); (2) isso permite inspecionar
        # as mensagens publicadas manualmente, via RabbitMQ Management UI
        # (http://localhost:15672), mesmo antes do Risk Scoring Service
        # existir. Declarar a mesma fila com os mesmos parâmetros é uma
        # operação segura de repetir — quando o Risk Scoring Service for
        # implementado, ele fará a mesma declaração (idempotente) e
        # simplesmente começará a consumir o que já estiver lá.
        queue = await self._channel.declare_queue(self._queue_name, durable=True)
        await queue.bind(self._exchange, routing_key=self._routing_key)

        LOGGER.info(
            "Conectado ao RabbitMQ: exchange=%s, fila=%s, routing_key=%s.",
            self._exchange_name,
            self._queue_name,
            self._routing_key,
        )

    async def publish(self, routing_key: str, payload: dict) -> None:
        assert self._exchange is not None, "initialize() precisa ser chamado antes."

        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            content_type="application/json",
            # PERSISTENT: a mensagem é gravada em disco pelo RabbitMQ, não
            # apenas mantida em memória — sobrevive a um restart do broker.
            # Sem isso, uma mensagem publicada pouco antes de um crash do
            # RabbitMQ seria perdida, o que não é aceitável para um evento
            # de negócio que já foi confirmado como persistido no Event Store.
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await self._exchange.publish(message, routing_key=routing_key)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()


class InMemoryEventPublisher:
    """Fake em memória, usado apenas em testes automatizados."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def initialize(self) -> None:
        pass

    async def publish(self, routing_key: str, payload: dict) -> None:
        self.published.append((routing_key, payload))

    async def close(self) -> None:
        pass