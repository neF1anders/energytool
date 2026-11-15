from pathlib import Path

from energytool.domain.reply import StyleExample
from energytool.infrastructure.llm.style_examples import load_style_examples
from tools.build_submission import read_style_examples, with_style_examples


def _write_examples(directory: Path) -> None:
    (directory / "demo_questions.csv").write_text(
        '﻿id,question\n1,"Панели на фасад ставите?"\n2,Без ответа\n3,"Сколько, стоит?"\n',
        encoding="utf-8",
    )
    (directory / "demo_output.csv").write_text(
        'id,answer\n1,"Да, ставим."\n2,\n3,"Считаем ""под задачу""."\n', encoding="utf-8"
    )


def test_examples_are_read_from_directory_without_empty_answers(tmp_path: Path) -> None:
    _write_examples(tmp_path)
    assert load_style_examples(tmp_path) == (
        StyleExample("Панели на фасад ставите?", "Да, ставим."),
        StyleExample("Сколько, стоит?", 'Считаем "под задачу".'),
    )


def test_without_directory_and_embedded_pairs_there_are_no_examples() -> None:
    assert load_style_examples(None) == ()


def test_build_embeds_examples_into_module_source(tmp_path: Path) -> None:
    _write_examples(tmp_path)
    source = "X = 1\nEMBEDDED: tuple[tuple[str, str], ...] = ()\n"
    embedded = with_style_examples(source, read_style_examples(tmp_path))
    scope: dict[str, object] = {}
    exec(embedded, scope)
    assert scope["EMBEDDED"] == (
        ("Панели на фасад ставите?", "Да, ставим."),
        ("Сколько, стоит?", 'Считаем "под задачу".'),
    )


def test_example_matches_last_turn_of_its_dialog() -> None:
    example = StyleExample("Пользователь: Мы завод\nПользователь: Нам  подходит опт?", "Да.")
    assert example.is_about("нам подходит опт?")
    assert not example.is_about("Мы завод")
    assert not example.is_about("")
