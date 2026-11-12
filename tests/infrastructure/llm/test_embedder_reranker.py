import json
from pathlib import Path

import httpx

from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.embedder import GatewayEmbedder
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.infrastructure.llm.reranker import GatewayReranker


def gateway_recording(batches: list[list[str]]) -> LlmGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path.endswith("/rerank"):
            batches.append(payload["documents"])
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})
        batches.append(payload["input"])
        data = [
            {"index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(payload["input"])
        ]
        return httpx.Response(200, json={"data": data})

    settings = Settings.from_env({"LLM_BASE_URL": "http://p", "LLM_API_KEY": "k"})
    return LlmGateway(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_embedder_batches_dedupes_and_caches() -> None:
    batches: list[list[str]] = []
    embedder = GatewayEmbedder(gateway_recording(batches), "m", batch_size=2)
    vectors = embedder.embed(["a", "bb", "a", "ccc"])
    assert vectors == [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0], [3.0, 1.0]]
    assert batches == [["a", "bb"], ["ccc"]]
    embedder.embed(["bb"])
    assert len(batches) == 2


def test_embedder_disk_cache_survives_restart(tmp_path: Path) -> None:
    batches: list[list[str]] = []
    GatewayEmbedder(gateway_recording(batches), "org/m", cache_dir=tmp_path).embed(["x"])
    fresh = GatewayEmbedder(gateway_recording(batches), "org/m", cache_dir=tmp_path)
    assert fresh.embed(["x"]) == [[1.0, 1.0]]
    assert len(batches) == 1
    assert (tmp_path / "org_m.jsonl").exists()


def test_reranker_trims_documents_and_skips_empty() -> None:
    batches: list[list[str]] = []
    reranker = GatewayReranker(gateway_recording(batches), max_document_chars=3)
    assert reranker.rerank("q", [], 2) == []
    assert reranker.rerank("q", ["abcdef"], 1) == [(0, 0.5)]
    assert batches == [["abc"]]
