"""Domain: сущности и чистые правила квалификации. Без IO, без LLM, без зависимостей.

Доменные сервисы (чистые функции):
- reducer.apply(state, extraction, registry) -> ConversationState
- scorer.score(state, rules, params) -> ScoreBoard
- dialog_policy.decide(state, extraction, board, registry, thresholds, mode) -> PolicyDecision
- brief.build(state, board, registry, contact) -> Brief
"""

from energytool.domain.contract import ALL_SLOTS, ContractRegistry
from energytool.domain.extraction import Extraction, Intent, SlotUpdate
from energytool.domain.knowledge import (
    BoostSnippet,
    BudgetSnippet,
    Chunk,
    Evidence,
    IdSnippet,
    KnowledgeQuery,
    KnowledgeResult,
    PinSnippet,
    Scope,
    ScopeSnippet,
    SectionKind,
    Snippet,
    TextSnippet,
    Topic,
)
from energytool.domain.lead import Brief, Contact, Lead
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.products import ALL_PRODUCTS, Goal, Product, Segment
from energytool.domain.reply import AskPlan, Reply, ReplyPlan
from energytool.domain.scoring import (
    EXCLUDE,
    ProductScore,
    Rule,
    RuleHit,
    ScoreBoard,
    ScoringParams,
)
from energytool.domain.slots import DocumentStatus, SlotSpec, SlotStatus, SlotType, SlotValue
from energytool.domain.state import ConversationState, DialogState, Message, Role, SoftSignal

__all__ = [
    "ALL_PRODUCTS",
    "ALL_SLOTS",
    "AskPlan",
    "BoostSnippet",
    "Brief",
    "BudgetSnippet",
    "Chunk",
    "Contact",
    "ContractRegistry",
    "ConversationState",
    "DialogState",
    "DocumentStatus",
    "EXCLUDE",
    "Evidence",
    "Extraction",
    "Goal",
    "IdSnippet",
    "Intent",
    "KnowledgeQuery",
    "KnowledgeResult",
    "Lead",
    "Message",
    "PinSnippet",
    "PolicyDecision",
    "Product",
    "ProductScore",
    "Reply",
    "ReplyPlan",
    "Role",
    "Rule",
    "RuleHit",
    "Scope",
    "ScopeSnippet",
    "ScoreBoard",
    "ScoringParams",
    "SectionKind",
    "Segment",
    "SlotSpec",
    "SlotStatus",
    "SlotType",
    "SlotUpdate",
    "SlotValue",
    "Snippet",
    "SoftSignal",
    "TextSnippet",
    "Thresholds",
    "Topic",
]
