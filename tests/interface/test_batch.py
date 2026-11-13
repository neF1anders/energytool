import csv
from pathlib import Path

from energytool.application.answer_question import AnswerMode
from energytool.interface.batch.run import Deadline
from energytool.interface.batch.submission import EMPTY_ANSWER, normalize_answer, write_submission
from tools.build_submission import parse_defaults, with_defaults


def test_deadline_modes() -> None:
    assert Deadline(100, 200).mode() is AnswerMode.FULL
    assert Deadline(0, 200).mode() is AnswerMode.FAST
    assert Deadline(0, 0).mode() is AnswerMode.NO_LLM


def test_submission_dedupes_and_normalizes(tmp_path: Path) -> None:
    path = tmp_path / "submission.csv"
    assert write_submission(path, [("1", "a\nb"), ("1", "dup"), ("2", "  ")]) == 2
    with path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [["id", "answer"], ["1", "a b"], ["2", EMPTY_ANSWER]]
    assert len(normalize_answer("x" * 20_000)) == 10_000


def test_zip_defaults_are_injected_after_sys_import() -> None:
    source = "import sys\n\nfrom x import main\n"
    patched = with_defaults(source, parse_defaults(["BATCH_PIPELINE=full"]))
    assert patched.index("os.environ.setdefault('BATCH_PIPELINE', 'full')") < patched.index(
        "from x"
    )
