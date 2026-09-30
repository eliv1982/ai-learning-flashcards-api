import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, ValidationError


class CardDataError(ValueError):
    """The flashcards dataset is missing, malformed or inconsistent."""


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, StringConstraints(min_length=1), AfterValidator(_not_blank)]


class Card(BaseModel):
    """A flashcard. Strict: no type coercion and no unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: int
    topic: NonBlankStr
    question: NonBlankStr
    answer: NonBlankStr
    hint: NonBlankStr


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<card>'}: {error['msg']}"
        for error in exc.errors()
    )


def parse_cards(raw: Any) -> list[Card]:
    if not isinstance(raw, list):
        raise CardDataError(
            f"cards data must be a JSON list of cards, got {type(raw).__name__}"
        )
    if not raw:
        raise CardDataError("cards data must contain at least one card")

    cards: list[Card] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(raw):
        try:
            card = Card.model_validate(item)
        except ValidationError as exc:
            raise CardDataError(f"card at index {index} is invalid: {_describe(exc)}") from exc
        if card.id in seen_ids:
            raise CardDataError(f"duplicate card id {card.id} at index {index}")
        seen_ids.add(card.id)
        cards.append(card)
    return cards


def load_cards(path: Path) -> list[Card]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CardDataError(f"cannot read cards file {path}: {exc}") from exc
    except ValueError as exc:
        raise CardDataError(f"cards file {path} is not valid UTF-8 JSON: {exc}") from exc
    return parse_cards(raw)
