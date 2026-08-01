"""Ponto de entrada do simulador PaySim."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from app.client import ApiGatewayClient
from app.config import Settings
from app.dataset import DatasetError, RandomPaySimSampler, SequentialPaySimReader
from app.mapper import TransactionMessageMapper
from app.runner import RunOptions, SimulatorRunner, TransactionSource

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lê transações da PaySim (em ordem, por padrão) e as envia ao API Gateway."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Processa somente uma transação.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Sobrescreve MAX_MESSAGES para esta execução; 0 significa contínuo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lê e imprime mensagens sem chamar o API Gateway.",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Força a reconstrução do índice de offsets do CSV.",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help=(
            "Reinicia a leitura sequencial a partir da primeira linha, ignorando "
            "o checkpoint salvo de execuções anteriores. Sem efeito no modo "
            "SAMPLING_STRATEGY=random."
        ),
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _build_source(settings: Settings) -> TransactionSource:
    """Constrói a fonte de dados de acordo com SAMPLING_STRATEGY.

    Manter essa escolha isolada em uma única função é o que permite ao
    restante do simulador (runner.py) trabalhar apenas com a interface
    TransactionSource, sem saber qual estratégia está ativa.
    """
    if settings.sampling_strategy == "random":
        LOGGER.warning(
            "SAMPLING_STRATEGY=random: a ordem/tempo original do PaySim não é "
            "preservada. Isso quebra os padrões de correlação entre contas que "
            "o Risk Scoring Service vai precisar (ver ADR 003/004 no README "
            "principal e a discussão na Issue #5). Use apenas para testes de "
            "carga/stress; para simular o fluxo real, prefira 'sequential'."
        )
        return RandomPaySimSampler(
            settings.dataset_path,
            settings.dataset_index_path,
            random_seed=settings.random_seed,
        )

    return SequentialPaySimReader(
        settings.dataset_path,
        settings.dataset_index_path,
        settings.sequential_checkpoint_path,
        checkpoint_every_messages=settings.checkpoint_every_messages,
    )


def main() -> int:
    args = parse_args()

    try:
        settings = Settings.from_environment()
        configure_logging(settings.log_level)

        max_messages = settings.max_messages if args.count is None else args.count
        if args.once:
            max_messages = 1
        if max_messages < 0:
            raise ValueError("--count não pode ser negativo.")

        source = _build_source(settings)
        source.prepare(rebuild_index=args.rebuild_index, reset_checkpoint=args.reset_checkpoint)

        mapper = TransactionMessageMapper(
            selection_strategy=settings.sampling_strategy,
            include_balance_fields=settings.include_balance_fields,
            include_ground_truth=settings.include_ground_truth,
        )
        client = ApiGatewayClient(
            settings.gateway_events_url,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
            retry_backoff_seconds=settings.retry_backoff_seconds,
        )
        runner = SimulatorRunner(
            source,
            mapper,
            client,
            RunOptions(
                dry_run=args.dry_run,
                max_messages=max_messages,
                send_interval_min_seconds=settings.send_interval_min_seconds,
                send_interval_max_seconds=settings.send_interval_max_seconds,
                stop_on_error=settings.stop_on_error,
            ),
        )

        def stop_gracefully(signum: int, _frame: object) -> None:
            LOGGER.info("Sinal %s recebido; encerrando o simulador.", signum)
            runner.request_stop()

        signal.signal(signal.SIGINT, stop_gracefully)
        signal.signal(signal.SIGTERM, stop_gracefully)

        LOGGER.info(
            "Simulador iniciado: dataset=%s, linhas=%s, estratégia=%s, destino=%s, "
            "intervalo=%.2fs-%.2fs, dry_run=%s.",
            settings.dataset_path,
            source.row_count,
            settings.sampling_strategy,
            settings.gateway_events_url,
            settings.send_interval_min_seconds,
            settings.send_interval_max_seconds,
            args.dry_run,
        )
        runner.run()
        return 0
    except (DatasetError, ValueError) as exc:
        LOGGER.error("Configuração inválida: %s", exc)
        return 2
    except Exception:
        LOGGER.exception("O simulador foi encerrado por um erro inesperado.")
        return 1


if __name__ == "__main__":
    sys.exit(main())