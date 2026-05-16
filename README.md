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

С Loki (monitoring stack уже запущен, сеть создана):

```bash
docker network create flashcards-observability || true
docker run --rm -p 8000:8000 \
  --network flashcards-observability \
  -e APP_NAME=ai-learning-flashcards-api \
  -e LOKI_URL=http://loki:3100/loki/api/v1/push \
  ai-learning-flashcards-api
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
- `docker network create flashcards-observability || true` — общая сеть для API и monitoring stack;
- остановка и удаление предыдущего контейнера `ai-learning-flashcards-api` (если он был);
- `docker run` нового контейнера с политикой перезапуска **`unless-stopped`**, сетью **`flashcards-observability`**, переменными **`APP_NAME`** и **`LOKI_URL`**, пробросом порта **`8010:8000`** (снаружи **8010**, внутри контейнера приложение слушает **8000**).

После деплоя Swagger/OpenAPI доступен по адресу **[http://85.198.69.132:8010/docs](http://85.198.69.132:8010/docs)**.

API-контейнер отправляет логи в Loki по адресу `http://loki:3100/loki/api/v1/push` (имя хоста `loki` доступно только внутри сети `flashcards-observability`).

В README не указываются приватные ключи, токены и значения секретов — их нужно настроить только в настройках репозитория на GitHub.

## Monitoring with Loki and Grafana

Приложение отправляет логи напрямую в **Loki** через HTTP `POST /loki/api/v1/push` (без Promtail). Каждый HTTP-запрос логируется middleware: method, path, status_code, duration_ms. При старте приложения отправляется отдельная запись `Application started`.

### Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `LOKI_URL` | `http://localhost:3100/loki/api/v1/push` | URL push API Loki |
| `APP_NAME` | `ai-learning-flashcards-api` | Label `app` в Loki |

Если Loki недоступен, приложение **не падает** — в stdout выводится предупреждение `WARNING: Failed to send log to Loki: ...`.

### Запуск monitoring stack (локально)

Сначала создайте общую Docker-сеть (один раз; повторная команда безопасна):

```bash
docker network create flashcards-observability || true
```

Из корня репозитория:

```bash
cd monitoring
docker compose up -d
```

- **Grafana:** [http://localhost:3000](http://localhost:3000) (логин/пароль: `admin` / `admin`)
- **Loki** на хосте: `http://localhost:3100`
- **Loki** внутри Docker-сети `flashcards-observability`: `http://loki:3100`

Data Source Loki подключается автоматически через `monitoring/grafana/provisioning/datasources/datasources.yml`.

Оба сервиса (`loki`, `grafana`) и API-контейнер на сервере используют одну сеть **`flashcards-observability`** — см. `monitoring/docker-compose.yml` и job `deploy` в workflow.

### Server deployment: monitoring stack

На сервере (один раз, до или после первого деплоя API) разверните Loki и Grafana из репозитория:

```bash
git clone <URL-репозитория>   # или обновите уже клонированный каталог
cd ai-learning-flashcards-api
docker network create flashcards-observability || true
cd monitoring
docker compose up -d
docker compose ps
```

Проверка:

- Grafana: `http://<IP-сервера>:3000` (`admin` / `admin`)
- Loki push из контейнера API: `http://loki:3100/loki/api/v1/push` (сеть `flashcards-observability`)

После `push` в `main` GitHub Actions пересоздаёт API-контейнер уже в этой сети с нужными `-e LOKI_URL=...`. Убедитесь, что monitoring stack запущен **до** генерации трафика к API.

Сгенерируйте логи на сервере:

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/cards
curl http://127.0.0.1:8010/cards/999
```

Последний запрос даст **404** и запись с `level="ERROR"` в Loki.

### Запуск API с логированием

Локально (Loki проброшен на хост `3100`):

```bash
set LOKI_URL=http://localhost:3100/loki/api/v1/push
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Linux/macOS:

```bash
export LOKI_URL=http://localhost:3100/loki/api/v1/push
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Если приложение в **той же Docker-сети**, что и Loki (например, через `docker compose`):

```bash
LOKI_URL=http://loki:3100/loki/api/v1/push
```

Сделайте несколько запросов к API (`/health`, `/cards`, `/quiz`), чтобы в Loki появились записи.

### LogQL в Grafana

**Explore → Loki**, затем:

| Вид | LogQL |
|-----|-------|
| Таблица логов | `{app="ai-learning-flashcards-api"}` |
| Pie chart по уровню | `sum by (level) (count_over_time({app="ai-learning-flashcards-api"}[$__range]))` |

Для pie chart создайте панель типа **Pie chart** и укажите запрос выше (диапазон времени берётся из `$__range` дашборда).

На сервере откройте Grafana (`http://<IP>:3000`) → **Explore** → datasource **Loki** → вставьте запросы из таблицы. Диапазон времени: **Last 15 minutes** (или шире, если логи старые).

### Screenshots для сдачи ДЗ

Сохраните скриншоты (локально или на сервере):

1. **Терминал** — `docker network create flashcards-observability || true` и `docker compose up -d` в `monitoring/`, вывод `docker compose ps` (контейнеры `loki`, `grafana` в статусе running).
2. **Grafana → Explore → Loki** — таблица логов по запросу `{app="ai-learning-flashcards-api"}` с полями method, endpoint, status_code после нескольких `curl` к API.
3. **Grafana — Pie chart** — запрос `sum by (level) (count_over_time({app="ai-learning-flashcards-api"}[$__range]))`, видны уровни INFO и ERROR.
4. **Grafana → Connections → Data sources** — provisioned datasource **Loki** с URL `http://loki:3100`.
5. **Терминал** — `pytest -q` с результатом `passed` (локальная проверка перед коммитом).
6. *(Опционально)* **Терминал API** — при остановленном Loki в stdout видно `WARNING: Failed to send log to Loki: ...` (приложение не падает).

### Тесты

```bash
pip install -r requirements.txt
python -m compileall .
pytest -q
```

## Лицензия

Учебный проект для домашнего задания.
