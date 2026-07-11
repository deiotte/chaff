FROM python:3.12-slim
WORKDIR /app

# Which optional extras to bake in. Default = API + Anthropic drafting, so the
# "Describe it in English" button works out of the box once you set a key.
# OpenAI/Google users rebuild with e.g. CHAFF_EXTRAS=api,nl-openai (see .env.example).
ARG CHAFF_EXTRAS=api,nl

COPY pyproject.toml README.md ./
COPY src/ src/
COPY api/ api/
COPY examples/ examples/
RUN pip install --no-cache-dir -e ".[${CHAFF_EXTRAS}]"
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
