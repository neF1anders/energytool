"""QuestionCondenser: история диалога → самостоятельный вопрос для поиска.

Последняя реплика диалога часто не содержит темы («С чего нам начать?», «Таких данных
хватит?»): поиск и реранкер по ней находят общие разделы. Один короткий вызов LLM
собирает вопрос с объектом и задачей клиента из истории. Отказ модели — пустая строка,
вызывающий код ищет по репликам как раньше.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from energytool.domain.state import Message, Role
from energytool.infrastructure.llm.gateway import LlmGateway, LlmGatewayError
from energytool.infrastructure.llm.prompts import CONDENSER_SYSTEM, CONDENSER_USER

logger = logging.getLogger(__name__)

CONDENSER_MAX_TOKENS = 120
MAX_QUERY_CHARS = 400


class LlmQuestionCondenser:
    def __init__(self, gateway: LlmGateway) -> None:
        self._gateway = gateway

    def condense(self, history: Sequence[Message]) -> str:
        names = {Role.USER: "Клиент", Role.ASSISTANT: "Консультант"}
        dialog = "\n".join(f"{names[m.role]}: {m.text}" for m in history)
        try:
            result = self._gateway.chat(
                [
                    {"role": "system", "content": CONDENSER_SYSTEM},
                    {"role": "user", "content": CONDENSER_USER.format(dialog=dialog)},
                ],
                temperature=0.0,
                max_tokens=CONDENSER_MAX_TOKENS,
            )
        except LlmGatewayError as exc:
            logger.warning("condenser failed, searching by raw messages: %s", exc)
            return ""
        query = " ".join(result.text.split()).strip('"«»')
        logger.info("question condensed", extra={"data": {"query": query[:MAX_QUERY_CHARS]}})
        return query[:MAX_QUERY_CHARS]
