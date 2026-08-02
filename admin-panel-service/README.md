# Admin Panel Service

Backend que serve o Painel Admin: consome os eventos publicados pelo
Quarantine Service, mantém uma projeção consultável dos casos suspeitos/em
quarentena, e expõe essa projeção ao Administrador via API REST — inclusive
o comando de liberação manual de uma conta.

## Administrador × Admin Panel Service

Os dois nomes se parecem, mas representam papéis diferentes no sistema:

- **Administrador** é um ator humano, externo ao Sistema Antifraude — a
  pessoa que consulta os casos suspeitos e decide se libera ou mantém uma
  conta em quarentena.
- **Admin Panel Service** é um microsserviço interno do Sistema Antifraude.
  Ele não decide nada sozinho: consome `ContaEmQuarentena`/`ContaLiberada`
  do broker, guarda essas informações num formato consultável, e traduz as
  requisições HTTP do Administrador (roteadas pelo API Gateway) em leituras
  dessa projeção ou no comando `ComandoDeLiberacao`.

Este README documenta o contrato entre os dois — como a consulta funciona
na prática — que era a lacuna deixada em aberto até esta issue.

## 1. Quais requisições o Administrador pode fazer?

Proposta de contrato REST, roteado pelo API Gateway através do proxy
genérico `/admin/**` (ver `api-gateway/README.md`) — que deve ser substituído
por estas rotas específicas assim que o serviço existir:

| Rota | Método | Descrição |
|---|---|---|
| `/cases` | `GET` | Lista os casos em quarentena, com filtros (`status`, intervalo de data, faixa de score) e paginação |
| `/cases/{account_id}` | `GET` | Detalhe de um caso: motivo da quarentena, score, histórico de eventos daquela conta |
| `/cases/{account_id}/release` | `POST` | Comando de liberação manual — publica `ComandoDeLiberacao` no broker |
| `/health` | `GET` | Liveness do serviço |

Não existe rota para "aplicar" quarentena manualmente — essa decisão é
sempre automática, tomada pelo Quarantine Service a partir do risk score
(ver [ADR 004](../README.md#adr-004--sistema-de-detecção-out-of-band-sem-autoridade-de-bloqueio-síncrono)
no README principal). O papel do Administrador é só revisar e, se for o
caso, liberar.

## 2. Quais dados, em que formato?

JSON, refletindo os campos que o serviço recebe via evento (não dados
inventados na camada de API):

```jsonc
// GET /cases (resumo, um item da lista)
{
  "account_id": "C90045638",
  "status": "EM_QUARENTENA",       // ou "LIBERADA"
  "risk_score": 0.87,
  "quarantined_at": "2026-08-02T14:03:00Z",
  "updated_at": "2026-08-02T14:03:00Z"
}
```

```jsonc
// GET /cases/{account_id} (detalhe)
{
  "account_id": "C90045638",
  "status": "EM_QUARENTENA",
  "risk_score": 0.87,
  "motivo": "Padrão de transferências encadeadas entre contas correlacionadas",
  "eventos": [
    { "tipo": "ContaEmQuarentena", "occurred_at": "2026-08-02T14:03:00Z" }
  ]
}
```

A listagem (`GET /cases`) inclui metadados de paginação (`total`, `page`,
`page_size`) — necessários para a API ser usável, não é agregação de
negócio (ver seção 3).

## 3. O serviço processa os dados ou só repassa brutos?

**Decisão: repassa os dados essencialmente brutos.** O Admin Panel Service
funciona como uma projeção de leitura (read model) dos eventos de
quarentena — filtro e paginação são suportados porque são necessários para
qualquer API de listagem ser usável, mas **agregações analíticas não são
responsabilidade deste serviço**.


## Padrões arquiteturais

| Padrão | Aplica ao Admin Panel Service? | Motivação |
|---|---|---|
| **SAGA (Choreography)** | Sim (já documentado) | Consome `ContaEmQuarentena`/`ContaLiberada` e publica `ComandoDeLiberacao`, fechando a cadeia sem orquestrador central |
| **CQRS** | Proposto | O serviço mantém sua própria projeção de leitura, otimizada para consulta do Administrador, alimentada por eventos publicados pelo lado de escrita (Quarantine Service) — isso é, na prática, o lado de query de um CQRS cujo lado de comando vive em outro serviço |
| **Event Sourcing** | Proposto (audit trail das ações do Administrador) | O comando de liberação (`POST /cases/{id}/release`) é uma decisão humana que o sistema pode precisar justificar depois — guardar essas ações como um log append-only (quem liberou, quando, qual caso) é consistente com o motivo de auditabilidade já usado nas ADRs 002 e 004 do README principal |
| **Anti-corruption Layer** | Não se aplica | O serviço só consome eventos de domínio internos, já no formato canônico do sistema — não há fronteira com um formato externo para traduzir, como no caso do Ingestion Service |

Caso aprovadas, a tabela "Padrões
Arquiteturais Aplicados" do README principal deve ser atualizada para
refletir o Admin Panel Service nessas linhas.

## Próximos passos

Implementação depende de:
- Quarantine Service existir e publicar `ContaEmQuarentena`/`ContaLiberada` de fato.
- API Gateway substituir o proxy genérico `/admin/**` pelas rotas específicas listadas acima.
