"""Modelos usados para validar o formato do evento recebido do API Gateway.

Este é o mesmo envelope validado em api-gateway/gateway/schemas.py. A
duplicação é intencional: cada microsserviço é uma unidade implantável
independente e mantém sua própria visão do contrato que consome — não há
um pacote de schemas compartilhado entre eles neste projeto. O custo é
precisar manter os dois em sincronia manualmente caso o contrato mude; o
benefício é que nenhum serviço precisa importar código de outro para
funcionar. Se isso se tornar um problema real (schemas divergindo sem
querer), vale considerar uma Issue futura para um pacote de contratos
compartilhado.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TransactionPayload(BaseModel):
    """Dados da transação em si, dentro do envelope do evento."""

    model_config = ConfigDict(extra="allow")

    step: int = Field(..., description="Marco de hora simulada em que a transação ocorreu.")
    type: str = Field(..., description="Tipo de movimentação (PAYMENT, TRANSFER, etc.).")
    amount: float = Field(..., ge=0, description="Valor da movimentação.")
    origin_account: str = Field(..., min_length=1, description="Conta de origem.")
    destination_account: str = Field(..., min_length=1, description="Conta de destino.")


class TransactionEvent(BaseModel):
    """Envelope completo de um evento de transação recebido do API Gateway."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    # datetime (não str): o Pydantic já valida e converte o ISO 8601 recebido
    # (ex.: "2026-08-03T22:11:25.221339Z") em um objeto datetime de verdade,
    # rejeitando com 422 qualquer valor malformado — em vez de deixar essa
    # validação "vazar" até o banco e virar um 500 lá dentro (ver domain.py
    # e event_store.py, que dependem deste campo já vir como datetime).
    occurred_at: datetime = Field(...)
    source: str = Field(..., min_length=1)
    transaction: TransactionPayload