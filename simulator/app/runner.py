"""Orquestra leitura, transformação e envio das transações."""

from __future__ import annotations

import json
import logging
import random
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from app.client import ApiGatewayClient, GatewayRequestError
from app.mapper import TransactionMessageMapper
from app.models import PaySimTransaction

LOGGER = logging.getLogger(__name__)


class TransactionSource(Protocol):
    """Interface mínima comum a SequentialPaySimReader e RandomPaySimSampler.

    O runner não precisa saber (nem deveria) qual estratégia de leitura está
    por trás — apenas que existe uma próxima transação disponível via
    `sample()`. Isso é o que permite trocar a estratégia (SAMPLING_STRATEGY
    em app/config.py) sem tocar em nenhuma linha deste arquivo.
    """

    def sample(self) -> PaySimTransaction: ...


@dataclass(frozen=True, slots=True)
class RunOptions:
    dry_run: bool
    max_messages: int
    send_interval_min_seconds: float
    send_interval_max_seconds: float
    stop_on_error: bool


class SimulatorRunner:
    """Executa o ciclo principal sem misturar responsabilidades internas."""

    def __init__(
        self,
        source: TransactionSource,
        mapper: TransactionMessageMapper,
        client: ApiGatewayClient,
        options: RunOptions,
    ) -> None:
        self.source = source
        self.mapper = mapper
        self.client = client
        self.options = options
        self._stop_event = threading.Event()

    def request_stop(self) -> None:
        """Solicita encerramento gracioso após o trabalho atual."""
        self._stop_event.set()

    def run(self) -> int:
        """Executa até o limite configurado ou até receber interrupção."""
        successful_messages = 0
        attempted_messages = 0

        try:
            while not self._stop_event.is_set():
                if (
                    self.options.max_messages > 0
                    and attempted_messages >= self.options.max_messages
                ):
                    break

                attempted_messages += 1
                message = self._create_next_message()

                if self.options.dry_run:
                    print(json.dumps(message, ensure_ascii=False, indent=2), flush=True)
                    successful_messages += 1
                else:
                    try:
                        response = self.client.send(message)
                        successful_messages += 1
                        LOGGER.info(
                            "Evento %s enviado com HTTP %s (%s/%s).",
                            message["event_id"],
                            response.status_code,
                            successful_messages,
                            attempted_messages,
                        )
                    except GatewayRequestError:
                        LOGGER.exception(
                            "Falha definitiva ao enviar o evento %s.", message["event_id"]
                        )
                        if self.options.stop_on_error:
                            raise

                self._wait_next_interval()

            LOGGER.info(
                "Simulador encerrado: %s sucesso(s) em %s tentativa(s).",
                successful_messages,
                attempted_messages,
            )
            return successful_messages
        finally:
            # Se a fonte de dados for sequencial, persiste a posição atual
            # antes de sair, para que a próxima execução retome daqui em vez
            # de recomeçar do início. RandomPaySimSampler não implementa esses
            # métodos (não há posição a salvar), então o acesso é opcional.
            flush_checkpoint = getattr(self.source, "flush_checkpoint", None)
            if callable(flush_checkpoint):
                flush_checkpoint()

            close = getattr(self.source, "close", None)
            if callable(close):
                close()

    def _create_next_message(self) -> dict[str, Any]:
        transaction = self.source.sample()
        return self.mapper.to_message(transaction)

    def _wait_next_interval(self) -> None:
        min_seconds = self.options.send_interval_min_seconds
        max_seconds = self.options.send_interval_max_seconds

        if max_seconds <= 0:
            return

        # Um pequeno jitter aleatório (em vez de um intervalo fixo) evita que
        # os envios pareçam um metrônomo perfeito, aproximando melhor o
        # comportamento de transações reais chegando de forma assíncrona
        # (ver discussão sobre espaçamento min/max na Issue #5).
        if max_seconds == min_seconds:
            delay = min_seconds
        else:
            delay = random.uniform(min_seconds, max_seconds)

        # wait(), em vez de sleep(), permite que Ctrl+C encerre o processo
        # sem aguardar todo o intervalo configurado.
        self._stop_event.wait(delay)