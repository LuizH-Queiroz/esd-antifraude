# Ingestion Service

Primeiro serviço interno do fluxo assíncrono: recebe eventos de transação
já roteados pelo API Gateway, aplica a Anti-corruption Layer, persiste no
Event Store (PostgreSQL) e publica no Message Broker (RabbitMQ) para
consumo futuro pelo Risk Scoring Service.

- API Gateway --(POST /internal/transactions)--> Ingestion Service
- Ingestion Service --(valida + traduz + persiste)--> Event Store (PostgreSQL)
- Ingestion Service --(publica TransacaoRegistrada)--> RabbitMQ

## Decisões de arquitetura (Issue "Ingestion Service e Message Broker")

**Ordem: persistir primeiro, publicar depois.** Se a publicação no
RabbitMQ falhar, o serviço responde 503 — mas o evento já está seguro no
Event Store (nada se perde). O Gateway propaga esse 503 para quem chamou,
e o simulador já trata esse código como retryable.

**Idempotência via `event_id`.** A tabela `events` tem `event_id` como
chave primária; uma tentativa de inserir um `event_id` repetido é
simplesmente ignorada (`ON CONFLICT DO NOTHING`), sem gerar erro. O header
`Idempotency-Key` (repassado pelo Gateway) é só informativo/para
depuração — a deduplicação de fato usa o campo `event_id`.

**Limitação conhecida (dual-write problem).** Persistir e publicar não são
atômicos: se o processo cair exatamente entre as duas operações, o evento
fica no banco mas nunca é publicado. A solução formal para isso é o padrão
*Transactional Outbox*, que não implementamos aqui por ser um salto de
complexidade desproporcional ao escopo da POC — mas é uma lacuna conhecida,
documentada em `ingestion/publisher.py`.

**Topologia do RabbitMQ:**

| Item | Valor |
|---|---|
| Exchange | `antifraude.eventos` (tipo `topic`, durável) |
| Fila | `ingestion.transacao-registrada` (durável) |
| Routing key | `transacao.registrada` |
| Mensagens | Persistentes (sobrevivem a um restart do broker) |

O Ingestion Service declara tanto a exchange quanto a fila (não só a
exchange). Isso é incomum para um serviço que só *produz* mensagens, mas é
necessário aqui: sem nenhuma fila vinculada, uma exchange `topic` descarta
mensagens publicadas sem consumidor. Declarar a fila agora garante que as
mensagens fiquem visíveis e inspecionáveis (via Management UI) mesmo antes
do Risk Scoring Service existir. Quando ele for implementado, vai declarar
a mesma fila (operação segura de repetir) e simplesmente começar a
consumir o que já estiver lá.

**Esquema do Event Store:** uma única tabela `events` (`event_id` chave
primária, `event_type`, `payload` em JSONB, `occurred_at`, `recorded_at`).
Sem `aggregate_id` por enquanto — dá pra extrair da própria `payload` via
JSONB se uma consulta futura do Risk Scoring Service precisar filtrar por
conta.

## Rodando localmente (sem Docker)

Requer um PostgreSQL e um RabbitMQ acessíveis (mais fácil: suba só essas
duas dependências via Docker Compose, e rode o serviço fora dele):

```bash
docker compose up -d ingestion-db rabbitmq
```

```bash
cd ingestion-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# ajuste DATABASE_URL/RABBITMQ_HOST para "localhost" com as portas
# publicadas no docker-compose.yml (5433 para o Postgres, 5672 para o
# RabbitMQ), já que fora do Docker os nomes dos serviços não resolvem.

uvicorn ingestion.main:app --reload --port 8001
```

## Rodando via Docker Compose

```bash
docker compose up --build ingestion-service
```

Isso sobe também `ingestion-db` e `rabbitmq` (dependências declaradas no
`docker-compose.yml` da raiz, com `condition: service_healthy`).

## Verificando manualmente

Com o serviço no ar (local ou via Docker Compose) e o Gateway também
rodando, dispare o simulador:

```bash
cd simulator
API_GATEWAY_URL=http://localhost:8080 python main.py --count 1
```

Depois, confira:

- **No Postgres** (`ingestion-db`, porta `5433` no host):
```bash
  docker compose exec ingestion-db psql -U ingestion -d ingestion_db -c "SELECT * FROM events;"
```
- **No RabbitMQ**: abra `http://localhost:15672` (usuário/senha
  `antifraud`/`antifraud`), vá em **Queues** → `ingestion.transacao-registrada`
  → **Get messages**, para ver a mensagem publicada de verdade.

## Testes automatizados

Os testes ficam em `tests/test_ingestion_service.py`, na raiz do
repositório (mesmo padrão do api-gateway), e usam `InMemoryEventStore` e
`InMemoryEventPublisher` — não precisam de PostgreSQL nem RabbitMQ reais:

```bash
cd esd-antifraude   # raiz do repositório
source .venv/bin/activate    # ou crie um novo, se necessário
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

## Variáveis de ambiente

Ver `.env.example` para a lista completa e valores padrão.

## Estrutura do código

ingestion-service/

├── Dockerfile

├── requirements.txt

├── pyproject.toml

├── .env.example

└── ingestion/

├── config.py # leitura centralizada de variáveis de ambiente

├── schemas.py # validação estrutural do evento recebido

├── domain.py # Anti-corruption Layer (evento externo -> domínio)

├── event_store.py # Event Store: PostgresEventStore + InMemoryEventStore

├── publisher.py # RabbitMQ: RabbitMQEventPublisher + InMemoryEventPublisher

├── main.py # monta a app, injeta store/publisher (produção ou testes)

└── routes/

├── transactions.py # POST /internal/transactions

└── health.py # GET /health