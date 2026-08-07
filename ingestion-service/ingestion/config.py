"""Leitura e validação das configurações do Ingestion Service.

Os defaults já correspondem às variáveis que o docker-compose.yml da raiz
injeta no serviço `ingestion-service` (DATABASE_URL, RABBITMQ_HOST/PORT/
USER/PASSWORD) — nenhuma configuração extra é necessária para rodar via
Docker Compose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Configurações imutáveis do Ingestion Service."""

    database_url: str

    # Montada a partir de RABBITMQ_HOST/PORT/USER/PASSWORD, já que é assim
    # que o docker-compose.yml da raiz expõe as credenciais do RabbitMQ —
    # em vez de exigir uma URL AMQP inteira como variável separada.
    rabbitmq_url: str

    # Topologia do RabbitMQ (ver ADR sobre o tema no README deste serviço).
    # Configuráveis via ambiente para permitir ajuste sem mudar código, mas
    # os defaults já são a topologia acordada pelo grupo.
    exchange_name: str
    queue_name: str
    routing_key: str

    log_level: str

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://ingestion:ingestion@ingestion-db:5432/ingestion_db",
        )

        rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
        rabbitmq_port = os.getenv("RABBITMQ_PORT", "5672")
        rabbitmq_user = os.getenv("RABBITMQ_USER", "antifraud")
        rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "antifraud")
        rabbitmq_url = f"amqp://{rabbitmq_user}:{rabbitmq_password}@{rabbitmq_host}:{rabbitmq_port}/"

        if not database_url.strip():
            raise ValueError("DATABASE_URL não pode ser vazia.")

        return cls(
            database_url=database_url,
            rabbitmq_url=rabbitmq_url,
            exchange_name=os.getenv("RABBITMQ_EXCHANGE", "antifraude.eventos"),
            queue_name=os.getenv("RABBITMQ_QUEUE", "transacoes.registradas"),
            routing_key=os.getenv("RABBITMQ_ROUTING_KEY", "transacao.registrada"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )