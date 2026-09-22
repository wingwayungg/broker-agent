"""
Local, in-process ways to run the agent. Nothing in here is served on
Render -- production is `langgraph dev` (see langgraph.json), which uses
`platform_graph` and mounts app/frontend.py.

Both modules go through `ask()` in app/agent.py, i.e. the MemorySaver-backed
`agent` graph that infers resume-vs-new-message from thread state. They
don't import each other; they're grouped because they share that path and
its dev-only dependencies (requirements-dev.txt). The folder is excluded
from the Docker image via .dockerignore.

    python -m local.cli              # terminal chat
    uvicorn local.api:app --reload   # FastAPI POST /ask
"""
