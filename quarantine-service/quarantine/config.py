"""Configuração do Quarantine Service.

As variáveis de ambiente são lidas aqui para manter a inicialização do
serviço centralizada e compatível com o formato usado pelos demais
microsserviços do projeto. Os valores padrão seguem a topologia do
``docker-compose.yml`` e o contrato de integração com o Admin Panel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    database_url: str = "sqlite:///:memory:"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    exchange_name: str = "antifraude.eventos"
    quarantine_routing_key: str = "conta.em-quarentena"
    released_routing_key: str = "conta.liberada"
    release_command_queue_name: str = "quarantine.comando-liberacao"
    release_command_routing_key: str = "comando.liberacao"

    @classmethod
    def from_environment(cls) -> "Settings":
        rabbitmq_host = os.getenv("RABBITMQ_HOST", "rabbitmq")
        rabbitmq_port = os.getenv("RABBITMQ_PORT", "5672")
        rabbitmq_user = os.getenv("RABBITMQ_USER", "antifraud")
        rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "antifraud")

        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///:memory:"),
            rabbitmq_url=(
                f"amqp://{rabbitmq_user}:{rabbitmq_password}"
                f"@{rabbitmq_host}:{rabbitmq_port}/"
            ),
            exchange_name=os.getenv("RABBITMQ_EXCHANGE", "antifraude.eventos"),
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
        )
