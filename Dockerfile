# Pinned by digest, not by tag (ADR-0031). A tag is mutable: `python:3.12-slim`
# can point at different bytes tomorrow, so two builds of the same commit are
# not the same image. The digest makes the base an input we chose rather than
# one we inherited.
#
# A pin only stays safe if something bumps it — otherwise it freezes an
# unpatched base in place, which is worse than the mutable tag it replaced.
# .github/dependabot.yml is that something; do not pin anything here without it.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
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
