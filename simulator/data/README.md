# Base de dados PaySim

Coloque nesta pasta o CSV baixado do Kaggle com o nome original:

```text
simulator/data/PS_20174392719_1491204439457_log.csv
```

Estrutura esperada:

```text
simulator/
├── data/
│   ├── README.md
│   └── PS_20174392719_1491204439457_log.csv  # não versionado
├── app/
├── main.py
└── Dockerfile
```

O arquivo não deve ser enviado ao GitHub: ele tem centenas de MB e sua licença e
distribuição continuam sendo geridas pela página da PaySim no Kaggle. Cada
integrante baixa sua própria cópia e a coloca no mesmo caminho relativo. Assim,
o código não depende do diretório pessoal nem do sistema operacional de quem o
executa.

Na primeira execução, o simulador cria um índice binário com a posição de cada
linha. Em execução local, o padrão é `data/.cache/paysim.offsets`; no Docker
Compose recomenda-se `/cache/paysim.offsets`, armazenado em volume nomeado.
