"""Cliente HTTP responsável exclusivamente pela comunicação com o Gateway."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    status_code: int
    body: str


class GatewayRequestError(RuntimeError):
    """Falha definitiva ao enviar um evento ao API Gateway."""


class ApiGatewayClient:
    """Envia eventos com timeout, retentativa e chave de idempotência.

    As retentativas reutilizam o mesmo ``event_id``. O cabeçalho
    ``Idempotency-Key`` permitirá que o futuro Gateway reconheça uma repetição
    causada por falha de rede e evite processar a mesma mensagem duas vezes.
    """

    RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(
        self,
        events_url: str,
        *,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
    ) -> None:
        self.events_url = events_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def send(self, message: dict[str, Any]) -> GatewayResponse:
        event_id = str(message["event_id"])
        encoded_body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            request = Request(
                self.events_url,
                data=encoded_body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "esd-antifraude-paysim-simulator/1.0",
                    "Idempotency-Key": event_id,
                },
            )

            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    return GatewayResponse(status_code=response.status, body=body)
            except HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                if exc.code not in self.RETRYABLE_STATUS_CODES:
                    raise GatewayRequestError(
                        f"Gateway recusou o evento {event_id} com HTTP {exc.code}: "
                        f"{error_body}"
                    ) from exc
                last_error: Exception = exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc

            if attempt < attempts:
                delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
                LOGGER.warning(
                    "Falha no envio do evento %s (tentativa %s/%s): %s. "
                    "Nova tentativa em %.2fs.",
                    event_id,
                    attempt,
                    attempts,
                    last_error,
                    delay,
                )
                time.sleep(delay)

        raise GatewayRequestError(
            f"Não foi possível enviar o evento {event_id} após {attempts} tentativa(s): "
            f"{last_error}"
        ) from last_error
