import httpx
import pytest

from energytool.bootstrap import Container
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.infrastructure.telemetry import LogTelemetry
from energytool.interface.cli import ping


def container_with(handler: httpx.MockTransport) -> Container:
    settings = Settings.from_env(
        {"LLM_BASE_URL": "http://proxy/v1", "LLM_API_KEY": "k", "LLM_MODEL": "qwen"}
    )
    gateway = LlmGateway(settings, client=httpx.Client(transport=handler))
    return Container(settings=settings, gateway=gateway, telemetry=LogTelemetry())


def fake_proxy(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/chat/completions"):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Да"}, "finish_reason": "stop"}]}
        )
    if request.url.path.endswith("/embeddings"):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    return httpx.Response(404, text="no rerank here")


def test_ping_passes_when_only_optional_models_fail(capsys: pytest.CaptureFixture[str]) -> None:
    assert ping(container_with(httpx.MockTransport(fake_proxy))) == 0
    out = capsys.readouterr().out
    assert "[ok]   chat" in out
    assert "[ok]   embed: dim=2" in out
    assert "[fail] rerank" in out


def test_ping_fails_when_chat_fails() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(401))
    assert ping(container_with(transport)) == 1
