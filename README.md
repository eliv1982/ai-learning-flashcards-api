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

Локально интерактивная документация: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Развёрнутое приложение (CI/CD): [http://85.198.69.132:8010/docs](http://85.198.69.132:8010/docs).

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

## GitHub Actions (CI/CD)

Файл workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

**Триггер:** workflow запускается при каждом `push` в ветку `main`.

### Job `build-and-push`

Собирает Docker-образ приложения и публикует его в **GitHub Container Registry** (`ghcr.io`):

1. **actions/checkout** — клонирует репозиторий в раннер.
2. **docker/setup-buildx-action** — включает Buildx для сборки образов.
3. **docker/login-action** — вход в GHCR от имени пользователя GitHub Actions.
4. **docker/metadata-action** — формирует теги и labels (в том числе `latest` для ветки по умолчанию и тег по SHA коммита).
5. **docker/build-push-action** — собирает образ из `Dockerfile` и отправляет его в `ghcr.io/<владелец>/<репозиторий>`.

Образ с тегом `latest` можно вручную скачать так (подставьте свой путь к пакету):

```bash
docker pull ghcr.io/<OWNER>/<REPO>:latest
```

### Job `deploy`

Запускается **только после успешного** завершения `build-and-push` (`needs: build-and-push`).

- Подключение к серверу выполняется через [**appleboy/ssh-action**](https://github.com/appleboy/ssh-action).
- Параметры SSH задаются в **GitHub Secrets** репозитория (значения в репозиторий не коммитятся): `SSH_HOST`, `SSH_USERNAME`, `SSH_KEY`, `SSH_PORT`.

На сервере по SSH последовательно выполняются:

- `docker login ghcr.io` — чтобы скачать образ из GHCR;
- `docker pull` образа `ghcr.io/<владелец>/<репозиторий>:latest` (имя совпадает с репозиторием на GitHub, см. workflow);
- остановка и удаление предыдущего контейнера `ai-learning-flashcards-api` (если он был);
- `docker run` нового контейнера с политикой перезапуска **`unless-stopped`** и пробросом порта **`8010:8000`** (снаружи **8010**, внутри контейнера приложение слушает **8000**).

После деплоя Swagger/OpenAPI доступен по адресу **[http://85.198.69.132:8010/docs](http://85.198.69.132:8010/docs)**.

В README не указываются приватные ключи, токены и значения секретов — их нужно настроить только в настройках репозитория на GitHub.

## Лицензия

Учебный проект для домашнего задания.
