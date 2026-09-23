FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
