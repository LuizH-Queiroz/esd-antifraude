"""Leitura centralizada das configurações do Admin Panel Service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Configurações imutáveis, com defaults compatíveis com o Docker Compose."""

    database_url: str
    rabbitmq_url: str
    exchange_name: str
    incoming_queue_name: str
    quarantine_routing_key: str
    released_routing_key: str
    release_command_queue_name: str
    release_command_routing_key: str
    log_level: str

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://admin_panel:admin_panel@admin-panel-db:5432/admin_panel_db",
        )
        rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
        rabbitmq_port = os.getenv("RABBITMQ_PORT", "5672")
        rabbitmq_user = os.getenv("RABBITMQ_USER", "antifraud")
        rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "antifraud")

        if not database_url.strip():
            raise ValueError("DATABASE_URL não pode ser vazia.")

        return cls(
            database_url=database_url,
            rabbitmq_url=(
                f"amqp://{rabbitmq_user}:{rabbitmq_password}"
                f"@{rabbitmq_host}:{rabbitmq_port}/"
            ),
            exchange_name=os.getenv("RABBITMQ_EXCHANGE", "antifraude.eventos"),
            incoming_queue_name=os.getenv(
                "RABBITMQ_ADMIN_QUEUE", "admin-panel.quarantine-events"
            ),
            quarantine_routing_key=os.getenv(
                "RABBITMQ_QUARANTINE_ROUTING_KEY", "conta.em-quarentena"
            ),
            released_routing_key=os.getenv(
                "RABBITMQ_RELEASED_ROUTING_KEY", "conta.liberada"
            ),
            release_command_queue_name=os.getenv(
                "RABBITMQ_RELEASE_COMMAND_QUEUE", "quarantine.comando-liberacao"
            ),
            release_command_routing_key=os.getenv(
                "RABBITMQ_RELEASE_COMMAND_ROUTING_KEY", "comando.liberacao"
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
