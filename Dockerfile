# One image, two Cloud Run services: the API and the worker differ only in their command.
FROM python:3.12-slim

# MALLOC_ARENA_MAX caps per-thread malloc arenas; parallel embedding threads otherwise ratchet RSS up.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PLIMSOLL_ROOT=/app \
    MALLOC_ARENA_MAX=2 \
    TREE_SITTER_LANGUAGE_PACK_CACHE_DIR=/app/.tree-sitter

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY rubric ./rubric
COPY config ./config

# The grammar cache creates owner-only (0700) directories, so the grammars must be fetched by the
# same user that runs the app — fetched as root, the app user could not read them.
RUN useradd --uid 10001 --no-create-home plimsoll \
 && mkdir -p /app/.tree-sitter && chown plimsoll /app/.tree-sitter
USER plimsoll

# Fetch every supported grammar at build time so cold starts never download parsers. The build
# fails here if any grammar cannot be loaded.
RUN python -c "from sar_plimsoll.review.chunking import parser_health as h; r = h(); bad = {k: v for k, v in r.items() if v != 'ok'}; assert not bad, bad; print('grammars ok:', len(r))"

ENV PORT=8080
CMD ["sh", "-c", "exec uvicorn sar_plimsoll.api.app:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
