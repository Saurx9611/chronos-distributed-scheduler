FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.14-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend ./backend
RUN uv sync --locked --no-dev --no-editable
RUN useradd --create-home chronos
RUN mkdir /data && chown chronos:chronos /data
USER chronos
EXPOSE 8000
CMD [".venv/bin/uvicorn", "chronos.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
