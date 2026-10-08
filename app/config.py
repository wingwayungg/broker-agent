"""
Central config. Keeping provider selection here means swapping Cerebras for
Groq or Gemini (or anything else) later is a one-line change, not a refactor.
"""
import os
from functools import cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    llm_provider: str = os.getenv("LLM_PROVIDER", "cerebras")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    # EDGAR requires "Name contact@email" here; see app/sec_filings.py.
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "minishlab/potion-retrieval-32M")
    # MongoDB Atlas cluster holding the 10-K index (see app/rag.py). Unset
    # means search_filings reports itself unavailable; nothing else needs it.
    mongodb_uri: str = os.getenv("MONGODB_URI", "")
    mongodb_db: str = os.getenv("MONGODB_DB", "broker_agent")

    broker_host: str = os.getenv("BROKER_HOST", "127.0.0.1")
    broker_port: int = int(os.getenv("BROKER_PORT", "7497"))
    broker_client_id: int = int(os.getenv("BROKER_CLIENT_ID", "1"))

    use_mock_broker: bool = os.getenv("USE_MOCK_BROKER", "true").lower() == "true"


settings = Settings()


@cache
def get_llm():
    """
    Returns a LangChain-compatible chat model based on configured provider.
    Swap providers by changing LLM_PROVIDER in .env — nothing else in the
    app needs to know which one is active.

    Cached: the client is stateless and constructing one per call (four
    times per research_stock) only burns time and connection setup.
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
    elif settings.llm_provider == "cerebras":
        # Cerebras exposes an OpenAI-compatible API, so ChatOpenAI with a
        # base_url avoids langchain-cerebras (pins the old langchain-core 0.3).
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=settings.cerebras_api_key,
            base_url="https://api.cerebras.ai/v1",
            model=os.getenv("CEREBRAS_MODEL", "gpt-oss-120b"),
            temperature=0,
        )
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


@cache
def get_embedder():
    """
    The embedding model behind app/rag.py: anything with
    encode(list[str]) -> 2D numpy array. A local model2vec static model by
    default (why: see app/rag.py's docstring); EMBEDDING_MODEL picks another
    model2vec model from the Hugging Face hub.

    Cached and imported lazily: loading reads 65-130MB of weights (float16
    as baked by the Dockerfile, float32 from the hub), which only the first
    filings search should pay for.
    """
    from model2vec import StaticModel

    return StaticModel.from_pretrained(settings.embedding_model)
