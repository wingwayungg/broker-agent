FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the embedding model into the image so the first filings search after
# a cold start doesn't wait on a Hugging Face download. It's saved at the
# precision it will run at: model2vec loads the full float32 weights before
# quantizing, so quantizing at runtime still pays the float32 peak, which
# for the 32M model is ~85MB more server peak; float16 scores the same in
# evals/.
ARG EMBEDDING_MODEL=minishlab/potion-retrieval-32M
ARG EMBEDDING_DTYPE=float16
# The Hub download cache (always float32) is deleted once the copy is saved.
RUN python -c "from model2vec import StaticModel; \
    StaticModel.from_pretrained( \
    '${EMBEDDING_MODEL}', \
    quantize_to='${EMBEDDING_DTYPE}' \
    ).save_pretrained('/models/${EMBEDDING_MODEL}')" \
    && rm -rf /root/.cache/huggingface
# The path ends in the model name, so app/rag.py's per-model `variant`
# naming still applies.
ENV EMBEDDING_MODEL=/models/${EMBEDDING_MODEL} HF_HUB_OFFLINE=1

COPY . .

EXPOSE 2024

# This app is for demo only, its API is unauthenticated. Acceptable while the broker is mocked.
# If connecting to a real broker in the future,the app should
# 1. Add an "auth" block to langgraph.json
# 2. Or Or replace langgraph dev with uvicorn, where auth is handled
# Alternatively, run the project privately on your own machine, where the
# unauthenticated API is not exposed and no auth is needed.
#
# `langgraph dev` serves the whole app in two ways: the LangGraph API
# (threads, SSE streaming, interrupt/resume) plus the browser UI mounted via
# langgraph.json. Using it in prod keeps deployed and local identical, and
# avoids hand-writing /threads endpoints using uvicorn
#
# Switching to `langgraph build` would make Docker image depend on Postgres
# and Redis, which is far larger, and drives maintenance cost up both technically
# and financially -- which deviates from the goal of a quick demo.

CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser", "--no-reload"]
