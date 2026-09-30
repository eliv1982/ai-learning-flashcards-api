import pytest
from fastapi.testclient import TestClient

from app.cards import Card, load_cards
from app.schemas import QuizQuestion
from main import DATA_PATH, create_app

CARDS = load_cards(DATA_PATH)
CARD_IDS = {c.id for c in CARDS}
QUIZ_FIELDS = {"id", "topic", "question", "hint"}


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["docs"] == "/docs"
    assert body["endpoints"]["card_by_id"] == "/cards/{card_id}"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cards_list_matches_dataset(client):
    response = client.get("/cards")
    assert response.status_code == 200
    assert [Card.model_validate(item) for item in response.json()] == CARDS


def test_card_by_id(client):
    response = client.get("/cards/1")
    assert response.status_code == 200
    assert Card.model_validate(response.json()) == CARDS[0]


def test_unknown_card_is_404(client):
    response = client.get("/cards/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Card not found"}


def test_non_integer_card_id_is_422(client):
    response = client.get("/cards/abc")
    assert response.status_code == 422


def test_random_route_is_not_shadowed_by_card_id(client):
    for _ in range(20):
        response = client.get("/cards/random")
        assert response.status_code == 200
        assert Card.model_validate(response.json()).id in CARD_IDS


def test_quiz_exposes_only_public_fields(client):
    for _ in range(20):
        response = client.get("/quiz")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == QUIZ_FIELDS
        assert body["id"] in CARD_IDS
        assert all(c.answer not in response.text for c in CARDS)


def test_quiz_model_cannot_carry_an_answer():
    assert set(QuizQuestion.model_fields) == QUIZ_FIELDS
    leaky = QuizQuestion.model_validate(CARDS[0], from_attributes=True)
    assert "answer" not in leaky.model_dump()


@pytest.fixture
def openapi(client):
    return client.get("/openapi.json").json()


def _schema_ref(operation, status="200"):
    return operation["responses"][status]["content"]["application/json"]["schema"]


def test_openapi_declares_typed_schemas(openapi):
    schemas = openapi["components"]["schemas"]
    for name in ["Card", "QuizQuestion", "HealthResponse", "RootResponse", "ErrorResponse"]:
        assert name in schemas

    paths = openapi["paths"]
    assert _schema_ref(paths["/health"]["get"]) == {"$ref": "#/components/schemas/HealthResponse"}
    assert _schema_ref(paths["/"]["get"]) == {"$ref": "#/components/schemas/RootResponse"}
    cards_schema = _schema_ref(paths["/cards"]["get"])
    assert cards_schema["type"] == "array"
    assert cards_schema["items"] == {"$ref": "#/components/schemas/Card"}
    assert _schema_ref(paths["/cards/random"]["get"]) == {"$ref": "#/components/schemas/Card"}
    assert _schema_ref(paths["/cards/{card_id}"]["get"]) == {"$ref": "#/components/schemas/Card"}
    assert _schema_ref(paths["/quiz"]["get"]) == {"$ref": "#/components/schemas/QuizQuestion"}


def test_openapi_quiz_schema_has_no_answer(openapi):
    schemas = openapi["components"]["schemas"]
    assert set(schemas["QuizQuestion"]["properties"]) == QUIZ_FIELDS
    assert "answer" in schemas["Card"]["properties"]


def test_openapi_documents_card_404(openapi):
    operation = openapi["paths"]["/cards/{card_id}"]["get"]
    assert _schema_ref(operation, "404") == {"$ref": "#/components/schemas/ErrorResponse"}
    assert "422" in operation["responses"]


def test_lifespan_can_be_recreated_without_leaking_state(recording_logger):
    app = create_app(logger=recording_logger)
    for _ in range(2):
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 200
    assert recording_logger.started == recording_logger.stopped == 2
