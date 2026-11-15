"""Composition root: Settings → адаптеры → use cases. Единственное место сборки.

Точки входа (CLI, batch, HTTP, eval) получают готовые объекты отсюда и не создают
адаптеры сами. Профиль меняет только то, какие адаптеры собираются здесь.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from energytool.application.build_index import BuildKnowledgeIndex, KnowledgeIndex
from energytool.application.qualification import Qualifier, Retriever
from energytool.application.retrieval_stages import get_config
from energytool.application.retrieve_knowledge import RetrieveKnowledge
from energytool.domain.contract import ContractRegistry
from energytool.domain.knowledge import BudgetSnippet
from energytool.domain.policy import Thresholds
from energytool.domain.reply import StyleExample
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scoring import ScoringParams
from energytool.infrastructure.config import Settings
from energytool.infrastructure.index.factory import InMemoryIndexFactory
from energytool.infrastructure.kb.loader import FileKnowledgeSource, find_knowledge_base
from energytool.infrastructure.kb.splitter import MarkdownSectionSplitter
from energytool.infrastructure.kb.tagmap import KbSectionTagger
from energytool.infrastructure.llm.condenser import LlmQuestionCondenser
from energytool.infrastructure.llm.embedder import GatewayEmbedder
from energytool.infrastructure.llm.extractor import LlmFactExtractor
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.infrastructure.llm.reranker import GatewayReranker
from energytool.infrastructure.llm.responder import LlmReplyWriter
from energytool.infrastructure.llm.style_examples import load_style_examples
from energytool.infrastructure.logs import configure_logging
from energytool.infrastructure.telemetry import LogTelemetry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    gateway: LlmGateway
    telemetry: LogTelemetry
    embedder: GatewayEmbedder
    reranker: GatewayReranker

    def close(self) -> None:
        self.gateway.close()

    def data_roots(self) -> list[Path]:
        """Где искать входные файлы: DATA_DIR, он же от каталога точки входа, cwd."""
        data_dir = self.settings.data_dir
        roots = [data_dir, Path.cwd() / data_dir, Path.cwd()]
        return list(dict.fromkeys(r.resolve() for r in roots))

    def build_index(self, kb_path: Path | None = None) -> KnowledgeIndex:
        path = kb_path or find_knowledge_base(self.data_roots())
        return BuildKnowledgeIndex(
            source=FileKnowledgeSource(path),
            splitter=MarkdownSectionSplitter(),
            tagger=KbSectionTagger(),
            embedder=self.embedder,
            factory=InMemoryIndexFactory(),
        )()

    def retriever(self, index: KnowledgeIndex, config: str | None = None) -> RetrieveKnowledge:
        return RetrieveKnowledge(
            config=get_config(config or self.settings.retrieval_config),
            index=index,
            embedder=self.embedder,
            reranker=self.reranker,
        )

    def qualifier(self, retrieve: Retriever, budget: BudgetSnippet) -> Qualifier:
        """Доменные правила и пороги — из domain (spec §5.5), не из окружения."""
        return Qualifier(
            registry=ContractRegistry(),
            rules=RULES,
            priors=PRIORS,
            params=ScoringParams(),
            thresholds=Thresholds(),
            retrieve=retrieve,
            budget=budget,
        )

    def extractor(self) -> LlmFactExtractor:
        return LlmFactExtractor(self.gateway)

    def condenser(self) -> LlmQuestionCondenser:
        return LlmQuestionCondenser(self.gateway)

    def style_examples(self) -> tuple[StyleExample, ...]:
        return load_style_examples(self.settings.style_examples_dir)

    def reply_writer(self) -> LlmReplyWriter:
        return LlmReplyWriter(
            self.gateway,
            rules_first=self.settings.answer_layout == "rules_first",
            best_last=self.settings.answer_evidence_order == "best_last",
            style_examples=self.style_examples()
            if self.settings.style_examples_mode == "block"
            else (),
            style_holdout=self.settings.style_examples_holdout,
        )


def build(env: Mapping[str, str] | None = None) -> Container:
    """Сборка для точек входа: окружение → логи → адаптеры."""
    settings = Settings.from_env(os.environ if env is None else env)
    configure_logging(settings.log_level, settings.log_format)
    logger.info("settings loaded", extra={"data": settings.safe_summary()})
    return assemble(settings, LlmGateway(settings))


def assemble(settings: Settings, gateway: LlmGateway) -> Container:
    """Адаптеры поверх готового gateway (тесты подставляют gateway с фейковым прокси)."""
    return Container(
        settings=settings,
        gateway=gateway,
        telemetry=LogTelemetry(),
        embedder=GatewayEmbedder(
            gateway, settings.embedder.model, cache_dir=settings.embedding_cache_dir
        ),
        reranker=GatewayReranker(gateway),
    )
