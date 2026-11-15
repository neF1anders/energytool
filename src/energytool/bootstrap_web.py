"""Composition root веб-части: Container → SQLite, HandleTurn, SubmitLead, заявки.

Отдельно от bootstrap.py, потому что bootstrap достижим из solution.py, а ZIP сабмита не
должен импортировать SQLAlchemy/FastAPI (их нет в окружении платформы).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from energytool.application.build_index import KnowledgeIndex
from energytool.application.handle_turn import HandleTurn
from energytool.application.submit_lead import SubmitLead
from energytool.bootstrap import Container
from energytool.domain.contract import ContractRegistry
from energytool.domain.knowledge import BudgetSnippet
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scoring import ScoringParams
from energytool.infrastructure.brief_render import TextBriefRenderer
from energytool.infrastructure.storage.database import create_database
from energytool.infrastructure.storage.lead_files import CompositeLeadSink, TextFileLeadSink
from energytool.infrastructure.storage.leads import SqliteLeadSink
from energytool.infrastructure.storage.sqlite_sessions import SqliteSessionRepo, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebServices:
    container: Container
    index: KnowledgeIndex
    sessions: SqliteSessionRepo
    handle_turn: HandleTurn
    submit_lead: SubmitLead
    leads: SqliteLeadSink


def build_web(container: Container, index: KnowledgeIndex | None = None) -> WebServices:
    settings = container.settings
    index = index or container.build_index()
    engine = create_database(settings.sqlite_path)
    sessions = SqliteSessionRepo(engine)
    leads = SqliteLeadSink(engine, webhook_url=settings.crm_webhook_url)
    registry = ContractRegistry()
    submit_lead = SubmitLead(
        registry=registry,
        rules=RULES,
        priors=PRIORS,
        params=ScoringParams(),
        renderer=TextBriefRenderer(registry),
        sink=CompositeLeadSink(leads, [TextFileLeadSink(settings.leads_dir)]),
        sessions=sessions,
        telemetry=container.telemetry,
        now=utc_now,
    )
    retrieve = container.retriever(index)
    budget = BudgetSnippet(top_k=settings.top_k, fallback_k=1)
    handle_turn = HandleTurn(
        sessions=sessions,
        extractor=container.extractor(),
        qualifier=container.qualifier(retrieve, budget),
        writer=container.reply_writer(),
        telemetry=container.telemetry,
        registry=registry,
        submit_lead=submit_lead.from_step,
    )
    logger.info(
        "web services ready",
        extra={
            "data": {
                "sqlite": str(settings.sqlite_path),
                "crm": bool(settings.crm_webhook_url),
                "leads_dir": str(settings.leads_dir),
            }
        },
    )
    return WebServices(container, index, sessions, handle_turn, submit_lead, leads)
