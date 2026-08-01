"""Leitura e validação das configurações do API Gateway.

Segue o mesmo princípio do simulador (ver simulator/app/config.py): tudo vem
de variáveis de ambiente, com valores padrão que já funcionam dentro do
Docker Compose da raiz do projeto (onde o nome do serviço funciona como
hostname), sem exigir configuração extra de quem for rodar o projeto.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _read_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw_value = os.getenv(name)
    value = default if raw_value is None else float(raw_value)
    if minimum is not None and value < minimum:
        raise ValueError(f"A variável {name} deve ser maior ou igual a {minimum}.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Configurações imutáveis do API Gateway."""

    # URL base dos microsserviços internos para onde o Gateway roteia.
    # Os defaults já correspondem aos nomes de serviço definidos no
    # docker-compose.yml da raiz do projeto.
    ingestion_service_url: str
    admin_panel_service_url: str

    # Paths internos usados ao encaminhar a requisição para cada serviço.
    # Ficam configuráveis porque nem Ingestion Service nem Admin Panel Service
    # têm contrato definitivo ainda — quando existir, basta ajustar aqui.
    ingestion_events_path: str

    # Timeout ao chamar um serviço interno. Mantido baixo de propósito: o
    # Gateway não deve travar esperando um serviço que ainda não existe (ou
    # está fora do ar); prefere falhar rápido e devolver 503, deixando quem
    # chamou (ex.: o simulador) decidir se tenta de novo.
    downstream_timeout_seconds: float

    log_level: str

    @classmethod
    def from_environment(cls) -> Settings:
        ingestion_service_url = os.getenv(
            "INGESTION_SERVICE_URL", "http://ingestion-service:8000"
        ).rstrip("/")
        admin_panel_service_url = os.getenv(
            "ADMIN_PANEL_SERVICE_URL", "http://admin-panel-service:8000"
        ).rstrip("/")
        ingestion_events_path = os.getenv(
            "INGESTION_EVENTS_PATH", "/internal/transactions"
        )

        if not ingestion_service_url:
            raise ValueError("INGESTION_SERVICE_URL não pode ser vazia.")
        if not admin_panel_service_url:
            raise ValueError("ADMIN_PANEL_SERVICE_URL não pode ser vazia.")
        if not ingestion_events_path.strip():
            raise ValueError("INGESTION_EVENTS_PATH não pode ser vazio.")

        return cls(
            ingestion_service_url=ingestion_service_url,
            admin_panel_service_url=admin_panel_service_url,
            ingestion_events_path=ingestion_events_path,
            downstream_timeout_seconds=_read_float(
                "DOWNSTREAM_TIMEOUT_SECONDS", 3.0, minimum=0.1
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )