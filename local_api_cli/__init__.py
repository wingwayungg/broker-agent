"""
The local API and the local CLI -- the two in-process ways a human drives
the agent. Named after what's in it rather than "local", because tests/ and
notebooks/ run locally too; what's specific here is the pair of entry
points. Nothing in here is served on Fly -- production is `langgraph dev`
(see langgraph.json), which uses `platform_graph` and mounts app/frontend.py.

Both modules go through `ask()` in app/agent.py, i.e. the MemorySaver-backed
`agent` graph that infers resume-vs-new-message from thread state. They
don't import each other; they're grouped because they share that path and
its dev-only dependencies (requirements-dev.txt). The folder is excluded
from the Docker image via .dockerignore.

    python -m local_api_cli.cli              # terminal chat
    uvicorn local_api_cli.api:app --reload   # FastAPI POST /ask
"""
