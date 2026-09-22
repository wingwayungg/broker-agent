"""
Quick terminal chat loop — the fastest way to demo this live in an
interview without standing up the FastAPI server first.

Run with `python -m local_api_cli.cli` from the repo root (not `python
local_api_cli/cli.py`, which would put local_api_cli/ on sys.path instead of
the root and break `import app`).
"""
from app.agent import ask


def main():
    print("Broker Portfolio Agent. Buy orders require two 'yes' confirmations. Type 'exit' to quit.\n")
    thread_id = "cli-session"
    while True:
        question = input("> ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue
        answer = ask(question, thread_id=thread_id)
        print(answer + "\n")


if __name__ == "__main__":
    main()
