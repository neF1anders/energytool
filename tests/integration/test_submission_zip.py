"""ZIP сабмита в чистом каталоге: импорты переписаны, прогон на фейковом прокси пишет
корректный submission.csv; при недоступном прокси ответы всё равно непустые."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools.build_submission import build

KB = {
    "20-products/23-solar.md": "# Солнце\n\n## Размещение\n\nМонтаж на кровлю и фасад.\n",
    "20-products/22-askue.md": "# Учёт\n\n## Кому подходит\n\nУчёт по цехам и объектам.\n",
}
QUESTIONS = [
    ("1", "Панели на фасад ставите?"),
    (
        "2",
        "Пользователь: Учёт по цехам нужен.\nАссистент: Сколько цехов?\nПользователь: Пять. Можно?",
    ),
    ("3", 'Можно, с запятой, и "кавычками"?'),
]


class FakeProxy(BaseHTTPRequestHandler):
    broken = False

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.broken:
            self._send(500, {"error": "down"})
        elif self.path.endswith("/embeddings"):
            data = [
                {"index": i, "embedding": [1.0, float(len(t) % 7)]}
                for i, t in enumerate(payload["input"])
            ]
            self._send(200, {"data": data})
        elif self.path.endswith("/rerank"):
            docs = payload["documents"]
            self._send(
                200,
                {
                    "results": [
                        {"index": i, "relevance_score": 1 / (i + 1)} for i in range(len(docs))
                    ]
                },
            )
        else:
            self._send(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": "Да, это возможно.\nУточните регион?"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {},
                },
            )

    def _send(self, status: int, body: object) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def proxy() -> Iterator[tuple[str, type[FakeProxy]]]:
    handler = type("Proxy", (FakeProxy,), {"broken": False})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", handler
    server.shutdown()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    archive = build(tmp_path / "submission.zip")
    run = tmp_path / "run"
    with zipfile.ZipFile(archive) as zf:
        assert all("/" not in name for name in zf.namelist())
        zf.extractall(run)
    data = run / "data"
    data.mkdir()
    with zipfile.ZipFile(data / "knowledge-base.zip", "w") as zf:
        for name, text in KB.items():
            zf.writestr(f"knowledge-base/{name}", text)
    with (data / "questions.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "question"])
        writer.writerows(QUESTIONS)
    return run


def run_solution(workdir: Path, base_url: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LLM_BASE_URL": base_url,
        "LLM_API_KEY": "fake",
        "LLM_MODEL": "qwen",
        "DATA_DIR": "data",
        "MAX_WORKERS": "2",
        **extra,
    }
    return subprocess.run(
        [sys.executable, "-E", "-s", "solution.py"],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def read_submission(workdir: Path) -> list[dict[str, str]]:
    with (workdir / "submission.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and list(rows[0]) == ["id", "answer"]
    return rows


def test_zip_runs_in_clean_dir(workdir: Path, proxy: tuple[str, type[FakeProxy]]) -> None:
    for py in workdir.glob("*.py"):
        assert "from energytool." not in py.read_text(encoding="utf-8")
    result = run_solution(workdir, proxy[0])
    assert result.returncode == 0, result.stderr[-2000:]
    rows = read_submission(workdir)
    assert [r["id"] for r in rows] == ["1", "2", "3"]
    assert all(r["answer"] == "Да, это возможно. Уточните регион?" for r in rows)
    assert '"turn_trace"' in result.stderr


def test_broken_proxy_still_gives_answers(
    workdir: Path, proxy: tuple[str, type[FakeProxy]]
) -> None:
    proxy[1].broken = True
    result = run_solution(workdir, proxy[0], LLM_MAX_RETRIES="0")
    assert result.returncode == 0, result.stderr[-2000:]
    rows = read_submission(workdir)
    assert len(rows) == 3
    assert all(r["answer"].strip() and "\n" not in r["answer"] for r in rows)
