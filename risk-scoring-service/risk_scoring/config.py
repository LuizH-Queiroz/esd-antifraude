"""Configurações do Risk Scoring Service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ml.config import DEFAULT_MODEL_PATH

# SIMULATION_START e TRANSACTION_TYPES NÃO são duplicadas aqui (ao
# contrário do que a primeira versão deste arquivo fazia): como ml/ vive
# DENTRO de risk-scoring-service/, faz parte do mesmo pacote/build Docker
# deste serviço — risk_scoring/features.py importa essas constantes
# diretamente de ml.config, com uma única fonte de verdade.


def _read_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw_value = os.getenv(name)
    value = default if raw_value is None else float(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"A variável {name} deve ser maior ou igual a {minimum}.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    rabbitmq_url: str
    exchange_name: str
    queue_name: str
    routing_key: str
    model_path: Path
    high_risk_threshold: float
    log_level: str

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://risk_scoring:risk_scoring@risk-scoring-db:5432/risk_scoring_db",
        )

        rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
        rabbitmq_port = os.getenv("RABBITMQ_PORT", "5672")
        rabbitmq_user = os.getenv("RABBITMQ_USER", "antifraud")
        rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "antifraud")
        rabbitmq_url = (
            f"amqp://{rabbitmq_user}:{rabbitmq_password}@{rabbitmq_host}:{rabbitmq_port}/"
        )

        exchange_name = os.getenv("RABBITMQ_EXCHANGE", "antifraude.eventos")
        queue_name = os.getenv("RABBITMQ_QUEUE", "transacoes.registradas")
        routing_key = os.getenv("RABBITMQ_ROUTING_KEY", "transacao.registrada")

        # Default vem do próprio ml/config.py: ARTIFACTS_DIR ali é resolvido
        # a partir de __file__, então já aponta certo independentemente de
        # onde o processo é iniciado. Só é sobrescrito se MODEL_PATH for
        # definida explicitamente no ambiente.
        model_path = Path(os.getenv("MODEL_PATH", str(DEFAULT_MODEL_PATH)))

        return cls(
            database_url=database_url,
            rabbitmq_url=rabbitmq_url,
            exchange_name=exchange_name,
            queue_name=queue_name,
            routing_key=routing_key,
            model_path=model_path,
            high_risk_threshold=_read_float("HIGH_RISK_THRESHOLD", 0.5, minimum=0.0),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )