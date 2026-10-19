"""Terminal chat loop: `python -m cli` from backend/."""

from cli.session import ask


def main():
    print(
        "Broker Portfolio Agent. Buy orders require two 'yes' confirmations. Type 'exit' to quit.\n"
    )
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
