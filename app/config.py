"""
Central config. Keeping provider selection here means swapping Groq for
Gemini (or anything else) later is a one-line change, not a refactor.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    ibkr_host: str = os.getenv("IBKR_HOST", "127.0.0.1")
    ibkr_port: int = int(os.getenv("IBKR_PORT", "7497"))
    ibkr_client_id: int = int(os.getenv("IBKR_CLIENT_ID", "1"))

    use_mock_ibkr: bool = os.getenv("USE_MOCK_IBKR", "true").lower() == "true"


settings = Settings()


def get_llm():
    """
    Returns a LangChain-compatible chat model based on configured provider.
    Swap providers by changing LLM_PROVIDER in .env — nothing else in the
    app needs to know which one is active.
    """
    if settings.llm_provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            api_key=settings.groq_api_key,
            model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=0,
        )
    elif settings.llm_provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            google_api_key=settings.gemini_api_key,
            model="gemini-1.5-flash",
            temperature=0,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
