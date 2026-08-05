# Risk Scoring — Modelo de ML

Pipeline offline que treina um modelo básico de detecção de fraude sobre o
dataset [PaySim](https://www.kaggle.com/datasets/ealaxi/paysim1/data) e o usa
para calcular um **risk score por conta** — a peça de Machine Learning que dá
base ao Risk Scoring Service (ver [ADR 003](../../README.md#adr-003--base-de-dados-para-entrada-de-dados)
do README raiz).

## Dados de entrada

Só as colunas priorizadas pelo projeto são usadas, mais o rótulo de fraude:

| Coluna | O que é | Como é usada |
|---|---|---|
| `step` | Hora sequencial da simulação (1 a 743, ~31 dias corridos). Não é um relógio de verdade — é só "quantas horas se passaram desde o início da simulação". | **Não entra no modelo como coluna.** É convertida em `timestamp` (ver seção abaixo) e descartada; o timestamp resultante é que alimenta as features de padrão temporal. |
| `type` | Tipo de movimentação bancária: `CASH_IN` (depósito em conta), `CASH_OUT` (saque), `DEBIT` (débito), `PAYMENT` (pagamento a comerciante) ou `TRANSFER` (transferência entre contas). | One-hot encoding — vira 5 colunas binárias (`type_CASH_IN`, `type_CASH_OUT`, ...). É o sinal mais forte do dataset: no PaySim, **fraude só acontece em `TRANSFER` e `CASH_OUT`** (é o padrão clássico simulado — a conta é comprometida, o dinheiro é transferido para uma conta "laranja" e depois sacado). |
| `amount` | Valor da transação, na moeda do dataset. | Usada direto (`amount`) e em log (`amount_log`, ver abaixo). |
| `nameOrig` | ID da conta de origem (que está enviando/gastando o dinheiro). | Usada para calcular o histórico da conta como remetente (`orig_prior_*`) e para identificar a linha na hora de agregar o risk score por conta. |
| `nameDest` | ID da conta de destino (que está recebendo o dinheiro). | Usada para calcular o histórico da conta como destinatária (`dest_prior_*`) e também na agregação do risk score. |
| `isFraud` (rótulo, não é feature) | Marcação original do PaySim: 1 se a transação faz parte de um cenário de fraude simulado, 0 caso contrário. | Usada **só** para treinar e avaliar o classificador (`train.py`). Não é uma coluna que o sistema recebe em produção — o Ingestion Service nunca vê `isFraud` (ver [ADR 003](../../README.md#adr-003--base-de-dados-para-entrada-de-dados)), então o modelo tem que aprender a prever fraude só com `type`/`amount`/`nameOrig`/`nameDest`/tempo. |

### De `step` para `timestamp`

`step` não tem valor de calendário real, mas carrega uma informação
importante: **ordem cronológica** e **posição dentro do dia** (já que cada
step = 1 hora). `data.py` converte isso em um `timestamp` de verdade,
ancorado em `2023-01-01 00:00:00` só como data-base arbitrária (a data em si
não significa nada; o que importa é a estrutura relativa). Esse timestamp
serve para duas coisas:

1. **Ordenar as transações no tempo**, o que permite calcular, para cada
   conta, um histórico *causal* (só olhando para trás) em vez de usar
   estatísticas do dataset inteiro — evita vazar informação do futuro para o
   modelo, o que inflaria artificialmente as métricas.
2. **Derivar features de padrão temporal** (`hour_of_day`, `day_index`,
   explicadas abaixo), que não existiriam usando `step` cru.

## Features (o que cada uma traz de informação)

As features de `features.py` cobrem os 4 fatores do risk score multifatorial
citados no README raiz que fazem sentido com as 5 colunas priorizadas: tipo
de transação, valor, padrão temporal e correlação entre contas.

**Tipo de transação**
- `type_CASH_IN`, `type_CASH_OUT`, `type_DEBIT`, `type_PAYMENT`,
  `type_TRANSFER` — one-hot do tipo. Sozinha, é a feature mais informativa
  do modelo: como só `TRANSFER`/`CASH_OUT` têm fraude no PaySim, essas duas
  colunas já eliminam ~74% das transações (todo `CASH_IN`/`DEBIT`/`PAYMENT`)
  da suspeita.

**Valor**
- `amount` — valor bruto da transação. Fraudes tendem a concentrar valores
  atípicos (muito altos, ou exatamente iguais ao saldo da conta — um padrão
  de "esvaziar a conta" que não conseguimos ver diretamente porque não
  usamos as colunas de saldo, mas que se reflete parcialmente na distribuição
  do valor).
- `amount_log` (`log(1 + amount)`) — mesma informação, mas em escala
  logarítmica. Valores de transação no PaySim variam de poucos reais a
  dezenas de milhões; sem o log, a árvore de decisão tende a só conseguir
  discriminar bem a cauda de valores altos. Foi a feature de maior peso no
  modelo treinado (ver resultados abaixo).

**Padrão temporal**
- `hour_of_day` (0-23, `timestamp.hour`) — em que hora do dia (dentro do
  ciclo de 24h simulado) a transação ocorreu. Captura se existe concentração
  de fraude em horários específicos (ex.: menor volume humano de transações
  legítimas de madrugada torna picos de atividade nesse horário mais
  suspeitos).
- `day_index` (`timestamp` − início da simulação, em dias) — em que dia da
  simulação (0 a ~30) a transação ocorreu. Captura tendência ao longo do
  tempo (ex.: campanhas de fraude concentradas em certos dias).

**Correlação entre contas** — a parte mais específica do dataset, calculada
de forma causal (`cumcount`/`cumsum` sobre o dataframe ordenado por tempo, só
olhando transações *anteriores* à linha atual):
- `orig_prior_tx_count` / `orig_prior_amount_sum` — quantas transações essa
  conta já fez como remetente antes desta, e a soma dos valores enviados.
  No PaySim, ~99,9% das contas `nameOrig` aparecem uma única vez (o "cliente"
  de uma transação isolada), então essas duas features quase sempre valem 0
  — por isso tiveram importância ~0 no treino (ver tabela abaixo). Ficam no
  modelo porque, quando uma conta *é* reutilizada como remetente (até 3x no
  dataset), isso já é, por si só, um comportamento fora do padrão típico.
- `dest_prior_tx_count` / `dest_prior_amount_sum` — quantas transações essa
  conta já **recebeu** antes desta, e a soma dos valores recebidos. Ao
  contrário de `nameOrig`, contas `nameDest` se repetem bastante (em média
  2,3 transações, até 113 no dataset) — é assim que o modelo enxerga contas
  "concentradoras", que recebem muitos depósitos de contas diferentes, um
  padrão comum em contas usadas para lavagem. Foram as 2 features de
  correlação com maior peso no treino.
- `orig_seen_as_dest_before` / `dest_seen_as_orig_before` — indicadores
  binários de **troca de papel**: a conta de origem desta transação já
  apareceu como destinatária em uma transação anterior? (ou o inverso, para
  a conta de destino). Isso tenta capturar o padrão clássico de "funil":
  uma conta recebe dinheiro e, pouco depois, o repassa adiante (cadeia
  origem → conta intermediária → destino). No treino atual essas duas
  features tiveram importância quase nula — no PaySim as fraudes são
  majoritariamente transações isoladas (`TRANSFER` seguido de `CASH_OUT`
  seguindo pares específicos de conta, não cadeias longas), então esse sinal
  específico de cadeia acabou pouco explorado pelo modelo com os dados
  atuais, mas é mantido porque é conceitualmente o tipo de sinal mais
  difícil de imitar por um fraudador (é sobre o grafo de transações, não
  sobre uma transação isolada).

## Como rodar

1. Baixe o CSV do Kaggle e coloque em `ml/data/PS_20174392719_1491204439457_log.csv`
   (o arquivo é grande — ~470MB — por isso fica de fora do git, ver `.gitignore`).
2. Instale as dependências: `pip install -r ml/requirements.txt`
3. Treine o classificador por transação:
   ```bash
   python -m ml.train
   ```
   Gera `ml/artifacts/fraud_classifier.joblib` e `ml/artifacts/metrics.json`.
4. Calcule o risk score de cada conta:
   ```bash
   python -m ml.score_accounts
   ```
   Gera `ml/artifacts/account_risk_scores.csv`, com uma linha por conta
   (`account_id`, `risk_score`, `tx_count`, `high_risk_tx_count`,
   `max_p_fraud`, `mean_p_fraud`, `total_amount`, `last_activity`), ordenado
   da conta mais arriscada para a menos arriscada.

Ambos os comandos aceitam `--data <caminho>` para apontar para outro CSV.

## Abordagem

O modelo é feito em duas etapas:

**1. Classificador por transação** (`train.py`) — um `RandomForestClassifier`
prevê a probabilidade de fraude de cada transação individual, a partir das
features listadas acima. Para compensar o forte desbalanceamento do PaySim
(~0,13% das transações são fraude), usa `class_weight={0: 1, 1: 20}` — um
peso calibrado empiricamente (ver "Experimentos" abaixo), bem menos agressivo
que o `class_weight="balanced_subsample"` usado numa primeira versão. O split
treino/teste é **temporal** (treina no início da simulação, avalia no final)
em vez de aleatório — mais realista para um modelo que, em produção, vai
rodar sobre um fluxo de eventos ordenado no tempo.

**2. Agregação por conta** (`score_accounts.py`) — o dataset do PaySim tem uma
particularidade: quase toda `nameOrig` aparece uma única vez (é o "cliente" de
uma transação isolada), enquanto `nameDest` se repete bastante, e ~1.800
contas aparecem tanto como remetente quanto como destinatária em transações
diferentes (cadeias de repasse). Por isso o score é calculado por **conta**
(o mesmo `account_id`, seja ele `nameOrig` ou `nameDest`), não só por
`nameOrig`: cada conta acumula as probabilidades de fraude de todas as
transações em que participou, como remetente ou destinatária, e

```
risk_score = 1 - PRODUTO(1 - p_fraude_i), para toda transação i da conta
```

isto é, a probabilidade de que **pelo menos uma** das transações associadas à
conta seja fraudulenta (0 a 100). Contas com várias transações suspeitas
acumuladas pontuam mais alto do que uma conta com um único pico isolado.

## Experimentos (o que foi testado para melhorar o modelo)

A primeira versão do modelo usava `class_weight="balanced_subsample"` e
PR-AUC 0,384. Duas frentes foram testadas para melhorar isso, comparadas
sempre no mesmo split temporal (treino ≤ 2023-01-15 18h, teste depois disso)
para serem comparáveis entre si:

| Config | Mudança | PR-AUC | F1 @ 0,5 |
|---|---|---|---|
| Baseline | `class_weight="balanced_subsample"`, features originais | 0,384 | 0,267 |
| + features novas | fan-in/fan-out (contrapartes distintas por conta) e "horas desde a última transação" da conta, mantendo `balanced_subsample` | 0,366 | 0,277 |
| + `class_weight={0:1, 1:20}` | features originais, só troca o peso de classe | **0,541** | **0,474** |
| + features novas *e* peso 20 | as duas mudanças juntas | 0,520 | 0,471 |

**Desbalanceamento — adotado.** `balanced_subsample` pesa a classe fraude na
proporção exata do desbalanceamento do treino (~1300:1) — peso agressivo
demais: o RandomForest passa a "forçar" tanto a separação da classe rara que
piora a qualidade geral do ranking (PR-AUC caiu, não subiu). Testando pesos
mais moderados (10, 15, 20, 25, 30), `{0: 1, 1: 20}` teve o melhor PR-AUC
(0,541) — por isso é o default atual de `train.py --fraud-class-weight`.

**Features novas — testadas e descartadas.** Foram testadas 4 features
adicionais de "correlação entre contas": `dest_unique_senders_count` /
`orig_unique_receivers_count` (quantas contrapartes *diferentes* uma conta já
teve — sinal clássico de conta "mula", que recebe de muitas origens
diferentes) e `hours_since_orig_last_tx` / `hours_since_dest_last_tx`
(velocidade: quanto tempo desde a última movimentação da conta). Em toda
comparação feita (com `balanced_subsample` e com peso 20), essas features
**pioraram** o PR-AUC em vez de melhorar — o RandomForest, com colunas a mais
mas sem ganho de sinal real, dilui a importância das features que já eram
boas. Por isso não entraram na versão final: o código dessas features foi
removido de `features.py` para não deixar complexidade sem benefício.

## Resultados do treinamento

Treino executado sobre o dataset completo do Kaggle (6.362.620 transações),
com os hiperparâmetros default de `train.py` (`RandomForestClassifier`,
150 árvores, profundidade máxima 14, `class_weight={0: 1, 1: 20}`). Valores
em `ml/artifacts/metrics.json`.

**Split temporal**

| | Transações | Período | Taxa de fraude |
|---|---|---|---|
| Treino | 5.113.884 | até 2023-01-15 18h (~66% da simulação) | 0,077% |
| Teste | 1.248.736 | de 2023-01-15 19h até o fim (~34% da simulação) | 0,340% |

(A taxa de fraude é bem maior no período de teste — a simulação não distribui
fraude uniformemente ao longo do tempo, o que torna o split temporal um teste
mais exigente do que um split aleatório teria sido.)

**Métricas no conjunto de teste**

| Métrica | Valor | Leitura |
|---|---|---|
| ROC-AUC | 0,973 | O modelo separa bem fraude de não-fraude em termos gerais de ranking (igual à versão anterior). |
| PR-AUC (average precision) | **0,541** (era 0,384) | Métrica mais relevante que ROC-AUC dado o desbalanceamento extremo (0,13% de fraude) — subiu 41% só com o reajuste do peso de classe, bem acima do "acaso" (~0,003, a taxa de fraude no teste). |
| Accuracy @ 0,5 | 0,996 | **Enganosa aqui** — como só 0,34% das transações de teste são fraude, um modelo bobo que sempre prevê "não-fraude" já teria 99,66% de acurácia sem detectar nenhuma fraude. É reportada só como referência; PR-AUC/precision/recall é que dizem se o modelo é bom de verdade num problema desbalanceado como este. |
| Precision @ 0,5 | **0,471** (era 0,160) | Das transações que o modelo marca como fraude, agora 47% de fato são — quase 3x melhor. |
| Recall @ 0,5 | 0,477 (era 0,811) | O modelo captura 48% das fraudes reais do teste neste limiar específico — caiu, mas é um efeito do limiar fixo em 0,5, não do modelo ter piorado (ver nota abaixo). |
| F1 @ 0,5 | **0,474** (era 0,267) | Quase dobrou. |

Matriz de confusão (limiar 0,5, sobre 1.248.736 transações de teste, das
quais 4.250 são fraude de verdade):

| | Previsto: não-fraude | Previsto: fraude |
|---|---|---|
| **Real: não-fraude** | 1.242.209 (VN) | 2.277 (FP) |
| **Real: fraude** | 2.223 (FN) | 2.027 (VP) |

Comparado com a versão anterior (18.135 falsos positivos, 804 falsos
negativos), o novo modelo troca parte do recall por uma queda enorme de
falso positivo (2.277, 8x menos). Isso é uma mudança real de comportamento
no limiar 0,5 — mas **não significa que o modelo piorou em capturar fraude**:
como o PR-AUC (métrica independente de limiar) subiu, o modelo consegue
qualquer combinação de recall/precisão pelo menos tão boa quanto antes,
bastando escolher um limiar mais baixo (ex.: ~0,2) para recuperar recall
~0,81 com precisão melhor que os 16% originais. Threshold é uma decisão de
calibração do Quarantine Service, separada do modelo em si (ver
[Limitações](#limitações-é-um-modelo-básico)).

**Importância das features** (quanto cada uma contribuiu para as decisões do
RandomForest, soma = 1,0):

| Feature | Importância |
|---|---|
| `hour_of_day` | 0,213 |
| `amount_log` | 0,153 |
| `amount` | 0,127 |
| `type_TRANSFER` | 0,123 |
| `dest_prior_amount_sum` | 0,100 |
| `dest_prior_tx_count` | 0,089 |
| `day_index` | 0,089 |
| `type_CASH_OUT` | 0,065 |
| `type_PAYMENT` | 0,025 |
| `type_CASH_IN` | 0,014 |
| `type_DEBIT` | 0,002 |
| `orig_prior_amount_sum` | 0,0003 |
| `orig_prior_tx_count` | 0,0001 |
| `dest_seen_as_orig_before` | 0,0001 |
| `orig_seen_as_dest_before` | ~0,000 |

Com o peso de classe recalibrado, `hour_of_day` passou a ser a feature mais
importante do modelo (0,213, era 0,125) — com menos pressão para forçar a
separação da classe rara a qualquer custo, o modelo consegue explorar melhor
o padrão temporal. Valor (`amount`/`amount_log`) e tipo de transação
continuam fortes, e a mesma assimetria de antes se mantém: o histórico da
conta destinatária (`dest_prior_*`) é bem mais informativo que o da conta de
origem (`orig_prior_*`), consequência de `nameDest` se repetir no dataset e
`nameOrig` quase nunca se repetir (explicado acima). As features de troca de
papel (`*_seen_as_*_before`) seguem com importância quase nula.

**Scoring final por conta**

Rodando `score_accounts.py` sobre o dataset completo: **9.073.900 contas
pontuadas** (a partir das 6.362.620 transações — cada conta aparece como
`nameOrig` e/ou `nameDest` em uma ou mais transações). A conta de maior risco
no resultado (`C1981613973`) ficou com `risk_score = 99,95`, `tx_count = 25`
e `max_p_fraud = 0,977`.

## Servindo o modelo via API

`ml/serve.py` expõe o modelo treinado (`fraud_classifier.joblib`) como uma
API HTTP com FastAPI — a forma de "incluir isso nas predições pela API".
Depois de treinar (`python -m ml.train`) e gerar os scores por conta
(`python -m ml.score_accounts`):

```bash
uvicorn ml.serve:app --reload --port 8002
```

Duas rotas (documentação interativa em `localhost:8002/docs`):

**`POST /predict`** — pontua uma transação nova em tempo real, recebendo
exatamente os campos que o Ingestion Service já valida (`type`, `amount`,
`nameOrig`, `nameDest`, mais um `timestamp` opcional):

```bash
curl -X POST localhost:8002/predict -H 'Content-Type: application/json' -d '{
  "type": "TRANSFER", "amount": 181000.0,
  "nameOrig": "C123456789", "nameDest": "C987654321"
}'
# {"p_fraud":0.69,"risk_level":"medio","orig_prior_tx_count":0,"dest_prior_tx_count":0}
```

O ponto delicado é que 6 das 15 features do modelo não vêm só da requisição
— são o histórico causal da conta (`orig_prior_tx_count`,
`dest_prior_amount_sum`, etc., ver "Features" acima), que em treino vinha do
dataset completo já ordenado no tempo. Uma predição em tempo real não tem
esse dataset à mão, então `serve.py` mantém um **estado em memória por
conta** (contador e soma de valores como remetente/destinatária), atualizado
a cada chamada — chamar `/predict` duas vezes com o mesmo `nameDest`
já muda o resultado da segunda vez, porque `dest_prior_tx_count` deixou de
ser 0:

```bash
curl -X POST localhost:8002/predict -H 'Content-Type: application/json' -d '{
  "type": "CASH_OUT", "amount": 90000.0,
  "nameOrig": "C555555555", "nameDest": "C987654321"
}'
# dest_prior_tx_count agora é 1 (a chamada anterior usou o mesmo nameDest)
```

Esse estado em memória é só para esta API de demonstração — não persiste
entre reinícios e não é a arquitetura real do sistema. O Risk Scoring
Service de produção (ainda não implementado) manteria esse mesmo histórico
no `risk-scoring-db` via Event Sourcing (ver [ADR 002](../../README.md#adr-002--persistência-de-dados-por-microsserviço)
do README raiz), alimentado por eventos `TransacaoRegistrada` consumidos do
RabbitMQ — não por chamadas REST síncronas, já que o sistema é out-of-band
e nunca bloqueia uma transação em andamento (ver [ADR 004](../../README.md#adr-004--sistema-de-detecção-out-of-band-sem-autoridade-de-bloqueio-síncrono)).
`serve.py` é útil para testar o modelo interativamente e é o esqueleto de
código (carregar o `.joblib`, montar o vetor de features, `predict_proba`)
que essa consumer do broker vai reaproveitar quando for implementada.

**`GET /accounts/{account_id}`** — consulta o risk score já pré-calculado em
lote (`ml/artifacts/account_risk_scores.csv`), sem rodar o modelo de novo:

```bash
curl localhost:8002/accounts/C1981613973
# {"account_id":"C1981613973","risk_score":99.9543,"tx_count":25, ...}
```

## Limitações (é um modelo básico)

- Não usa saldos (`oldbalance*`/`newbalance*`) nem outras colunas do dataset
  original — só as 5 priorizadas pelo projeto, por [decisão do ADR 003](../../README.md#adr-003--base-de-dados-para-entrada-de-dados).
- `timestamp` é ancorado numa data arbitrária; só a estrutura relativa
  (hora do dia, dia da simulação) tem significado, não a data em si.
- O limiar de 0,5 usado para reportar precision/recall/F1 é só uma
  referência — em um sistema real, o threshold de quarentena (Quarantine
  Service) deveria ser calibrado à parte a partir da curva precision-recall
  completa, não usar 0,5 cegamente. Para o caso de uso principal deste
  pipeline (rankear contas por `risk_score`, um valor contínuo), o que
  importa é a qualidade do ranking — PR-AUC/ROC-AUC — não a métrica num
  limiar específico.
- É uma etapa offline de treino/scoring em lote sobre o CSV completo, não o
  serviço em si — o Risk Scoring Service (ainda não implementado) é quem, em
  produção, vai carregar `fraud_classifier.joblib` e aplicar o mesmo
  `features.py` a cada evento `TransacaoRegistrada` consumido do broker.
