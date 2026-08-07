# Quarantine Service

Microsserviço responsável por aplicar e liberar a quarentena de contas com base em eventos de risco e comandos recebidos do Admin Panel Service.

## Papel no fluxo antifraude

- Consome comandos de liberação publicados pelo Admin Panel via RabbitMQ.
- Mantém o estado oficial da conta (`EM_QUARENTENA` ou `LIBERADA`) para o ciclo de vida da conta suspeita.
- Publica eventos de quarentena e liberação para o mesmo broker, seguindo o contrato do projeto.
- Usa idempotência por `event_id` para evitar efeitos duplicados quando o RabbitMQ entrega a mesma mensagem mais de uma vez.

## Arquitetura interna

O serviço foi organizado em quatro blocos principais:

- `quarantine/config.py`: leitura centralizada das variáveis de ambiente.
- `quarantine/broker.py`: publicação em RabbitMQ com fallback em memória para testes.
- `quarantine/repository.py`: armazenamento do estado da conta e registro de comandos já processados.
- `quarantine/consumer.py`: consumidor assíncrono da fila `quarantine.comando-liberacao`.

## Contrato RabbitMQ

| Item | Valor |
|---|---|
| Exchange | `antifraude.eventos` |
| Fila de comandos | `quarantine.comando-liberacao` |
| Routing key de comando | `comando.liberacao` |
| Routing key de quarentena | `conta.em-quarentena` |
| Routing key de liberação | `conta.liberada` |

## Variáveis de ambiente

O arquivo [.env.example](.env.example) centraliza os valores padrão do serviço. As variáveis mais importantes são:

- `DATABASE_URL`
- `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD`
- `RABBITMQ_EXCHANGE`
- `RABBITMQ_RELEASE_COMMAND_QUEUE`
- `RABBITMQ_RELEASE_COMMAND_ROUTING_KEY`
- `RABBITMQ_QUARANTINE_ROUTING_KEY`
- `RABBITMQ_RELEASED_ROUTING_KEY`

## Rodando localmente

```bash
cd quarantine-service
cp .env.example .env
pip install -r requirements.txt
uvicorn quarantine.main:app --reload --port 8003
```

## Rodando via Docker Compose

```bash
docker compose up --build quarantine-service
```

## Fluxo esperado de integração

1. O Risk Scoring Service publica um evento `ContaEmQuarentena` a partir de um score alto.
2. O Quarantine Service atualiza o estado da conta para `EM_QUARENTENA` e publica o evento correspondente.
3. O Admin Panel recebe o evento e mostra a conta em revisão manual.
4. Quando o administrador solicita a liberação, o Admin Panel publica um `ComandoDeLiberacao` na fila `quarantine.comando-liberacao`.
5. O Quarantine Service consome esse comando, aplica a transição para `LIBERADA` e publica `ContaLiberada`.

## Testes

```bash
cd esd-antifraude
pytest -q tests/test_quarantine_service.py
```

## Estrutura do código

- `quarantine/config.py`: leitura de variáveis de ambiente.
- `quarantine/broker.py`: abstração de publicação no RabbitMQ com fallback em memória.
- `quarantine/repository.py`: armazenamento do estado e idempotência para comandos.
- `quarantine/processor.py`: regras de negócio para processamento de liberação.
- `quarantine/consumer.py`: consumidor assíncrono da fila RabbitMQ.
- `quarantine/routes/health.py`: endpoint `GET /health`.
- `quarantine/routes/internal.py`: endpoints internos para testes e integração.
