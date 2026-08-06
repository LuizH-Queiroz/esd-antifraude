"""Risk Scoring Service do Sistema Antifraude.

Consome eventos `TransacaoRegistrada` do RabbitMQ (publicados pelo
Ingestion Service), calcula a probabilidade de fraude de cada transação
usando o modelo treinado em `ml/` (RandomForestClassifier, ver
`ml/README.md`), e mantém um risk score incremental por conta,
persistido no `risk-scoring-db`.

Diferente do API Gateway e do Ingestion Service (que respondem a
requisições HTTP), este serviço é, no fundo, um CONSUMIDOR de fila rodando
em segundo plano — a API HTTP que ele expõe (`/health`, `/accounts/{id}`)
é secundária, só para inspeção manual e para o `/health/dependencies` do
Gateway conseguir checar se este serviço está de pé.
"""