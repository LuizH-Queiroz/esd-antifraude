# Simulador PaySim

Este componente representa o sistema externo que produz transações para o
Sistema Antifraude. A cada ciclo ele lê **uma transação da PaySim**, converte
a linha em uma mensagem JSON e a envia por HTTP ao API Gateway.

## Estratégia de leitura: sequencial (padrão) ou aleatória

> Esta seção documenta a resposta às duas perguntas em aberto na
> **Issue #5 ("Considerações de Arquitetura")**.

Por padrão (`SAMPLING_STRATEGY=sequential`), o simulador percorre o CSV **na
ordem original** — que já é cronológica, por `step` — em vez de sortear linhas
aleatórias com reposição (como em uma versão anterior deste componente).

Isso importa porque o Risk Scoring Service depende de observar **múltiplas
transações da mesma conta ao longo do tempo** para calcular o fator de risco
"correlação entre contas" (ver ADR 003 do README principal, que cita
`nameOrig`/`nameDest` como identificadores de cadeias de transferência). Um
sorteio aleatório uniforme entre milhões de linhas praticamente elimina a
chance de duas transações da mesma conta caírem em envios próximos no tempo —
o padrão que o Risk Scoring Service precisaria detectar simplesmente não
chegaria a existir no fluxo de eventos. A ADR 004 também já descrevia o
simulador como um "replay sequencial e espaçado no tempo"; o modo padrão atual
é o que implementa essa descrição.

O modo `SAMPLING_STRATEGY=random` (sorteio uniforme, com reposição) continua
disponível, mas deve ser usado apenas para testes de carga/stress, onde o que
importa é o volume de eventos, não a ordem/correlação entre eles.

## Por que existe um índice?

A PaySim possui milhões de linhas. O simulador não carrega tudo em memória nem
relê o CSV do início a cada execução. Na primeira execução ele faz uma única
passagem para criar um índice de offsets (posição em bytes de cada linha).
Esse mesmo índice é reaproveitado pelos dois modos de leitura:

- **Sequencial (padrão):** o índice é usado apenas para retomar rapidamente da
  posição salva no checkpoint (ver abaixo); a partir daí a leitura segue linha
  a linha, em ordem.
- **Aleatório:** a cada envio, sorteia-se um número de linha, consulta-se sua
  posição no índice e salta-se diretamente para ela no CSV.

## Retomando de onde parou (modo sequencial)

A posição atual (próxima linha a ler) é salva periodicamente em
`SEQUENTIAL_CHECKPOINT_PATH` (a cada `CHECKPOINT_EVERY_MESSAGES` envios, e
sempre ao encerrar o simulador, inclusive via Ctrl+C). Isso significa que
parar e reiniciar o simulador continua o "replay" de onde parou, em vez de
repetir sempre as mesmas primeiras linhas ou perder a posição.

Para forçar um recomeço do início (por exemplo, para uma nova demonstração),
use a flag `--reset-checkpoint`:

```bash
python main.py --reset-checkpoint --count 20
```

## Espaçamento entre envios (min/max)

A cada envio, o simulador aguarda um intervalo aleatório sorteado entre
`SEND_INTERVAL_MIN_SECONDS` e `SEND_INTERVAL_MAX_SECONDS` (em vez de uma
pausa fixa), para simular um fluxo de transações mais próximo do real — no
mundo real, eventos não chegam com espaçamento perfeitamente constante.

Os padrões (`0.5s` a `2.0s`) são um ritmo pensado para demonstração/aula,
fácil de acompanhar nos logs. Como referência para quem quiser aproximar do
throughput real da PaySim: a base tem 6.362.620 transações distribuídas em
744 `step`s de 1 hora simulada cada, ou seja, uma média de **~2,4
transações por segundo**. Para simular esse ritmo, algo como
`SEND_INTERVAL_MIN_SECONDS=0.2` / `SEND_INTERVAL_MAX_SECONDS=0.6` fica
mais próximo do real (ajuste conforme o que fizer sentido para a
demonstração do grupo).

## Preparação da base

Baixe o arquivo pela página da PaySim no Kaggle e coloque-o em:

```text
data/PS_20174392719_1491204439457_log.csv
```

Não renomeie nem versione o CSV. Mais detalhes estão em `data/README.md`.

## Teste local sem API Gateway

Na pasta `simulator`:

```bash
python main.py --once --dry-run
```

O comando cria/reutiliza o índice, lê uma transação e imprime o JSON.
Para imprimir cinco transações em sequência:

```bash
python main.py --count 5 --dry-run
```

## Teste local enviando para um Gateway

Em Linux/macOS:

```bash
export API_GATEWAY_URL=http://localhost:8080
export API_GATEWAY_ENDPOINT=/events/transactions
python main.py --count 10
```

Em PowerShell:

```powershell
$env:API_GATEWAY_URL = "http://localhost:8080"
$env:API_GATEWAY_ENDPOINT = "/events/transactions"
python main.py --count 10
```

O endpoint é configurável porque o contrato definitivo do API Gateway ainda
pode evoluir. A transformação do contrato fica isolada em `app/mapper.py`.

## Execução pelo Docker Compose

Depois de aplicar o trecho indicado em `root-changes/docker-compose.md`, execute
na raiz do repositório:

```bash
docker compose up --build simulator
```

Como o Gateway agora já existe de verdade (ver `api-gateway/README.md`), o
primeiro teste recomendado é:

```bash
docker compose run --rm simulator python main.py --once --dry-run
```

## Formato enviado

Exemplo resumido:

```json
{
  "event_id": "uuid",
  "event_type": "TRANSACTION_CREATED",
  "occurred_at": "2026-07-24T18:00:00Z",
  "source": "paysim-simulator",
  "transaction": {
    "step": 1,
    "type": "PAYMENT",
    "amount": 1060.31,
    "origin_account": "C429214117",
    "destination_account": "M1591654462"
  },
  "simulation_metadata": {
    "dataset": "PaySim",
    "selection": "sequential"
  }
}
```

O campo `simulation_metadata.selection` reflete o `SAMPLING_STRATEGY` em uso
(`sequential` ou `random`), para que quem inspecionar o evento saiba como
aquela transação foi escolhida.

Os quatro campos de saldo também ficam fora por padrão, porque a documentação
da PaySim alerta que eles não devem ser usados na detecção de fraude. Para um
teste de integração que precise reproduzir a linha completa, use
`INCLUDE_BALANCE_FIELDS=true`.

`isFraud` e `isFlaggedFraud` são o gabarito da base e ficam fora do evento por
padrão, evitando vazamento de resposta para o futuro risk-scoring-service. Para
um teste comparativo controlado, use `INCLUDE_GROUND_TRUTH=true`.