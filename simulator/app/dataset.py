"""Acesso eficiente ao CSV da PaySim, com dois modos de leitura.

A base completa possui milhões de registros. Carregá-la inteira com pandas
consumiria memória desnecessariamente, e percorrê-la do começo ao fim antes de
cada envio seria muito lento. Por isso, na primeira execução, o simulador faz
uma única passagem pelo CSV para registrar, em um arquivo binário (o
"índice"), a posição (offset em bytes) de cada linha de dados. A partir daí,
qualquer linha pode ser lida diretamente, sem reler o arquivo inteiro.

Esse índice é compartilhado pelos dois modos de leitura oferecidos aqui:

* ``SequentialPaySimReader`` (padrão) — percorre o CSV **na ordem original**
  (que já é cronológica, por ``step``), retomando de onde parou entre uma
  execução e outra via um checkpoint persistido em disco. Esse é o modo
  correto para o objetivo do sistema: o Risk Scoring Service depende de
  observar múltiplas transações da mesma conta ao longo do tempo (o fator
  "correlação entre contas" descrito na ADR 003 do README principal), e esse
  padrão só existe se as linhas forem entregues na ordem em que aconteceram.
  A ADR 004 também descreve o simulador como um "replay sequencial e espaçado
  no tempo" — este é o modo que implementa essa descrição.

* ``RandomPaySimSampler`` (opcional) — sorteia uma linha aleatória a cada
  chamada, com reposição (a mesma linha pode se repetir). Era o comportamento
  padrão anterior deste arquivo. Ele embaralha a ordem/tempo original das
  transações, o que quebra os padrões de correlação entre contas que o motor
  de risco depende para funcionar — por isso deixou de ser o padrão, mas
  segue disponível (via ``SAMPLING_STRATEGY=random`` — ver app/config.py)
  para cenários específicos de teste de carga/stress, onde a ordem não
  importa e o que se quer é apenas volume de eventos variados.

Ambos os modos são O(1) por linha lida (depois que o índice existe) e usam
memória constante, independentemente do tamanho do dataset.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.models import PaySimTransaction

LOGGER = logging.getLogger(__name__)

INDEX_ENTRY = struct.Struct("<Q")  # inteiro unsigned de 64 bits, little-endian
INDEX_VERSION = 1
EXPECTED_COLUMNS = (
    "step",
    "type",
    "amount",
    "nameOrig",
    "oldbalanceOrg",
    "newbalanceOrig",
    "nameDest",
    "oldbalanceDest",
    "newbalanceDest",
    "isFraud",
    "isFlaggedFraud",
)


class DatasetError(RuntimeError):
    """Erro de configuração, leitura ou validação da base PaySim."""


@dataclass(frozen=True, slots=True)
class DatasetIndexMetadata:
    version: int
    dataset_size: int
    dataset_mtime_ns: int
    row_count: int
    columns: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "dataset_size": self.dataset_size,
            "dataset_mtime_ns": self.dataset_mtime_ns,
            "row_count": self.row_count,
            "columns": list(self.columns),
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> DatasetIndexMetadata:
        return cls(
            version=int(value["version"]),
            dataset_size=int(value["dataset_size"]),
            dataset_mtime_ns=int(value["dataset_mtime_ns"]),
            row_count=int(value["row_count"]),
            columns=tuple(str(column) for column in value["columns"]),
        )


class PaySimIndex:
    """Constrói e consulta o índice de offsets de bytes do CSV da PaySim.

    Esta classe concentra toda a lógica de indexação que antes vivia dentro
    de ``RandomPaySimSampler``. Extraí-la permite que os dois modos de
    leitura (sequencial e aleatório) compartilhem exatamente o mesmo índice,
    sem duplicar código nem risco de os dois modos ficarem
    inconsistentes entre si.
    """

    def __init__(self, dataset_path: Path, index_path: Path) -> None:
        self.dataset_path = dataset_path.resolve()
        self.index_path = index_path.resolve()
        self.metadata_path = self.index_path.with_suffix(self.index_path.suffix + ".json")
        self._metadata: DatasetIndexMetadata | None = None

    @property
    def is_ready(self) -> bool:
        return self._metadata is not None

    @property
    def row_count(self) -> int:
        if self._metadata is None:
            raise DatasetError("O índice ainda não foi preparado (chame prepare() antes).")
        return self._metadata.row_count

    @property
    def columns(self) -> tuple[str, ...]:
        if self._metadata is None:
            raise DatasetError("O índice ainda não foi preparado (chame prepare() antes).")
        return self._metadata.columns

    def prepare(self, *, rebuild_index: bool = False) -> None:
        """Valida a base e cria/reutiliza o índice de posições."""
        self._ensure_dataset_exists()

        if not rebuild_index:
            metadata = self._load_valid_metadata()
            if metadata is not None:
                self._metadata = metadata
                LOGGER.info(
                    "Índice PaySim reutilizado: %s linhas disponíveis.",
                    metadata.row_count,
                )
                return

        self._metadata = self._build_index()

    def get_offset(self, row_number: int) -> int:
        """Retorna o offset (em bytes) do início da linha `row_number`."""
        with self.index_path.open("rb") as index_file:
            index_file.seek(row_number * INDEX_ENTRY.size)
            packed_offset = index_file.read(INDEX_ENTRY.size)

        if len(packed_offset) != INDEX_ENTRY.size:
            raise DatasetError(f"O índice terminou antes da linha {row_number}.")
        return INDEX_ENTRY.unpack(packed_offset)[0]

    def read_row_values(self, byte_offset: int) -> Sequence[str]:
        """Lê e faz o parse CSV de uma única linha, a partir de um offset conhecido."""
        with self.dataset_path.open("rb") as dataset_file:
            dataset_file.seek(byte_offset)
            line_bytes = dataset_file.readline()

        return self.parse_csv_line(line_bytes, byte_offset)

    def parse_csv_line(self, line_bytes: bytes, byte_offset: int) -> Sequence[str]:
        """Faz o parse CSV de uma linha crua já lida do arquivo.

        Exposto como método público (não apenas usado internamente) porque
        ``SequentialPaySimReader`` também precisa fazer esse mesmo parse ao
        ler a próxima linha de um arquivo já posicionado, sem reabrir/buscar
        o offset novamente.
        """
        try:
            line_text = line_bytes.decode("utf-8")
            values = next(csv.reader([line_text]))
        except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
            raise DatasetError(
                f"Não foi possível interpretar a linha no offset {byte_offset}."
            ) from exc

        if len(values) != len(EXPECTED_COLUMNS):
            raise DatasetError(
                f"Linha no offset {byte_offset} possui {len(values)} campos; "
                f"esperados: {len(EXPECTED_COLUMNS)}."
            )
        return values

    def _ensure_dataset_exists(self) -> None:
        if not self.dataset_path.is_file():
            raise DatasetError(
                "Arquivo PaySim não encontrado em "
                f"{self.dataset_path}. Consulte simulator/data/README.md."
            )
        if self.dataset_path.stat().st_size == 0:
            raise DatasetError(f"O arquivo {self.dataset_path} está vazio.")

    def _load_valid_metadata(self) -> DatasetIndexMetadata | None:
        if not self.index_path.is_file() or not self.metadata_path.is_file():
            return None

        try:
            metadata = DatasetIndexMetadata.from_json(
                json.loads(self.metadata_path.read_text(encoding="utf-8"))
            )
            stat = self.dataset_path.stat()
            expected_index_size = metadata.row_count * INDEX_ENTRY.size

            is_valid = all(
                (
                    metadata.version == INDEX_VERSION,
                    metadata.dataset_size == stat.st_size,
                    metadata.dataset_mtime_ns == stat.st_mtime_ns,
                    metadata.columns == EXPECTED_COLUMNS,
                    metadata.row_count > 0,
                    self.index_path.stat().st_size == expected_index_size,
                )
            )
            return metadata if is_valid else None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            LOGGER.warning("Índice existente inválido; ele será reconstruído.")
            return None

    def _build_index(self) -> DatasetIndexMetadata:
        """Cria o índice de offsets de maneira atômica e com memória constante."""
        LOGGER.info(
            "Criando índice da PaySim na primeira execução. Esta etapa lê o CSV "
            "uma única vez, mas não carrega a base inteira na memória."
        )
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        temporary_index = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary_metadata = self.metadata_path.with_suffix(
            self.metadata_path.suffix + ".tmp"
        )

        row_count = 0
        try:
            with self.dataset_path.open("rb") as dataset_file, temporary_index.open(
                "wb"
            ) as index_file:
                columns = self._read_and_validate_header(dataset_file)

                while True:
                    line_offset = dataset_file.tell()
                    line = dataset_file.readline()
                    if not line:
                        break
                    if not line.strip():
                        continue

                    index_file.write(INDEX_ENTRY.pack(line_offset))
                    row_count += 1

                index_file.flush()
                os.fsync(index_file.fileno())

            if row_count == 0:
                raise DatasetError("O CSV possui cabeçalho, mas nenhuma linha de dados.")

            stat = self.dataset_path.stat()
            metadata = DatasetIndexMetadata(
                version=INDEX_VERSION,
                dataset_size=stat.st_size,
                dataset_mtime_ns=stat.st_mtime_ns,
                row_count=row_count,
                columns=columns,
            )
            temporary_metadata.write_text(
                json.dumps(metadata.to_json(), indent=2), encoding="utf-8"
            )

            # replace() evita deixar um índice parcial caso o processo seja encerrado.
            temporary_index.replace(self.index_path)
            temporary_metadata.replace(self.metadata_path)

            LOGGER.info("Índice PaySim criado: %s linhas indexadas.", row_count)
            return metadata
        finally:
            temporary_index.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)

    def _read_and_validate_header(self, dataset_file: BinaryIO) -> tuple[str, ...]:
        header_bytes = dataset_file.readline()
        if not header_bytes:
            raise DatasetError("O CSV não possui cabeçalho.")

        try:
            header_text = header_bytes.decode("utf-8-sig")
            columns = tuple(next(csv.reader([header_text])))
        except (UnicodeDecodeError, csv.Error, StopIteration) as exc:
            raise DatasetError("Não foi possível interpretar o cabeçalho do CSV.") from exc

        if columns != EXPECTED_COLUMNS:
            raise DatasetError(
                "Cabeçalho PaySim inesperado.\n"
                f"Esperado: {EXPECTED_COLUMNS}\n"
                f"Recebido: {columns}"
            )
        return columns


class SequentialPaySimReader:
    """Percorre o CSV da PaySim em ordem, retomando de onde parou entre execuções.

    Este é o modo padrão do simulador (ver SAMPLING_STRATEGY em app/config.py).
    Como o PaySim já vem ordenado por `step` (tempo simulado), ler
    sequencialmente preserva os padrões temporais e de correlação entre
    contas (`nameOrig`/`nameDest`) que o Risk Scoring Service vai precisar
    observar — ao contrário da amostragem aleatória, que embaralha essa ordem.

    A posição atual (próxima linha a ler) é salva periodicamente em um
    arquivo de checkpoint. Isso permite parar e reiniciar o simulador (ex.:
    `docker compose restart simulator`) sem repetir desde o início nem perder
    o lugar — importante numa simulação "de tempo real" contínua.
    """

    def __init__(
        self,
        dataset_path: Path,
        index_path: Path,
        checkpoint_path: Path,
        *,
        checkpoint_every_messages: int = 20,
    ) -> None:
        self.index = PaySimIndex(dataset_path, index_path)
        self.checkpoint_path = checkpoint_path.resolve()
        self.checkpoint_every_messages = checkpoint_every_messages

        self._current_row = 0
        self._file_handle: BinaryIO | None = None
        self._pending_since_checkpoint = 0

    @property
    def row_count(self) -> int:
        return self.index.row_count

    def prepare(self, *, rebuild_index: bool = False, reset_checkpoint: bool = False) -> None:
        """Prepara o índice e posiciona a leitura no ponto correto de retomada."""
        self.index.prepare(rebuild_index=rebuild_index)

        self._current_row = 0 if reset_checkpoint else self._load_checkpoint()
        self._reopen_at(self._current_row)

        LOGGER.info(
            "Leitura sequencial da PaySim: retomando na linha %s de %s (%.2f%% percorrido).",
            self._current_row,
            self.index.row_count,
            100 * self._current_row / self.index.row_count,
        )

    def sample(self) -> PaySimTransaction:
        """Lê e devolve a próxima transação, na ordem original do CSV.

        O nome `sample()` é mantido igual ao de `RandomPaySimSampler` de
        propósito: o restante do simulador (runner.py) não precisa saber
        qual estratégia de leitura está em uso por trás dessa chamada.
        """
        if not self.index.is_ready:
            self.prepare()

        if self._current_row >= self.index.row_count:
            LOGGER.warning(
                "Fim do dataset PaySim atingido (linha %s); reiniciando a leitura "
                "sequencial a partir do início (loop). Isso só deve ocorrer em "
                "execuções muito longas ou de teste.",
                self.index.row_count,
            )
            self._current_row = 0
            self._reopen_at(0)

        assert self._file_handle is not None
        raw_line = self._file_handle.readline()
        if not raw_line:
            # Proteção extra contra um EOF inesperado antes do fim contado pelo
            # índice (ex.: arquivo truncado externamente durante a execução).
            LOGGER.warning("EOF inesperado antes do fim do índice; reiniciando do início.")
            self._current_row = 0
            self._reopen_at(0)
            raw_line = self._file_handle.readline()

        values = self.index.parse_csv_line(raw_line, self._current_row)
        row = dict(zip(self.index.columns, values, strict=True))
        transaction = PaySimTransaction.from_csv_row(row)

        self._current_row += 1
        self._pending_since_checkpoint += 1
        if self._pending_since_checkpoint >= self.checkpoint_every_messages:
            self._save_checkpoint()

        return transaction

    def flush_checkpoint(self) -> None:
        """Persiste a posição atual imediatamente (chamado ao encerrar o simulador)."""
        self._save_checkpoint()

    def close(self) -> None:
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None

    def _reopen_at(self, row_number: int) -> None:
        offset = self.index.get_offset(row_number)
        self.close()
        self._file_handle = self.index.dataset_path.open("rb")
        self._file_handle.seek(offset)

    def _load_checkpoint(self) -> int:
        if not self.checkpoint_path.is_file():
            return 0
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            row_number = int(data.get("next_row", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            LOGGER.warning("Checkpoint sequencial inválido ou corrompido; reiniciando do início.")
            return 0

        if not (0 <= row_number < self.index.row_count):
            LOGGER.warning(
                "Checkpoint aponta para uma linha fora do dataset atual; reiniciando do início."
            )
            return 0
        return row_number

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.checkpoint_path.with_suffix(
            self.checkpoint_path.suffix + ".tmp"
        )
        temporary_path.write_text(
            json.dumps({"next_row": self._current_row}), encoding="utf-8"
        )
        temporary_path.replace(self.checkpoint_path)  # escrita atômica
        self._pending_since_checkpoint = 0


class RandomPaySimSampler:
    """Seleciona linhas aleatórias da PaySim com distribuição uniforme e reposição.

    Modo opcional (SAMPLING_STRATEGY=random) — ver o aviso no topo deste
    arquivo sobre por que este deixou de ser o padrão.
    """

    def __init__(
        self,
        dataset_path: Path,
        index_path: Path,
        *,
        random_seed: int | None = None,
    ) -> None:
        self.index = PaySimIndex(dataset_path, index_path)
        self._random = random.Random(random_seed)

    @property
    def row_count(self) -> int:
        return self.index.row_count

    def prepare(self, *, rebuild_index: bool = False, reset_checkpoint: bool = False) -> None:
        # `reset_checkpoint` não se aplica a este modo (não há posição a retomar);
        # o parâmetro existe só para manter a mesma assinatura de chamada que
        # SequentialPaySimReader, permitindo que main.py trate os dois modos de
        # forma intercambiável.
        del reset_checkpoint
        self.index.prepare(rebuild_index=rebuild_index)

    def sample(self) -> PaySimTransaction:
        """Sorteia e devolve exatamente uma transação aleatória."""
        if not self.index.is_ready:
            self.prepare()

        random_row_number = self._random.randrange(self.index.row_count)
        byte_offset = self.index.get_offset(random_row_number)
        values = self.index.read_row_values(byte_offset)

        row = dict(zip(self.index.columns, values, strict=True))
        return PaySimTransaction.from_csv_row(row)