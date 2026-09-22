FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 2024

# `langgraph dev` serves the whole app in one process: the LangGraph API
# (threads, SSE streaming, interrupt/resume) plus the browser UI mounted via
# langgraph.json. Using it in prod keeps deployed and local identical, and
# avoids hand-writing /threads endpoints that would have to stay in sync
# with app/static/app.js.
#
# It is a development server, so this is a deliberate trade-off: its API is
# unauthenticated (anyone with the URL can run the graph on our API keys)
# and langgraph-api is Elastic-2.0 licensed. Acceptable while the broker is
# mocked. Before wiring a real broker, add an "auth" block to langgraph.json
# or serve the graph from a small uvicorn app instead.
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "2024", "--no-browser", "--no-reload"]
