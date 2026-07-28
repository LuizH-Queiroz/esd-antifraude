"""Acesso aleatório e eficiente ao CSV da PaySim.

A base completa possui milhões de registros. Carregá-la inteira com pandas
consumiria memória desnecessariamente, e percorrê-la do começo ao fim antes de
cada envio seria muito lento.

A estratégia adotada é:

1. Na primeira execução, fazer uma única leitura para registrar, em um arquivo
   binário, a posição inicial de cada linha de dados.
2. Em cada sorteio, escolher uniformemente um número de linha.
3. Ler no índice apenas os 8 bytes daquela posição e saltar diretamente para a
   linha correspondente no CSV.

Depois que o índice existe, cada seleção é O(1), usa pouca memória e não segue a
ordem do arquivo. O sorteio é feito com reposição, portanto uma mesma transação
pode ser escolhida novamente em outro envio.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

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
    def from_json(cls, value: dict[str, object]) -> "DatasetIndexMetadata":
        return cls(
            version=int(value["version"]),
            dataset_size=int(value["dataset_size"]),
            dataset_mtime_ns=int(value["dataset_mtime_ns"]),
            row_count=int(value["row_count"]),
            columns=tuple(str(column) for column in value["columns"]),
        )


class RandomPaySimSampler:
    """Seleciona linhas aleatórias da PaySim com distribuição uniforme."""

    def __init__(
        self,
        dataset_path: Path,
        index_path: Path,
        *,
        random_seed: int | None = None,
    ) -> None:
        self.dataset_path = dataset_path.resolve()
        self.index_path = index_path.resolve()
        self.metadata_path = self.index_path.with_suffix(self.index_path.suffix + ".json")
        self._random = random.Random(random_seed)
        self._metadata: DatasetIndexMetadata | None = None

    @property
    def row_count(self) -> int:
        if self._metadata is None:
            raise DatasetError("O sampler ainda não foi preparado.")
        return self._metadata.row_count

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

    def sample(self) -> PaySimTransaction:
        """Sorteia e devolve exatamente uma transação aleatória."""
        if self._metadata is None:
            self.prepare()

        assert self._metadata is not None
        random_row_number = self._random.randrange(self._metadata.row_count)
        byte_offset = self._read_offset(random_row_number)
        values = self._read_csv_values(byte_offset)

        row = dict(zip(self._metadata.columns, values, strict=True))
        return PaySimTransaction.from_csv_row(row)

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

    def _read_offset(self, row_number: int) -> int:
        with self.index_path.open("rb") as index_file:
            index_file.seek(row_number * INDEX_ENTRY.size)
            packed_offset = index_file.read(INDEX_ENTRY.size)

        if len(packed_offset) != INDEX_ENTRY.size:
            raise DatasetError("O índice terminou antes da posição sorteada.")
        return INDEX_ENTRY.unpack(packed_offset)[0]

    def _read_csv_values(self, byte_offset: int) -> Sequence[str]:
        with self.dataset_path.open("rb") as dataset_file:
            dataset_file.seek(byte_offset)
            line_bytes = dataset_file.readline()

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
