# The Next.js standalone server, built here. Only its output and the node binary
# reach the final image; no node_modules install or build tooling ships.
FROM node:24-slim AS web
RUN corepack enable
# Corepack picks the pnpm version from the package.json in the working
# directory (not pnpm's -C), so run from frontend/.
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN NEXT_TELEMETRY_DISABLED=1 pnpm build

FROM python:3.14-slim

WORKDIR /app

COPY backend/requirements.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Huggingface cache does not persist across a stop and start on Fly.io
# which means the embedding model has to be downloaded across a server restart
# and pays the float32 memory peak at runtime
# To solve it, pre-download it during build so it includes in the image
# and is served quickly when needed (i.e. without waiting on a Hugging Face download)
ARG EMBEDDING_MODEL=minishlab/potion-retrieval-32M
ARG EMBEDDING_DTYPE=float16
# Delete Hub cache to reduce image size since the quantized float16 copy will be used instead.
RUN python -c "from model2vec import StaticModel; \
    StaticModel.from_pretrained( \
    '${EMBEDDING_MODEL}', \
    quantize_to='${EMBEDDING_DTYPE}' \
    ).save_pretrained('/models/${EMBEDDING_MODEL}')" \
    && rm -rf /root/.cache/huggingface
ENV EMBEDDING_MODEL=/models/${EMBEDDING_MODEL} HF_HUB_OFFLINE=1

COPY --from=web /usr/local/bin/node /usr/local/bin/node
RUN node --version

COPY backend/ backend/
COPY --from=web /src/frontend/.next/standalone frontend/
COPY --from=web /src/frontend/.next/static frontend/.next/static
COPY scripts/start.sh scripts/

# Next.js serves the public port and proxies /api to the agent API on localhost.
EXPOSE 3000
CMD ["scripts/start.sh"]
