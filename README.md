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

Требуется Python 3.12 (та же версия, что в Docker-образе и CI; закреплённые зависимости на 3.10 не устанавливаются).

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

Контейнер работает от непривилегированного пользователя (UID 10001); в образ попадают только зависимости из `requirements.txt`.

## GitHub Actions (CI/CD)

Файл workflow: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml).

**Триггеры:** `pull_request` и `push` в `main`. Цепочка jobs: **`test` → `build` → `deploy`**. Каждый следующий job запускается только после успешного предыдущего. `deploy` работает только для `push` в `main`; pull request доходит максимум до smoke-теста образа.

Права токена заданы на уровне job: `test` — `contents: read`; `build` — `contents: read` и `packages: write`; `deploy` — `contents: read` (проверка, что коммит всё ещё на вершине `main`) и `packages: read`.

### Job `test` — обязательный gate

Python **3.12** (та же minor-версия, что в Docker-образе), `pip install -r requirements-dev.txt`, `pip check`, `pytest -q`. Пока тесты красные, образ не собирается, не публикуется и не деплоится. Внешние сервисы (Loki) не нужны.

### Job `build`

Образ собирается **один раз**. Именно этот образ проходит smoke-тест и затем (только при `push` в `main`) публикуется в GHCR — без повторной сборки. Smoke-тест проверяет: в образе нет pytest/httpx; контейнер стартует при недоступном Loki; `/health` и `/quiz` отвечают; процесс работает не от root. Если проверка не пройдена, публикации нет; тестовый контейнер удаляется в любом случае.

Теги в `ghcr.io/<владелец>/<репозиторий>`:

| Тег | Назначение |
|-----|-----------|
| `sha-<полный SHA коммита>` | неизменяемый; деплой использует именно его, дополнительно закреплённый по digest (`…:sha-<SHA>@sha256:…`) |
| `latest` | только для ручного `docker pull`; деплой его **никогда** не использует |

### Job `deploy`

