from energytool.application.knowledge_request import query_from_history
from energytool.application.transcript import last_user_message, parse_transcript
from energytool.domain.knowledge import BudgetSnippet, TextSnippet
from energytool.domain.state import Role

DIALOG = """Пользователь: Мы электросетевая компания.
Хотим лучше находить потери.
Ассистент: Какие данные учёта у вас есть?
Пользователь: Примерно за два года. Хватит?"""


def test_single_question_is_one_user_message() -> None:
    (message,) = parse_transcript("У нас шиферная крыша. Стоит ли?")
    assert message.role is Role.USER
    assert message.text == "У нас шиферная крыша. Стоит ли?"


def test_dialog_roles_and_continuation_lines() -> None:
    messages = parse_transcript(DIALOG)
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert messages[0].text == "Мы электросетевая компания. Хотим лучше находить потери."
    assert last_user_message(messages) == "Примерно за два года. Хватит?"


def test_label_synonyms_and_case() -> None:
    messages = parse_transcript("клиент: привет\nБОТ: здравствуйте\nКлиент: вопрос")
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT, Role.USER]


def test_multiline_question_without_labels_stays_one_message() -> None:
    assert len(parse_transcript("строка один\nстрока два")) == 1


def test_history_query_weights_and_context() -> None:
    query = query_from_history(parse_transcript(DIALOG), BudgetSnippet(top_k=3))
    texts = [s for s in query if isinstance(s, TextSnippet)]
    assert [(t.text, t.weight, t.role) for t in texts] == [
        ("Примерно за два года. Хватит?", 1.0, "query"),
        ("Мы электросетевая компания. Хотим лучше находить потери.", 0.5, "query"),
        ("Какие данные учёта у вас есть?", 1.0, "context"),
    ]
    assert query[-1] == BudgetSnippet(top_k=3)


def test_single_question_query_has_no_context() -> None:
    query = query_from_history(parse_transcript("вопрос"))
    assert [type(s).__name__ for s in query] == ["TextSnippet", "BudgetSnippet"]
