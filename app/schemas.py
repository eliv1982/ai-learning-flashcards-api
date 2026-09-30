from typing import Literal

from pydantic import BaseModel


class QuizQuestion(BaseModel):
    """Public quiz payload. It deliberately has no ``answer`` field."""

    id: int
    topic: str
    question: str
    hint: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class RootResponse(BaseModel):
    service: str
    description: str
    docs: str
    endpoints: dict[str, str]


class ErrorResponse(BaseModel):
    detail: str
