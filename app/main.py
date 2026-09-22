"""
FastAPI wrapper around the agent. Kept deliberately thin — the graph in
app/agent.py has all the logic, this just exposes it over HTTP and gives
each caller a stable thread_id so multi-turn memory works per-session.
"""
from pydantic import BaseModel

from fastapi import FastAPI

from app.agent import ask

app = FastAPI(title="Broker Portfolio Agent")


class Question(BaseModel):
    question: str
    thread_id: str = "default"


class Answer(BaseModel):
    answer: str


@app.post("/ask")
def ask_endpoint(payload: Question) -> Answer:
    answer = ask(payload.question, thread_id=payload.thread_id)
    return Answer(answer=answer)

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
