"""Ponto de entrada do simulador PaySim."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from app.client import ApiGatewayClient
from app.config import Settings
from app.dataset import DatasetError, RandomPaySimSampler
from app.mapper import TransactionMessageMapper
from app.runner import RunOptions, SimulatorRunner

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seleciona transações aleatórias da PaySim e as envia ao API Gateway."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Seleciona e processa somente uma transação.",
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
        help="Sorteia e imprime mensagens sem chamar o API Gateway.",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Força a reconstrução do índice aleatório do CSV.",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
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

        sampler = RandomPaySimSampler(
            settings.dataset_path,
            settings.dataset_index_path,
            random_seed=settings.random_seed,
        )
        sampler.prepare(rebuild_index=args.rebuild_index)

        mapper = TransactionMessageMapper(
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
            sampler,
            mapper,
            client,
            RunOptions(
                dry_run=args.dry_run,
                max_messages=max_messages,
                send_interval_seconds=settings.send_interval_seconds,
                stop_on_error=settings.stop_on_error,
            ),
        )

        def stop_gracefully(signum: int, _frame: object) -> None:
            LOGGER.info("Sinal %s recebido; encerrando o simulador.", signum)
            runner.request_stop()

        signal.signal(signal.SIGINT, stop_gracefully)
        signal.signal(signal.SIGTERM, stop_gracefully)

        LOGGER.info(
            "Simulador iniciado: dataset=%s, linhas=%s, destino=%s, dry_run=%s.",
            settings.dataset_path,
            sampler.row_count,
            settings.gateway_events_url,
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
