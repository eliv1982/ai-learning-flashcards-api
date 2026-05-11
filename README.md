# AI Learning Flashcards API

Учебный мини-проект на **FastAPI**: REST API с карточками по темам **AI**, **LLM**, **RAG**, **Git**, **Docker** и **CI/CD**. Данные хранятся в `data/flashcards.json` (без БД и внешних API).

## Возможности

| Метод | Путь | Описание |
|--------|------|-----------|
| GET | `/` | Описание сервиса и список эндпоинтов |
| GET | `/health` | Проверка работоспособности: `{"status": "ok"}` |
| GET | `/cards` | Все карточки |
| GET | `/cards/random` | Случайная карточка |
| GET | `/cards/{card_id}` | Карточка по `id` (404, если нет) |
| GET | `/quiz` | Случайная карточка без поля `answer` (`id`, `topic`, `question`, `hint`) |

Интерактивная документация: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## Локальный запуск

Требуется Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Linux/macOS: `source .venv/bin/activate` вместо активации через `Scripts`.

## Запуск через Docker

Сборка и запуск контейнера на порту **8000**:

```bash
docker build -t ai-learning-flashcards-api .
docker run --rm -p 8000:8000 ai-learning-flashcards-api
```

Проверка: откройте [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

## GitHub Actions (CI): сборка и публикация образа

Файл workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

**Когда запускается:** при каждом `push` в ветку `main`.

**Что делает:**

1. **actions/checkout** — клонирует репозиторий в раннер.
2. **docker/setup-buildx-action** — включает Buildx для сборки образов.
3. **docker/login-action** — вход в **GitHub Container Registry** (`ghcr.io`) с использованием встроенного **`secrets.GITHUB_TOKEN`** (отдельные пароли для GHCR в секретах не задаются).
4. **docker/metadata-action** — формирует теги и labels для образа (в том числе `latest` для ветки по умолчанию и тег по SHA коммита).
5. **docker/build-push-action** — собирает образ из `Dockerfile` в корне репозитория и **пушит** его в `ghcr.io/<владелец>/<репозиторий>`.

SSH-деплой на сервер в workflow **не используется** — только сборка и публикация образа (учебный сценарий CI/CD).

После успешного прогона образ можно скачать, указав свой namespace и имя репозитория GitHub, например:

```bash
docker pull ghcr.io/<OWNER>/<REPO>:latest
```

Для приватного пакета может потребоваться `docker login ghcr.io` с Personal Access Token с правом `read:packages`.

## Лицензия

Учебный проект для домашнего задания.
