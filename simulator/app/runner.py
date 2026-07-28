"""Orquestra sorteio, transformação e envio das transações."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from app.client import ApiGatewayClient, GatewayRequestError
from app.dataset import RandomPaySimSampler
from app.mapper import TransactionMessageMapper

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunOptions:
    dry_run: bool
    max_messages: int
    send_interval_seconds: float
    stop_on_error: bool


class SimulatorRunner:
    """Executa o ciclo principal sem misturar responsabilidades internas."""

    def __init__(
        self,
        sampler: RandomPaySimSampler,
        mapper: TransactionMessageMapper,
        client: ApiGatewayClient,
        options: RunOptions,
    ) -> None:
        self.sampler = sampler
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

        while not self._stop_event.is_set():
            if (
                self.options.max_messages > 0
                and attempted_messages >= self.options.max_messages
            ):
                break

            attempted_messages += 1
            message = self._create_random_message()

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

            if self.options.send_interval_seconds > 0:
                # wait(), em vez de sleep(), permite que Ctrl+C encerre o processo
                # sem aguardar todo o intervalo configurado.
                self._stop_event.wait(self.options.send_interval_seconds)

        LOGGER.info(
            "Simulador encerrado: %s sucesso(s) em %s tentativa(s).",
            successful_messages,
            attempted_messages,
        )
        return successful_messages

    def _create_random_message(self) -> dict[str, Any]:
        transaction = self.sampler.sample()
        return self.mapper.to_message(transaction)
