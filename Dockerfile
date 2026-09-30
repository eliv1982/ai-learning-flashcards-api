# Exact Python patch version on Debian 13, pinned by digest so rebuilds do not drift.
# Update the tag and the digest together (docker buildx imagetools inspect python:<tag>).
FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Unprivileged runtime user with a fixed UID/GID.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code stays root-owned and read-only for the runtime user.
COPY main.py .
COPY app/ ./app/
COPY data/ ./data/

USER app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
