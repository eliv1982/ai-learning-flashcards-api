import json

import pytest
from fastapi.testclient import TestClient

from app.cards import Card, CardDataError, load_cards, parse_cards
from main import DATA_PATH, create_app

VALID_CARD = {
    "id": 1,
    "topic": "Git",
    "question": "Что делает git merge?",
    "answer": "Объединяет ветки.",
    "hint": "Слияние",
}
TEXT_FIELDS = ["topic", "question", "answer", "hint"]


def card(**overrides):
    return {**VALID_CARD, **overrides}


def test_valid_dataset_loads():
    cards = parse_cards([card(id=1), card(id=2)])
    assert [c.id for c in cards] == [1, 2]
    assert all(isinstance(c, Card) for c in cards)


def test_shipped_dataset_loads():
    cards = load_cards(DATA_PATH)
    assert len(cards) > 0
    assert len({c.id for c in cards}) == len(cards)


@pytest.mark.parametrize("field", ["id", *TEXT_FIELDS])
def test_missing_required_field_fails(field):
    broken = {k: v for k, v in VALID_CARD.items() if k != field}
    with pytest.raises(CardDataError, match=field):
        parse_cards([broken])


@pytest.mark.parametrize("field", TEXT_FIELDS)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_text_field_fails(field, blank):
    with pytest.raises(CardDataError, match=field):
        parse_cards([card(**{field: blank})])


@pytest.mark.parametrize(
    "bad",
    [
        card(id="1"),
        card(id=True),
        card(id=1.5),
        card(topic=5),
        card(unexpected="x"),
    ],
    ids=["str-id", "bool-id", "float-id", "int-topic", "unknown-field"],
)
def test_wrong_types_and_unknown_fields_fail(bad):
    with pytest.raises(CardDataError, match="index 0"):
        parse_cards([bad])


@pytest.mark.parametrize(
    "raw",
    [{"cards": []}, "cards", 42, None, ["not-a-card"], [None], [[1, 2]]],
    ids=["dict-root", "str-root", "int-root", "null-root", "str-item", "null-item", "list-item"],
)
def test_malformed_structure_fails(raw):
    with pytest.raises(CardDataError):
        parse_cards(raw)


def test_empty_dataset_fails():
    with pytest.raises(CardDataError, match="at least one card"):
        parse_cards([])


def test_duplicate_ids_fail():
    with pytest.raises(CardDataError, match="duplicate card id 7"):
        parse_cards([card(id=7), card(id=8), card(id=7)])


def test_one_bad_card_rejects_the_whole_dataset():
    with pytest.raises(CardDataError, match="index 1"):
        parse_cards([card(id=1), card(id=2, hint=""), card(id=3)])


def test_load_cards_rejects_invalid_json(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text("[{", encoding="utf-8")
    with pytest.raises(CardDataError, match="not valid"):
        load_cards(path)


def test_load_cards_rejects_missing_file(tmp_path):
    with pytest.raises(CardDataError, match="cannot read"):
        load_cards(tmp_path / "missing.json")


def test_load_cards_reads_utf8(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps([card(question="Что такое RAG?")], ensure_ascii=False), encoding="utf-8")
    assert load_cards(path)[0].question == "Что такое RAG?"


def test_invalid_data_prevents_application_startup(tmp_path, recording_logger):
    path = tmp_path / "cards.json"
    path.write_text("[]", encoding="utf-8")
    app = create_app(cards_path=path, logger=recording_logger)

    with pytest.raises(CardDataError):
        with TestClient(app):
            pass

    assert recording_logger.started == 0


@pytest.mark.parametrize("item", load_cards(DATA_PATH), ids=lambda c: f"card-{c.id}")
def test_hint_does_not_reveal_answer(item):
    hint = " ".join(item.hint.casefold().split())
    answer = " ".join(item.answer.casefold().split())
    assert hint != answer
    assert hint not in answer
    assert answer not in hint


@pytest.mark.parametrize(
    ("card_id", "leaked_phrase"),
    [(1, "retrieval"), (2, "large language model")],
)
def test_audited_hints_no_longer_spell_out_the_acronym(card_id, leaked_phrase):
    hint = next(c.hint for c in load_cards(DATA_PATH) if c.id == card_id)
    assert leaked_phrase not in hint.casefold()
