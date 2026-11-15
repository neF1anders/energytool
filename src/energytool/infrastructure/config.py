"""Settings: инфраструктурные параметры из окружения (docs/spec.md §10).

Бизнес-параметры (пороги, веса правил) здесь не живут — они в domain (§5.5).

Профили (MODELS_PROFILE):
- platform   — прокси организаторов, адрес и ключ передаёт платформа;
- openrouter — локальная разработка: OpenRouter, ключ из .env
  (`uv run --env-file .env ...`).

Имена переменных и их синонимы совпадают с примером сабмита организаторов. Ключи
хранятся в полях с repr=False и в лог попадают только через safe_summary().
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Имена моделей платформы из примера сабмита; используются, только если env не задал свои.
PLATFORM_LLM_MODEL = "qwen/qwen3.8-27b"
PLATFORM_EMB_MODEL = "baai/bge-m3"
PLATFORM_RERANKER_MODEL = "qwen/qwen3-reranker-8b"

LogFormat = Literal["json", "text"]
ModelProvider = Literal["remote", "local"]


class ConfigError(ValueError):
    """Окружение не позволяет запустить нужную часть системы."""


class ModelsProfile(StrEnum):
    PLATFORM = "platform"
    OPENROUTER = "openrouter"


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    """Адрес, ключ и модель одного OpenAI-совместимого эндпоинта."""

    base_url: str
    model: str
    api_key: str = field(default="", repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True, slots=True)
class Timeouts:
    chat_sec: float = 30.0
    embed_sec: float = 10.0
    rerank_sec: float = 10.0


@dataclass(frozen=True, slots=True)
class Settings:
    profile: ModelsProfile
    data_dir: Path
    llm: ModelEndpoint
    embedder: ModelEndpoint
    reranker: ModelEndpoint
    embedder_provider: ModelProvider = "remote"
    reranker_provider: ModelProvider = "remote"
    temperature: float = 0.1
    max_tokens: int = 1024
    max_workers: int = 4
    top_k: int = 4
    answer_top_k: int = 8  # разделов в ответе этапа 1; TOP_K платформы на него не влияет
    answer_keep_ratio: float = 0.4  # отсечение разделов слабее 0.4 от лучшего скора реранкера
    answer_keep_floor: float = 0.25
    answer_min_score: float = 0.15  # лучший скор ниже — в базе нет ответа (отказ или специалист)
    answer_context_tokens: int = 5000  # бюджет найденных разделов в промпте этапа 1
    batch_condense: bool = (
        False  # вопрос для поиска из истории диалога (один вызов LLM); на eval без прироста
    )
    batch_guardrails: bool = True  # разделы-ограничения базы в ответе этапа 1
    dialog_topic_k: int = 0  # диалог: разделов по предыдущим репликам клиента (0 — выключено)
    answer_expand: str = "none"  # none | siblings | file: соседи найденных разделов в промпте
    answer_expand_tokens: int = 3000
    answer_layout: str = "facts_first"  # facts_first | rules_first: порядок блоков промпта
    answer_evidence_order: str = "best_first"  # best_first | best_last
    style_examples_dir: Path | None = None  # разработка: каталог с примерами организаторов
    style_examples_holdout: bool = False  # eval: не показывать образец с тем же вопросом
    style_examples_mode: str = "block"  # block — список в системном промпте; turns — примеры
    style_shots_k: int = 3  # ближайших образцов на вопрос в режиме turns
    reranker_top_n: int = 4
    timeouts: Timeouts = field(default_factory=Timeouts)
    max_retries: int = 3
    disable_thinking: bool = True
    embedding_cache_dir: Path | None = None
    llm_cache_dir: Path | None = None  # только разработка: кэш ответов моделей на диске
    retrieval_config: str = "hybrid_rerank"
    log_level: str = "INFO"
    log_format: LogFormat = "json"
    run_budget_sec: int = 1080
    fast_path_after_sec: int = 900
    batch_pipeline: str = "naive"  # naive | full: на демо равны по качеству, naive вдвое быстрее
    batch_ask: bool = False  # вопрос анкеты в ответе этапа 1: на демо слегка хуже
    sqlite_path: Path = Path("var/energytool.sqlite")
    crm_webhook_url: str = ""  # пусто — заявка пишется в leads со статусом mock_sent

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        reader = _EnvReader(env)
        profile = _parse_profile(reader.text("MODELS_PROFILE", ModelsProfile.PLATFORM.value))
        llm = _llm_endpoint(reader, profile)
        return cls(
            profile=profile,
            data_dir=Path(reader.text("DATA_DIR", "data")),
            llm=llm,
            embedder=ModelEndpoint(
                base_url=reader.url("EMB_BASE_URL") or llm.base_url,
                api_key=reader.first("EMB_API_KEY", "EMBEDDER_API_KEY") or llm.api_key,
                model=reader.text("EMB_MODEL", PLATFORM_EMB_MODEL),
            ),
            reranker=ModelEndpoint(
                base_url=reader.url("RERANKER_BASE_URL") or llm.base_url,
                api_key=reader.text("RERANKER_API_KEY") or llm.api_key,
                model=reader.text("RERANKER_MODEL", PLATFORM_RERANKER_MODEL),
            ),
            embedder_provider=_parse_provider(reader.text("EMB_PROVIDER", "remote")),
            reranker_provider=_parse_provider(reader.text("RERANKER_PROVIDER", "remote")),
            temperature=reader.number("TEMPERATURE", 0.1, 0.0, 2.0),
            max_tokens=reader.integer("MAX_TOKENS", 1024, 128, 8192),
            max_workers=reader.integer("MAX_WORKERS", 4, 1, 16),
            top_k=reader.integer("TOP_K", 4, 1, 30),
            answer_top_k=reader.integer("ANSWER_TOP_K", 8, 1, 30),
            answer_keep_ratio=reader.number("ANSWER_KEEP_RATIO", 0.4, 0.0, 1.0),
            answer_keep_floor=reader.number("ANSWER_KEEP_FLOOR", 0.25, 0.0, 1.0),
            answer_min_score=reader.number("ANSWER_MIN_SCORE", 0.15, 0.0, 1.0),
            answer_context_tokens=reader.integer("ANSWER_CONTEXT_TOKENS", 5000, 500, 20000),
            batch_condense=reader.flag("BATCH_CONDENSE", default=False),
            style_examples_dir=Path(examples)
            if (examples := reader.text("STYLE_EXAMPLES_DIR"))
            else None,
            style_examples_holdout=reader.flag("STYLE_EXAMPLES_HOLDOUT", default=False),
            style_examples_mode=reader.choice("STYLE_EXAMPLES_MODE", ("block", "turns")),
            style_shots_k=reader.integer("STYLE_SHOTS_K", 3, 1, 10),
            batch_guardrails=reader.flag("BATCH_GUARDRAILS", default=True),
            dialog_topic_k=reader.integer("DIALOG_TOPIC_K", 0, 0, 8),
            answer_expand=reader.choice("ANSWER_EXPAND", ("none", "siblings", "file")),
            answer_expand_tokens=reader.integer("ANSWER_EXPAND_TOKENS", 3000, 200, 20000),
            answer_layout=reader.choice("ANSWER_LAYOUT", ("facts_first", "rules_first")),
            answer_evidence_order=reader.choice(
                "ANSWER_EVIDENCE_ORDER", ("best_first", "best_last")
            ),
            reranker_top_n=reader.integer("RERANKER_TOP_N", 4, 1, 30),
            max_retries=reader.integer("LLM_MAX_RETRIES", 3, 0, 5),
            disable_thinking=reader.flag("LLM_DISABLE_THINKING", default=True),
            embedding_cache_dir=Path(cache) if (cache := reader.text("EMB_CACHE_DIR")) else None,
            llm_cache_dir=Path(llm_cache) if (llm_cache := reader.text("LLM_CACHE_DIR")) else None,
            retrieval_config=reader.text("RETRIEVAL_CONFIG", "hybrid_rerank"),
            log_level=reader.text("LOG_LEVEL", "INFO").upper(),
            log_format="text" if reader.text("LOG_FORMAT", "json") == "text" else "json",
            run_budget_sec=reader.integer("RUN_BUDGET_SEC", 1080, 60, 1200),
            fast_path_after_sec=reader.integer("FAST_PATH_SEC", 900, 0, 1200),
            batch_pipeline="full" if reader.text("BATCH_PIPELINE") == "full" else "naive",
            batch_ask=reader.flag("BATCH_ASK", default=False),
            sqlite_path=Path(reader.text("SQLITE_PATH", "var/energytool.sqlite")),
            crm_webhook_url=reader.url("CRM_WEBHOOK_URL"),
        )

    def require_llm(self) -> None:
        """Fail fast на старте точек входа, которым нужна генеративная модель."""
        if not self.llm.configured:
            missing = [
                name
                for name, value in (
                    ("LLM_BASE_URL", self.llm.base_url),
                    ("LLM_API_KEY", self.llm.api_key),
                    ("LLM_MODEL", self.llm.model),
                )
                if not value
            ]
            raise ConfigError(f"профиль {self.profile}: не заданы {', '.join(missing)}")

    def safe_summary(self) -> dict[str, object]:
        """Настройки для лога старта: без ключей, только факт их наличия."""
        return {
            "profile": self.profile.value,
            "data_dir": str(self.data_dir),
            "llm": _endpoint_summary(self.llm),
            "embedder": {**_endpoint_summary(self.embedder), "provider": self.embedder_provider},
            "reranker": {**_endpoint_summary(self.reranker), "provider": self.reranker_provider},
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_workers": self.max_workers,
            "top_k": self.top_k,
            "reranker_top_n": self.reranker_top_n,
            "max_retries": self.max_retries,
            "disable_thinking": self.disable_thinking,
            "embedding_cache_dir": str(self.embedding_cache_dir or ""),
            "retrieval_config": self.retrieval_config,
            "run_budget_sec": self.run_budget_sec,
            "fast_path_after_sec": self.fast_path_after_sec,
            "batch_pipeline": self.batch_pipeline,
            "batch_ask": self.batch_ask,
            "sqlite_path": str(self.sqlite_path),
            "crm_webhook": bool(self.crm_webhook_url),
        }


def _llm_endpoint(reader: _EnvReader, profile: ModelsProfile) -> ModelEndpoint:
    default_url = OPENROUTER_BASE_URL if profile is ModelsProfile.OPENROUTER else ""
    # для openrouter id модели обязателен в .env: каталог OpenRouter не совпадает с платформой
    default_model = PLATFORM_LLM_MODEL if profile is ModelsProfile.PLATFORM else ""
    return ModelEndpoint(
        base_url=reader.url("LLM_BASE_URL", "BASE_URL", "OPENAI_BASE_URL") or default_url,
        api_key=reader.first("LLM_API_KEY", "OPENAI_API_KEY"),
        model=reader.first("LLM_MODEL", "GEN_MODEL", "OPENAI_MODEL") or default_model,
    )


def _endpoint_summary(endpoint: ModelEndpoint) -> dict[str, object]:
    return {
        "base_url": endpoint.base_url,
        "model": endpoint.model,
        "has_key": bool(endpoint.api_key),
    }


def _parse_profile(raw: str) -> ModelsProfile:
    try:
        return ModelsProfile(raw.lower())
    except ValueError as exc:
        options = ", ".join(p.value for p in ModelsProfile)
        raise ConfigError(f"MODELS_PROFILE={raw!r}: ожидается одно из {options}") from exc


def _parse_provider(raw: str) -> ModelProvider:
    if raw == "local":
        return "local"
    if raw == "remote":
        return "remote"
    raise ConfigError(f"провайдер модели {raw!r}: ожидается remote или local")


@dataclass(frozen=True, slots=True)
class _EnvReader:
    """Чтение env по правилам примера сабмита: пустое значение или значение, равное
    имени переменной (незаполненный шаблон платформы), считается отсутствующим."""

    env: Mapping[str, str]

    def text(self, name: str, default: str = "") -> str:
        value = self.env.get(name, "").strip()
        return default if not value or value == name else value

    def first(self, *names: str) -> str:
        return next((value for name in names if (value := self.text(name))), "")

    def url(self, *names: str) -> str:
        return self.first(*names).rstrip("/")

    def choice(self, name: str, options: tuple[str, ...]) -> str:
        """Одно из значений; пустое или неизвестное — первое (умолчание)."""
        value = self.text(name).lower()
        return value if value in options else options[0]

    def integer(self, name: str, default: int, lower: int, upper: int) -> int:
        try:
            return max(lower, min(upper, int(self.text(name))))
        except ValueError:
            return default

    def number(self, name: str, default: float, lower: float, upper: float) -> float:
        try:
            return max(lower, min(upper, float(self.text(name))))
        except ValueError:
            return default

    def flag(self, name: str, *, default: bool) -> bool:
        value = self.text(name).lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return default
