import json
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from starlette.requests import Request

from app.cards import Card, load_cards
from app.observability import EventLogger, LokiLogger, RequestLoggingMiddleware
from app.schemas import ErrorResponse, HealthResponse, QuizQuestion, RootResponse

DATA_PATH = Path(__file__).resolve().parent / "data" / "flashcards.json"

router = APIRouter()


def get_cards(request: Request) -> list[Card]:
    return request.app.state.cards


Cards = Annotated[list[Card], Depends(get_cards)]


@router.get("/")
def root() -> RootResponse:
    return RootResponse(
        service="Learning Flashcards API",
        description="Мини-API с учебными карточками по AI, LLM, RAG, Git, Docker и CI/CD.",
        docs="/docs",
        endpoints={
            "health": "/health",
            "all_cards": "/cards",
            "random_card": "/cards/random",
            "card_by_id": "/cards/{card_id}",
            "quiz": "/quiz",
        },
    )


@router.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/cards")
def list_cards(cards: Cards) -> list[Card]:
    return cards


@router.get("/cards/random")
def random_card(cards: Cards) -> Card:
    return random.choice(cards)


@router.get(
    "/cards/{card_id}",
    responses={404: {"model": ErrorResponse, "description": "Card not found"}},
)
def get_card(card_id: int, cards: Cards) -> Card:
    for card in cards:
        if card.id == card_id:
            return card
    raise HTTPException(status_code=404, detail="Card not found")


@router.get("/quiz")
def quiz(cards: Cards) -> QuizQuestion:
    return QuizQuestion.model_validate(random.choice(cards), from_attributes=True)


def create_app(
    cards_path: Path = DATA_PATH, logger: EventLogger | None = None
) -> FastAPI:
    event_logger = logger if logger is not None else LokiLogger.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.cards = load_cards(cards_path)
        event_logger.start()
        event_logger.log(json.dumps({"event": "application_started"}))
        try:
            yield
        finally:
            event_logger.stop()

    app = FastAPI(
        title="Learning Flashcards API",
        description="Учебное API с карточками по AI, LLM, RAG, Git, Docker и CI/CD.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.add_middleware(RequestLoggingMiddleware, logger=event_logger)
    return app


app = create_app()
