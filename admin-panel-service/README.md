# Admin Panel Service

Backend do Painel Admin do Sistema Antifraude. O serviço:

- consome `ContaEmQuarentena` e `ContaLiberada` do RabbitMQ;
- mantém uma projeção consultável no PostgreSQL;
- guarda o histórico append-only dos eventos e ações administrativas;
- expõe consultas REST;
- publica `ComandoDeLiberacao` quando um administrador solicita a liberação.

## Rotas

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/cases` | Lista casos com filtros e paginação |
| `GET` | `/cases/{account_id}` | Detalha um caso e seu histórico |
| `POST` | `/cases/{account_id}/release` | Registra e publica um comando de liberação |

Filtros de `GET /cases`: `status`, `date_from`, `date_to`, `min_score`,
`max_score`, `page` e `page_size`.

Pelo API Gateway, as mesmas chamadas usam o prefixo `/admin`, por exemplo:
`GET http://localhost:8080/admin/cases`.

## Contratos do RabbitMQ

Exchange topic durável: `antifraude.eventos`.

### Evento `ContaEmQuarentena`

Routing key: `conta.em-quarentena`.

```json
{
  "event_id": "q-123",
  "event_type": "ContaEmQuarentena",
  "occurred_at": "2026-08-04T18:00:00Z",
  "account_id": "C90045638",
  "risk_score": 0.87,
  "motivo": "Padrão de transferências encadeadas"
}
```

### Evento `ContaLiberada`

Routing key: `conta.liberada`.

```json
{
  "event_id": "l-123",
  "event_type": "ContaLiberada",
  "occurred_at": "2026-08-04T18:15:00Z",
  "account_id": "C90045638",
  "released_by": "quarantine-service"
}
```

Os campos de domínio também podem vir dentro de `payload`; isso reduz o
acoplamento enquanto o Quarantine Service ainda está sendo implementado.

### Comando `ComandoDeLiberacao`

Routing key: `comando.liberacao`. O serviço declara a fila durável
`quarantine.comando-liberacao`, evitando perda do comando antes de o
Quarantine Service começar a consumir.

```json
{
  "event_id": "command-uuid",
  "event_type": "ComandoDeLiberacao",
  "occurred_at": "2026-08-04T18:10:00+00:00",
  "account_id": "C90045638",
  "requested_by": "professor-admin",
  "motivo": "Revisão manual concluída"
}
```

## Execução

Na raiz do repositório:

```bash
docker compose up --build admin-panel-service api-gateway
```

Swagger direto do serviço: `http://localhost:8004/docs`.

## Exemplos

```bash
curl "http://localhost:8080/admin/cases?status=EM_QUARENTENA&page=1&page_size=20"

curl "http://localhost:8080/admin/cases/C90045638"

curl -X POST "http://localhost:8080/admin/cases/C90045638/release" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: liberacao-C90045638-1" \
  -d '{"requested_by":"reuben","motivo":"Revisão manual"}'
```

## Consistência e idempotência

Eventos recebidos são deduplicados por `event_id`. O estado da conta só muda
para `LIBERADA` quando chega `ContaLiberada`; publicar o comando não altera a
projeção antecipadamente.

A ação humana é gravada antes da publicação no RabbitMQ. Caso a publicação
falhe, a API retorna `503`; o cliente deve repetir usando o mesmo header
`Idempotency-Key`. Assim o audit log não duplica, mas a publicação pode ser
refeita com semântica at-least-once.

## Testes

A partir da raiz do repositório:

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

Os testes usam repositório e broker em memória; PostgreSQL e RabbitMQ não são
necessários para a suíte unitária.

## Testando sem o Quarantine Service

Como o Quarantine Service ainda é um placeholder no repositório, publique um
caso de demonstração de dentro do container:

```bash
docker compose exec admin-panel-service python scripts/publish_sample_event.py
curl http://localhost:8080/admin/cases
```
