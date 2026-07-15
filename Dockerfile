FROM python:3.12-slim
WORKDIR /app

# Which optional extras to bake in. Default = API + streaming sinks (so the
# Stream tab can push to Kafka/MQTT/HTTP out of the box) + Anthropic/OpenAI
# drafting, so a key pasted in the UI (Claude or GPT) works too.
# Add Google with CHAFF_EXTRAS=api,streaming,nl,nl-openai,nl-google (.env.example).
ARG CHAFF_EXTRAS=api,streaming,nl,nl-openai

COPY pyproject.toml README.md ./
COPY src/ src/
COPY api/ api/
COPY examples/ examples/
RUN pip install --no-cache-dir -e ".[${CHAFF_EXTRAS}]"
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
