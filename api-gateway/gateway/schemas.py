"""Modelos usados para validar o formato dos eventos recebidos pelo Gateway.

Importante: esta validação é deliberadamente "rasa". O Gateway confere que o
evento tem o **envelope** esperado (os campos que o simulador já envia hoje,
descritos em simulator/app/mapper.py) — não faz validação de domínio (regras
de negócio sobre o conteúdo da transação). Essa responsabilidade é da futura
Anti-corruption Layer do Ingestion Service (ver README principal), que ainda
não existe. O papel do Gateway aqui é apenas rejeitar cedo (HTTP 422) uma
requisição claramente malformada, antes de gastar uma chamada de rede
tentando encaminhá-la a um serviço interno.

`model_config = {"extra": "allow"}` em cada modelo permite que campos novos
(ex.: os campos opcionais de saldo/gabarito que o simulador só inclui quando
INCLUDE_BALANCE_FIELDS/INCLUDE_GROUND_TRUTH estão ativos) passem adiante sem
quebrar a validação — o Gateway não precisa conhecer todo campo opcional que o
produtor de eventos decida enviar.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TransactionPayload(BaseModel):
    """Dados da transação em si, dentro do envelope do evento."""

    model_config = ConfigDict(extra="allow")

    step: int = Field(..., description="Marco de hora simulada em que a transação ocorreu.")
    type: str = Field(..., description="Tipo de movimentação (PAYMENT, TRANSFER, etc.).")
    amount: float = Field(..., ge=0, description="Valor da movimentação.")
    origin_account: str = Field(..., min_length=1, description="Conta de origem.")
    destination_account: str = Field(..., min_length=1, description="Conta de destino.")


class SimulationMetadata(BaseModel):
    """Metadados sobre como o evento foi gerado pelo simulador."""

    model_config = ConfigDict(extra="allow")

    dataset: str
    selection: str


class TransactionEvent(BaseModel):
    """Envelope completo de um evento de transação recebido pelo Gateway.

    Espelha o formato produzido por simulator/app/mapper.py. Se esse
    contrato mudar (por exemplo, quando o Ingestion Service definir sua ACL
    oficial), este é o arquivo a atualizar no Gateway.
    """

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    occurred_at: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    transaction: TransactionPayload
    simulation_metadata: SimulationMetadata | None = None

    def to_downstream_payload(self) -> dict[str, Any]:
        """Serializa o evento para repassar ao serviço interno de destino.

        Centralizar essa conversão aqui (em vez de espalhar `.model_dump()`
        pelas rotas) facilita ajustar o formato de saída no futuro, sem mexer
        na lógica de roteamento.
        """
        return self.model_dump(mode="json")