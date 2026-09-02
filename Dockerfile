FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DISCLOSURE_CONFIG_DIR=/app/config
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY --chown=10001:10001 eval ./eval
RUN pip install --no-cache-dir ".[agent]"
RUN useradd --create-home --uid 10001 agent
USER agent
EXPOSE 8000
CMD ["disclosure-agent", "serve"]
