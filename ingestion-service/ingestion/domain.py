"""Anti-corruption Layer: traduz o evento externo para o modelo de domínio.

Esta é a peça central da ADR sobre a ACL, citada no README principal: o
formato que o Ingestion Service RECEBE (o envelope validado em
schemas.py, que reflete o que o Simulador/Sistema Bancário envia) não
precisa ser idêntico ao formato que ele PUBLICA no RabbitMQ (o que o
futuro Risk Scoring Service vai consumir). Hoje a tradução é quase um
"passthrough" — os campos são praticamente os mesmos — mas o ponto
importante é que essa tradução tem UM lugar só no código. Quando a Issue
do Risk Scoring Service definir exatamente quais campos/tipos ele precisa
(ex.: amount como inteiro em centavos, em vez de float), o ajuste é feito
aqui, sem tocar em nada além disso.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ingestion.schemas import TransactionEvent


@dataclass(frozen=True, slots=True)
class TransacaoRegistrada:
    """Representação de domínio do evento, independente do formato de entrada."""

    event_id: str
    # datetime, não str: mantido como objeto real por todo o domínio, para
    # que qualquer consumidor (Event Store, mensagem publicada) trabalhe com
    # um tipo confiável, em vez de reanalisar uma string em cada lugar que
    # precisar da data. A conversão para texto só acontece na borda de saída
    # (to_message(), abaixo), onde o formato precisa virar JSON.
    occurred_at: datetime
    step: int
    transaction_type: str
    amount: float
    origin_account: str
    destination_account: str

    @classmethod
    def from_incoming_event(cls, event: TransactionEvent) -> TransacaoRegistrada:
        """Constrói o evento de domínio a partir do envelope externo validado."""
        return cls(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            step=event.transaction.step,
            transaction_type=event.transaction.type,
            amount=event.transaction.amount,
            origin_account=event.transaction.origin_account,
            destination_account=event.transaction.destination_account,
        )

    def to_message(self) -> dict:
        """Serializa para o formato publicado no RabbitMQ.

        Deliberadamente um dicionário simples (não reaproveita o schema de
        entrada): o formato de saída é uma decisão própria deste serviço,
        não um reflexo do que chegou — mesmo que, hoje, os valores sejam
        praticamente os mesmos.
        """
        return {
            "event_id": self.event_id,
            "event_type": "TransacaoRegistrada",
            "occurred_at": self.occurred_at.isoformat(),
            "transaction": {
                "step": self.step,
                "type": self.transaction_type,
                "amount": self.amount,
                "origin_account": self.origin_account,
                "destination_account": self.destination_account,
            },
        }