# MiR REST API emulator — for CI services and docker compose test stacks.
#
#   docker build -t mir-emulator .
#   docker run --rm -p 8080:8080 mir-emulator --mir-version 3.8.1
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/mir-emulator packages/mir-emulator
COPY packages/mir-spec-scraper/pyproject.toml packages/mir-spec-scraper/pyproject.toml
COPY packages/mir-spec-scraper/src/mir_spec_scraper/__init__.py packages/mir-spec-scraper/src/mir_spec_scraper/__init__.py
RUN uv build --package mir-emulator --out-dir /dist

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 emulator
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
USER emulator
EXPOSE 8080
ENTRYPOINT ["mir-emulator", "--host", "0.0.0.0"]
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/')"]
