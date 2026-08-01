"""Leitura e validação das configurações do simulador.

As configurações são fornecidas por variáveis de ambiente para que o mesmo
código funcione sem alterações no host do desenvolvedor, no Docker Compose e,
no futuro, em outro ambiente de execução.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SIMULATOR_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_NAME = "PS_20174392719_1491204439457_log.csv"

VALID_SAMPLING_STRATEGIES = frozenset({"sequential", "random"})


def _read_bool(name: str, default: bool) -> bool:
    """Converte uma variável de ambiente para booleano."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"A variável {name} deve ser true/false, 1/0, yes/no ou on/off; "
        f"valor recebido: {raw_value!r}."
    )


def _read_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw_value = os.getenv(name)
    value = default if raw_value is None else int(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"A variável {name} deve ser maior ou igual a {minimum}.")
    return value


def _read_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw_value = os.getenv(name)
    value = default if raw_value is None else float(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"A variável {name} deve ser maior ou igual a {minimum}.")
    return value


def _read_interval_range() -> tuple[float, float]:
    """Resolve o intervalo (mínimo, máximo) de espaçamento entre envios.

    Existem três formas de configurar isso, verificadas nesta ordem:

    1. SEND_INTERVAL_MIN_SECONDS e/ou SEND_INTERVAL_MAX_SECONDS definidos —
       usados diretamente (o que faltar assume o outro valor, virando um
       intervalo fixo).
    2. Apenas a variável legada SEND_INTERVAL_SECONDS definida (usada por
       versões anteriores deste arquivo) — tratada como um intervalo fixo
       (min == max), preservando o comportamento anterior sem exigir que
       ninguém atualize um `.env` já existente.
    3. Nenhuma das anteriores — usa o padrão recomendado na Issue #5
       (0.5s a 2.0s), com variação aleatória a cada envio em vez de um
       intervalo fixo, para simular um fluxo de transações mais realista
       (o mundo real não envia eventos com espaçamento perfeitamente
       constante, feito um metrônomo).
    """
    min_env = os.getenv("SEND_INTERVAL_MIN_SECONDS")
    max_env = os.getenv("SEND_INTERVAL_MAX_SECONDS")
    legacy_env = os.getenv("SEND_INTERVAL_SECONDS")

    if min_env is not None or max_env is not None:
        interval_min = float(min_env) if min_env is not None else float(max_env)
        interval_max = float(max_env) if max_env is not None else interval_min
    elif legacy_env is not None:
        interval_min = interval_max = float(legacy_env)
    else:
        interval_min, interval_max = 0.5, 2.0

    if interval_min < 0:
        raise ValueError("SEND_INTERVAL_MIN_SECONDS não pode ser negativo.")
    if interval_max < interval_min:
        raise ValueError(
            "SEND_INTERVAL_MAX_SECONDS não pode ser menor que SEND_INTERVAL_MIN_SECONDS."
        )
    return interval_min, interval_max


@dataclass(frozen=True, slots=True)
class Settings:
    """Configurações imutáveis utilizadas durante uma execução."""

    dataset_path: Path
    dataset_index_path: Path
    sampling_strategy: str
    sequential_checkpoint_path: Path
    checkpoint_every_messages: int
    api_gateway_url: str
    api_gateway_endpoint: str
    send_interval_min_seconds: float
    send_interval_max_seconds: float
    request_timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_messages: int
    include_balance_fields: bool
    include_ground_truth: bool
    stop_on_error: bool
    random_seed: int | None
    log_level: str

    @property
    def gateway_events_url(self) -> str:
        """Monta a URL final sem depender de barras duplicadas."""
        return (
            f"{self.api_gateway_url.rstrip('/')}"
            f"/{self.api_gateway_endpoint.strip('/')}"
        )

    @classmethod
    def from_environment(cls) -> Settings:
        """Cria as configurações a partir do ambiente atual."""
        default_dataset = SIMULATOR_ROOT / "data" / DEFAULT_DATASET_NAME
        default_index = SIMULATOR_ROOT / "data" / ".cache" / "paysim.offsets"
        default_checkpoint = SIMULATOR_ROOT / "data" / ".cache" / "sequential_position.json"

        seed_value = os.getenv("SIMULATOR_RANDOM_SEED")
        random_seed = int(seed_value) if seed_value not in {None, ""} else None

        api_gateway_url = os.getenv("API_GATEWAY_URL", "http://api-gateway:8000").strip()
        api_gateway_endpoint = os.getenv(
            "API_GATEWAY_ENDPOINT", "/events/transactions"
        ).strip()

        if not api_gateway_url:
            raise ValueError("API_GATEWAY_URL não pode ser vazia.")
        if not api_gateway_endpoint:
            raise ValueError("API_GATEWAY_ENDPOINT não pode ser vazio.")

        sampling_strategy = os.getenv("SAMPLING_STRATEGY", "sequential").strip().lower()
        if sampling_strategy not in VALID_SAMPLING_STRATEGIES:
            raise ValueError(
                "SAMPLING_STRATEGY deve ser 'sequential' ou 'random'; "
                f"valor recebido: {sampling_strategy!r}."
            )

        send_interval_min, send_interval_max = _read_interval_range()

        return cls(
            dataset_path=Path(os.getenv("DATASET_PATH", str(default_dataset))).expanduser(),
            dataset_index_path=Path(
                os.getenv("DATASET_INDEX_PATH", str(default_index))
            ).expanduser(),
            sampling_strategy=sampling_strategy,
            sequential_checkpoint_path=Path(
                os.getenv("SEQUENTIAL_CHECKPOINT_PATH", str(default_checkpoint))
            ).expanduser(),
            checkpoint_every_messages=_read_int(
                "CHECKPOINT_EVERY_MESSAGES", 20, minimum=1
            ),
            api_gateway_url=api_gateway_url,
            api_gateway_endpoint=api_gateway_endpoint,
            send_interval_min_seconds=send_interval_min,
            send_interval_max_seconds=send_interval_max,
            request_timeout_seconds=_read_float(
                "REQUEST_TIMEOUT_SECONDS", 5.0, minimum=0.1
            ),
            max_retries=_read_int("MAX_RETRIES", 3, minimum=0),
            retry_backoff_seconds=_read_float(
                "RETRY_BACKOFF_SECONDS", 1.0, minimum=0.0
            ),
            max_messages=_read_int("MAX_MESSAGES", 0, minimum=0),
            include_balance_fields=_read_bool("INCLUDE_BALANCE_FIELDS", False),
            include_ground_truth=_read_bool("INCLUDE_GROUND_TRUTH", False),
            stop_on_error=_read_bool("STOP_ON_ERROR", False),
            random_seed=random_seed,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )