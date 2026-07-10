FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY api/ api/
COPY examples/ examples/
RUN pip install --no-cache-dir -e '.[api]'
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
