FROM python:3.12-slim
WORKDIR /app

COPY pyproject.toml ./
COPY server.py auth.py ./
COPY open-api-definitions ./open-api-definitions

RUN pip install --no-cache-dir . \
    && useradd --create-home --shell /usr/sbin/nologin mcp
USER mcp

EXPOSE 8000
CMD ["fivetran-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
