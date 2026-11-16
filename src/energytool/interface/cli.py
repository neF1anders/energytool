"""CLI для разработки: `uv run --env-file .env python -m energytool <команда>`."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence

from energytool.bootstrap import Container, build
from energytool.infrastructure.config import ConfigError
from energytool.infrastructure.llm.gateway import LlmGatewayError
from energytool.infrastructure.logs import log_context, log_duration

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="energytool")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ping", help="проверить модели на текущем профиле")
    args = parser.parse_args(argv)

    try:
        container = build()
    except ConfigError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    try:
        with log_context(command=args.command):
            return ping(container)
    finally:
        container.close()


def ping(container: Container) -> int:
    """Chat обязателен (код 1 при ошибке); embeddings и rerank — информативно:
    на OpenRouter их может не быть (spec §10, §16)."""
    try:
        container.settings.require_llm()
    except ConfigError as exc:
        print(f"ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    gateway = container.gateway
    gateway.calibrate_thinking()
    checks: list[tuple[str, bool, Callable[[], str]]] = [
        ("chat", True, lambda: _ping_chat(container)),
        ("embed", False, lambda: f"dim={len(gateway.embed(['проверка связи'])[0])}"),
        ("rerank", False, lambda: f"top={gateway.rerank('счётчик', ['счётчик', 'кровля'], 2)}"),
    ]
    failed_required = False
    for name, required, check in checks:
        try:
            with log_duration(logger, f"ping {name}", level=logging.INFO) as extra:
                extra["result"] = detail = check()
            print(f"[ok]   {name}: {detail}")
        except LlmGatewayError as exc:
            print(f"[fail] {name}: {exc}")
            failed_required = failed_required or required
    return 1 if failed_required else 0


def _ping_chat(container: Container) -> str:
    result = container.gateway.chat(
        [{"role": "user", "content": "Ответь одним словом: работает?"}], max_tokens=256
    )
    return (
        f"model={result.model} text={result.text!r} finish={result.finish_reason} "
        f"tokens={result.tokens_in}/{result.tokens_out} ms={result.ms}"
    )
