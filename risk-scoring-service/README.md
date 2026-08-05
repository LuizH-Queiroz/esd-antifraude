# Risk Scoring Service

Consome eventos `TransacaoRegistrada` do RabbitMQ (publicados pelo
Ingestion Service), aplica o modelo de fraude treinado (ver `ml/README.md`)
a cada transação, e mantém um **risk score incremental por conta**,
persistido no `risk-scoring-db`.

- RabbitMQ --(consome transacoes.registradas)--> Risk Scoring Service
- Risk Scoring Service --(lê histórico da conta)--> account_stats (Postgres)
- Risk Scoring Service --(roda o modelo .joblib)--> p_fraud
- Risk Scoring Service --(atualiza histórico)--> account_stats (Postgres)

## Por que isto é mais que "chamar o modelo"

O modelo (`ml/train.py`) foi treinado com **15 features** por transação
(ver `ml/README.md`), mas só 4 delas vêm direto da transação em si
(`amount`, `type`, e as derivadas `amount_log`/`hour_of_day`/`day_index`
via `step`). As outras 6 (`orig_prior_*`, `dest_prior_*`,
`*_seen_as_*_before`) dependem do **histórico da conta ao longo do
tempo** — no treino, calculado em lote sobre o CSV inteiro
(`cumcount`/`cumsum` causais); aqui, mantido de forma incremental, conta a
conta, na tabela `account_stats`.

## Duas decisões de coerência com o pipeline de ML (importantes)

**1. Timestamp simulado, não real.** As features de padrão temporal
(`hour_of_day`, `day_index`) usam o timestamp **derivado do `step`** da
transação (mesma fórmula de `ml/data.py`: `SIMULATION_START + (step - 1)
horas`), não o horário real (`occurred_at`) em que o evento chegou. Usar
`occurred_at` daria ao modelo uma distribuição de "hora do dia" diferente
da que ele foi treinado para reconhecer — ver `risk_scoring/features.py`.

**2. Constantes importadas de `ml/`, não duplicadas.** Como `ml/` vive
DENTRO desta pasta (`risk-scoring-service/ml/`), faz parte do mesmo
serviço/build Docker — `risk_scoring/features.py` importa
`SIMULATION_START`/`TRANSACTION_TYPES` diretamente de `ml.config`, sem
duplicação. O que `tests/test_feature_parity.py` continua verificando é
outra coisa: que a reimplementação **escalar** (linha a linha, para uma
transação por vez, em `build_stateless_features()`) produz exatamente o
mesmo resultado que o pipeline em lote (`build_transaction_features()`,
usado no treino, que opera sobre um DataFrame inteiro) — as duas lógicas
são escritas de formas diferentes, então vale testar que concordam.

## O esquema `account_stats`

Uma linha por conta, cobrindo tanto as features causais quanto o
`risk_score` agregado (mesma fórmula de `ml/score_accounts.py`:
`risk_score = (1 - produto(1 - p_fraude)) * 100`, calculada
incrementalmente via soma de log-sobrevivência, em vez de em lote):

| Coluna | Uso |
|---|---|
| `tx_count_as_orig`, `amount_sum_as_orig`, `first_seen_as_orig_at` | histórico como remetente |
| `tx_count_as_dest`, `amount_sum_as_dest`, `first_seen_as_dest_at` | histórico como destinatária |
| `log_survival_sum` | soma de `log1p(-p_fraud)` de toda transação em que a conta participou (como remetente OU destinatária) — vira `risk_score` via `(1 - exp(soma)) * 100` |
| `p_fraud_sum`, `involvement_count` | para `mean_p_fraud` |
| `max_p_fraud`, `high_risk_tx_count`, `total_amount`, `last_activity` | espelham as colunas de saída de `ml/score_accounts.py` |

Para cada evento, o fluxo é: **ler** o estado atual das duas contas
envolvidas → montar o vetor de features → rodar o modelo → **só depois**
atualizar o estado das duas contas com esta transação. Essa ordem
preserva a causalidade (uma transação nunca "vê" a si mesma no próprio
histórico), igual ao treino.

## Limitação conhecida: processamento precisa ser sequencial

Esse esquema só é causal se as mensagens forem processadas **uma de cada
vez, na mesma ordem em que foram publicadas** — por isso o consumidor
(`consumer.py`) não usa nenhum paralelismo. Isso já é compatível com o
projeto hoje (simulador em modo sequencial — ver `simulator/README.md` —,
um único consumidor). Se um dia o grupo quiser escalar horizontalmente
(múltiplas instâncias consumindo a mesma fila), esse esquema de estado por
conta quebra, a menos que o consumo seja particionado por `account_id`. Não
é um problema agora, mas vale registrar como trade-off consciente.

## Rodando localmente (sem Docker)

Requer PostgreSQL, RabbitMQ **e o modelo já treinado**. Note que `ml/`
vive DENTRO desta pasta — os comandos de treino também rodam a partir
daqui, não da raiz do repositório:

```bash
    docker compose up -d risk-scoring-db rabbitmq
    cd risk-scoring-service
    python -m ml.train           # gera ml/artifacts/fraud_classifier.joblib
```

```bash
cd risk-scoring-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# ajuste DATABASE_URL/RABBITMQ_HOST/MODEL_PATH para rodar fora do Docker

uvicorn risk_scoring.main:app --reload --port 8002
```

## Rodando via Docker Compose

```bash
    cd risk-scoring-service
    python -m ml.train   # se ainda não tiver rodado
    cd ..
    docker compose up --build risk-scoring-service
```

Como ml/ vive dentro de risk-scoring-service/, o volume que já monta a pasta inteira também expõe ml/artifacts/fraud_classifier.joblib automaticamente — não é preciso nenhum volume extra para o modelo.

## Verificando manualmente

Com o fluxo completo no ar (simulador → gateway → ingestion-service →
RabbitMQ → **este serviço**):

```bash
cd simulator
API_GATEWAY_URL=http://localhost:8080 python main.py --count 5
```

Depois:

```bash
curl http://localhost:8002/accounts/<ID_DE_UMA_CONTA_QUE_APARECEU_NOS_LOGS>
```

Ou direto no banco:
```bash
docker compose exec risk-scoring-db psql -U risk_scoring -d risk_scoring_db -c "SELECT * FROM account_stats ORDER BY log_survival_sum LIMIT 10;"
```

## Testes automatizados

```bash
cd esd-antifraude
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

`tests/test_risk_scoring_service.py` usa `InMemoryAccountStatsStore` e um
modelo fake — não precisa de Postgres/RabbitMQ/`.joblib` real.
`tests/test_feature_parity.py` compara as features deste serviço com as
de `ml/features.py` (precisa de `pandas`/`numpy` instalados, mas não do
modelo treinado).

## Estrutura do código

risk-scoring-service/

├── Dockerfile

├── requirements.txt

├── pyproject.toml

├── .env.example

├── ml/ # pipeline de treino/scoring em lote (dataset, features, modelo)

└── risk_scoring/

├── config.py # settings + constantes duplicadas de ml/config.py

├── features.py # features sem estado (step -> timestamp -> features)

├── model.py # carrega o .joblib, roda predict_proba

├── account_stats.py # estado causal por conta + risk score (Postgres/InMemory)

├── consumer.py # consumidor RabbitMQ (aio-pika)

├── scoring.py # orquestra: ler estado -> features -> prever -> atualizar estado

├── main.py # FastAPI (/health, /accounts) + task do consumidor

└── routes/

├── health.py

└── accounts.py
