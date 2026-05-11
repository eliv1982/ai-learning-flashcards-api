import json
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

DATA_PATH = Path(__file__).resolve().parent / "data" / "flashcards.json"

_cards: list[dict] = []


def _load_cards() -> list[dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cards
    _cards = _load_cards()
    yield


app = FastAPI(
    title="AI Learning Flashcards API",
    description="Учебное API с карточками по AI, LLM, RAG, Git, Docker и CI/CD.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def root() -> dict:
    return {
        "service": "AI Learning Flashcards API",
        "description": "Мини-API с учебными карточками по AI, LLM, RAG, Git, Docker и CI/CD.",
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "all_cards": "/cards",
            "random_card": "/cards/random",
            "card_by_id": "/cards/{card_id}",
            "quiz": "/quiz",
        },
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/cards")
def list_cards() -> list[dict]:
    return _cards


@app.get("/cards/random")
def random_card() -> dict:
    if not _cards:
        raise HTTPException(status_code=404, detail="No cards available")
    return random.choice(_cards)


@app.get("/cards/{card_id}")
def get_card(card_id: int) -> dict:
    for card in _cards:
        if card.get("id") == card_id:
            return card
    raise HTTPException(status_code=404, detail="Card not found")


@app.get("/quiz")
def quiz() -> dict:
    if not _cards:
        raise HTTPException(status_code=404, detail="No cards available")
    card = random.choice(_cards)
    return {
        "id": card["id"],
        "topic": card["topic"],
        "question": card["question"],
        "hint": card["hint"],
    }