- Образ берётся из результата `build` **того же запуска**, поэтому запуск N не может выкатить образ запуска N+1.
- Concurrency-группа `production-deploy`: одновременно идёт один деплой. Уже выполняющийся деплой не отменяется, следующий ждёт очереди.
- **Защита от устаревшего деплоя.** Группа выстраивает деплои в очередь, но не упорядочивает их по коммитам: медленная сборка старого коммита может дойти до `deploy` уже после того, как новый коммит выкатили. Поэтому первый шаг job (он выполняется уже внутри группы, до любого SSH) запрашивает через GitHub API коммит на вершине `main`. Если он не равен `GITHUB_SHA` запуска, деплой **штатно пропускается**: job остаётся успешным, в логе и summary есть сообщение `Stale deployment skipped`, к серверу подключения не было, откатывать нечего. Если вершину `main` определить не удалось, job падает (fail closed). Следствие: повторный запуск (re-run) деплоя старого коммита тоже пропускается — к старой версии возвращайтесь ручным rollback (ниже).
- Перед SSH выполняется проверка входных данных (наличие и формат; значения не печатаются). Если не хватает секрета, job падает с понятным сообщением.
- Подключение — [**appleboy/ssh-action**](https://github.com/appleboy/ssh-action), закреплённый на commit SHA релиза v1.2.0. Action проверяет host key сервера по `SSH_FINGERPRINT`.

**Секреты репозитория** (значения в git не хранятся): `SSH_HOST`, `SSH_PORT`, `SSH_USERNAME`, `SSH_KEY` (приватный ключ), `SSH_FINGERPRINT` (отпечаток host key сервера в формате `SHA256:…`, как выводит `ssh-keygen -l`).

> **Какой отпечаток нужен.** `SSH_FINGERPRINT` должен совпадать с отпечатком того host key (или host-сертификата), который сервер **реально предъявляет** клиенту action при согласовании SSH-соединения, с учётом эффективной конфигурации `sshd`. Наличие файла `/etc/ssh/ssh_host_*_key.pub` этого не доказывает: ключ может быть отключён в `sshd_config` (`HostKey`, `HostKeyAlgorithms`), а `HostCertificate` меняет то, что именно предъявляется (у сертификата свой отпечаток, он может отличаться от отпечатка ключа из `.pub`-файла). Если сервер предлагает несколько ключей, выбор между ними делает клиент; его обычный порядок предпочтения — лишь пояснение, а не правило выбора отпечатка.
>
> Получайте отпечаток **только по доверенному каналу** — через консоль хостинг-провайдера или уже доверенную SSH-сессию на сервер, — а не по сети, в которой вы видите его впервые:
>
> 1. На сервере посмотрите эффективную конфигурацию: `sudo sshd -T | grep -Ei '^(hostkey|hostcertificate|hostkeyalgorithms) '` — какие ключи, сертификаты и алгоритмы включены на самом деле.
> 2. Там же снимите то, что сервер предъявляет на своём SSH-порту: `ssh-keyscan -p <порт> 127.0.0.1 | ssh-keygen -lf -`. Каждая строка — предъявляемый ключ и его отпечаток `SHA256:…`. Если вы снимаете его с другой машины (`ssh-keyscan -p <порт> <хост>`), используйте значение только после сверки с доверенным каналом выше.
> 3. Если предъявляется один ключ, это и есть нужное значение. Если несколько, значение не должно зависеть от выбора клиента: оставьте серверу один host key (`HostKey` / `HostKeyAlgorithms`, проверка `sudo sshd -t`, затем перечитайте конфигурацию `sshd`) и возьмите отпечаток именно его.
> 4. Не подставляйте отпечаток, впервые увиденный по недоверенной сети, и не копируйте значение из чужого вывода или сообщения об ошибке: само по себе оно ничего не доказывает.
>
> Если закреплённый отпечаток не совпадает с согласованным ключом, action завершается с `host key fingerprint mismatch` ещё на этапе SSH-рукопожатия — скрипт на сервере не запускается. В этом случае повторите шаги выше, а не «подгоняйте» значение.

Скрипт на сервере выполняется в Bash со строгим режимом (`set -euo pipefail`):

1. `docker login ghcr.io` токеном job во **временном** `DOCKER_CONFIG` (токен не остаётся в `~/.docker` пользователя) и `docker pull` точного образа. Если образ не скачался, работающий сервис не затрагивается;
2. сеть `flashcards-observability` создаётся только если её ещё нет;
3. текущий контейнер `ai-learning-flashcards-api` останавливается и переименовывается в `ai-learning-flashcards-api-previous`. Он не удаляется; предыдущий rollback-контейнер при этом заменяется, поэтому хранится не больше одного;
4. запускается новый контейнер `ai-learning-flashcards-api` (`--restart unless-stopped`, сеть `flashcards-observability`, `APP_NAME`, `LOKI_URL`, порт `8010:8000`);
5. до 30 секунд опрашивается `http://127.0.0.1:8010/health`. Деплой успешен только после ответа `{"status":"ok"}`.

**Автоматический rollback.** Если новый контейнер не стартует, перезапускается в цикле или не проходит проверку `/health`, скрипт показывает его логи, удаляет его, возвращает `ai-learning-flashcards-api-previous` под прежним именем, запускает и проверяет `/health`. Job при этом завершается **ошибкой**. При первом деплое, когда предыдущего контейнера нет, просто удаляется неудавшийся контейнер. Что восстанавливать, rollback определяет по контейнерам, которые реально существуют на сервере, а не по внутренним флагам скрипта, поэтому он срабатывает и при HUP/TERM (обрыв SSH, отмена запуска) в любой момент, в том числе сразу после переименования старого контейнера.

**Ручной rollback** (на сервере, вернуться к предыдущему релизу):

```bash
docker rm -f ai-learning-flashcards-api
docker rename ai-learning-flashcards-api-previous ai-learning-flashcards-api
docker update --restart unless-stopped ai-learning-flashcards-api
docker start ai-learning-flashcards-api
curl http://127.0.0.1:8010/health
```

Если `-previous` уже нет, запустите нужный релиз по его неизменяемому тегу: `docker pull ghcr.io/<владелец>/<репозиторий>:sha-<полный SHA>`, затем `docker run` с теми же параметрами, что в скрипте деплоя.

После деплоя Swagger/OpenAPI доступен по адресу **[http://85.198.69.132:8010/docs](http://85.198.69.132:8010/docs)**.

API-контейнер отправляет логи в Loki по адресу `http://loki:3100/loki/api/v1/push` (имя хоста `loki` доступно только внутри сети `flashcards-observability`).

В README не указываются приватные ключи, токены и значения секретов — их нужно настроить только в настройках репозитория на GitHub.

## Monitoring with Loki and Grafana

Приложение отправляет логи напрямую в **Loki** через HTTP `POST /loki/api/v1/push` (без Promtail). Каждый HTTP-запрос логируется middleware; в теле сообщения (JSON) пишутся `method`, `route` (шаблон маршрута, например `/cards/{card_id}`, либо `unmatched`), `status_code`, `duration_ms`. Уровень: `5xx` — `ERROR` (в том числе необработанные исключения), `4xx` — `WARNING`, остальные — `INFO`. Labels в Loki только `app`, `level`, `method`; остальные значения в labels не выносятся, чтобы не раздувать кардинальность (для разбора используйте `| json`). При старте приложения отправляется отдельная запись `{"event": "application_started"}`.

### Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `LOKI_URL` | `http://localhost:3100/loki/api/v1/push` | URL push API Loki |
| `APP_NAME` | `ai-learning-flashcards-api` | Label `app` в Loki |

Отправка не блокирует обработку запросов: middleware только кладёт событие в ограниченную очередь (1000 событий), а один фоновый поток отправляет их в Loki с таймаутом `(connect 1 с, read 2 с)`. Если Loki недоступен или не отвечает, приложение **не падает и не замедляется** — при переполнении очереди события отбрасываются, а в stdout не чаще раза в минуту выводится предупреждение `WARNING: Failed to send log to Loki: ...`.

### Запуск monitoring stack (локально)

Сначала создайте общую Docker-сеть (один раз; повторная команда безопасна):

```bash
docker network create flashcards-observability || true
```

Пароль администратора Grafana задаётся через переменную окружения `GRAFANA_ADMIN_PASSWORD`. Значения по умолчанию нет: если переменная не задана или пуста, `docker compose` завершается с ошибкой. Реальный пароль не хранится в репозитории — скопируйте шаблон и впишите свой пароль (файл `monitoring/.env` игнорируется git, не используйте `admin`):

```bash
cd monitoring
cp .env.example .env          # Windows: copy .env.example .env
# откройте .env и задайте GRAFANA_ADMIN_PASSWORD
docker compose up -d
```

Docker Compose автоматически читает `monitoring/.env` при любой команде `docker compose` из этого каталога (включая `down` и `ps`).

- **Grafana:** [http://localhost:3000](http://localhost:3000) — логин `admin`, пароль из `GRAFANA_ADMIN_PASSWORD`
- **Loki** на хосте: `http://localhost:3100`
- **Loki** внутри Docker-сети `flashcards-observability`: `http://loki:3100`

Порты Grafana (`3000`) и Loki (`3100`) опубликованы **только на `127.0.0.1`** хоста, поэтому с других машин они недоступны. API-контейнер отправляет логи в Loki по Docker-сети (`http://loki:3100`), внешний порт для этого не нужен.

Data Source Loki подключается автоматически через `monitoring/grafana/provisioning/datasources/datasources.yml`.

Оба сервиса (`loki`, `grafana`) и API-контейнер на сервере используют одну сеть **`flashcards-observability`** — см. `monitoring/docker-compose.yml` и job `deploy` в workflow.

#### Хранение данных, перезапуск и retention

- Данные хранятся в именованных Docker-томах: `monitoring_grafana-data` (`/var/lib/grafana`) и `monitoring_loki-data` (`/loki`). Они переживают `docker compose down` и `docker compose up -d`. Команда `docker compose down -v` удаляет тома вместе с данными.
- Для обоих сервисов задан `restart: unless-stopped`: после перезапуска хоста или Docker они поднимаются сами (если не были остановлены вручную).
- Loki хранит логи **7 дней** (`limits_config.retention_period: 168h` и `compactor.retention_enabled: true` в `monitoring/loki-config.yml`), более старые данные удаляются компактором.
- `GRAFANA_ADMIN_PASSWORD` применяется только при первой инициализации тома Grafana. Чтобы сменить пароль позже, выполните `docker compose exec grafana grafana cli admin reset-admin-password '<новый пароль>'`.
- Если monitoring stack уже запускался по старому `docker-compose.yml` (без томов), логи и настройки Grafana лежали внутри контейнеров и при пересоздании не сохранятся.

### Server deployment: monitoring stack

На сервере (один раз, до или после первого деплоя API) разверните Loki и Grafana из репозитория:

```bash
git clone <URL-репозитория>   # или обновите уже клонированный каталог
cd ai-learning-flashcards-api
docker network create flashcards-observability || true
cd monitoring
cp .env.example .env
nano .env                     # задайте GRAFANA_ADMIN_PASSWORD (не используйте admin)
docker compose up -d
docker compose ps
```

Проверка:

- Grafana и Loki на сервере слушают только `127.0.0.1`, поэтому `http://<IP-сервера>:3000` снаружи недоступен. Порт `3000` в firewall открывать не нужно.
- Доступ к Grafana со своего компьютера — через SSH-туннель:

  ```bash
  ssh -L 3000:127.0.0.1:3000 <пользователь>@<IP-сервера>
  ```

  Пока туннель открыт, Grafana доступна на [http://localhost:3000](http://localhost:3000) (логин `admin`, пароль из `monitoring/.env` на сервере).
- Loki push из контейнера API: `http://loki:3100/loki/api/v1/push` (сеть `flashcards-observability`)

После `push` в `main` GitHub Actions пересоздаёт API-контейнер уже в этой сети с нужными `-e LOKI_URL=...`. Убедитесь, что monitoring stack запущен **до** генерации трафика к API.

Сгенерируйте логи на сервере:

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/cards
curl http://127.0.0.1:8010/cards/999
```

Последний запрос даст **404** и запись с `level="WARNING"` в Loki (`ERROR` — только для ответов `5xx`).

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

Откройте Grafana (локально — `http://localhost:3000`, на сервере — через SSH-туннель, см. выше) → **Explore** → datasource **Loki** → вставьте запросы из таблицы. Диапазон времени: **Last 15 minutes** (или шире, если логи старые).

### Screenshots для сдачи ДЗ

Сохраните скриншоты (локально или на сервере):

1. **Терминал** — `docker network create flashcards-observability || true` и `docker compose up -d` в `monitoring/`, вывод `docker compose ps` (контейнеры `loki`, `grafana` в статусе running).
2. **Grafana → Explore → Loki** — таблица логов по запросу `{app="ai-learning-flashcards-api"} | json` с полями method, route, status_code после нескольких `curl` к API.
3. **Grafana — Pie chart** — запрос `sum by (level) (count_over_time({app="ai-learning-flashcards-api"}[$__range]))`, видны уровни INFO и WARNING (например, после запроса `/cards/999`).
4. **Grafana → Connections → Data sources** — provisioned datasource **Loki** с URL `http://loki:3100`.
5. **Терминал** — `pytest -q` с результатом `passed` (локальная проверка перед коммитом).
6. *(Опционально)* **Терминал API** — при остановленном Loki в stdout видно `WARNING: Failed to send log to Loki: ...` (приложение не падает и не замедляется).

### Тесты

`requirements.txt` — зависимости приложения (все версии закреплены), `requirements-dev.txt` — то же плюс pytest и httpx.

```bash
pip install -r requirements-dev.txt
python -m compileall .
pytest -q
```

## Лицензия

Учебный проект для домашнего задания.
