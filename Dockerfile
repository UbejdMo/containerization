# Slim rather than alpine: psycopg[binary] ships manylinux wheels that need
# glibc, and alpine's musl would force a source build with a compiler toolchain
# in the image. Slim is the smaller image once that is accounted for.
FROM python:3.13-slim

# Keeps .pyc files out of the image, and makes logs appear in `docker compose
# logs` immediately instead of sitting in Python's stdout buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies are copied and installed before the source, so editing main.py
# reuses the cached install layer instead of re-downloading everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Not root. If the app is ever made to write to a mounted path, this is what
# stops it from doing so as uid 0 on the host.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

# --host 0.0.0.0, not the default 127.0.0.1: inside a container, localhost means
# the container itself, and a server bound there is unreachable from the host no
# matter what ports are published.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
