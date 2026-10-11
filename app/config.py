import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)
DATA = Path(os.getenv("DATA_DIR") or dotenv_values(ENV_FILE).get("DATA_DIR") or "./data").resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "originals").mkdir(exist_ok=True)


DEFAULTS = {
    "LLM_PROVIDER": "openai",
    "CHAT_MODEL": "gpt-4.1-mini",
    "EMBEDDING_PROVIDER": "openai",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_DIMENSIONS": 1536,
    "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "OLLAMA_CONTEXT_LENGTH": 32768,
    "OPENAI_API_KEY": "",
    "NEO4J_URI": "bolt://127.0.0.1:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "",
    "NEO4J_DATABASE": "neo4j",
    "SOURCE_DATABASE_URL": "",
}
SECRETS = {"OPENAI_API_KEY", "NEO4J_PASSWORD", "SOURCE_DATABASE_URL"}


def settings():
    """Read persisted settings every time so the API and separate worker see the same values."""
    values = {**DEFAULTS, **dotenv_values(ENV_FILE), **os.environ}
    path = DATA / "app.sqlite"
    if path.exists():
        with closing(sqlite3.connect(path, timeout=30)) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'"
            ).fetchone()
            row = db.execute("SELECT value FROM settings WHERE id=1").fetchone() if exists else None
            if row:
                values.update(json.loads(row[0]))
    return values


def get(name, default=None):
    return settings().get(name, default)


def public_settings(values=None):
    values = values if values is not None else settings()
    return {
        **{key: values[key] for key in DEFAULTS if key not in SECRETS},
        **{key + "_SET": bool(values.get(key)) for key in SECRETS},
    }


def embedding_profile(values=None):
    s = values if values is not None else settings()
    parts = [s["EMBEDDING_PROVIDER"], s["EMBEDDING_MODEL"], str(s["EMBEDDING_DIMENSIONS"])]
    if s["EMBEDDING_PROVIDER"] == "ollama":
        parts.append(s["OLLAMA_BASE_URL"].rstrip("/"))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def model(values=None, num_ctx=None, reasoning=None):
    """Ollama-only overrides for mechanical callers; ignored for OpenAI.

    num_ctx: smaller window when the prompt is one short chunk (see worker.EXTRACT_CONTEXT).
    reasoning=False: skip the model's thinking pass. Measured on gemma4, one extraction spent
    8.3 of 11.2 seconds generating a 4,300-character thought that is then discarded.
    """
    s = values if values is not None else settings()
    if s["LLM_PROVIDER"] == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=s["CHAT_MODEL"],
            base_url=s["OLLAMA_BASE_URL"],
            num_ctx=int(num_ctx or s["OLLAMA_CONTEXT_LENGTH"]),
            reasoning=reasoning,
            temperature=0,
            client_kwargs={"timeout": 180},
            async_client_kwargs={"timeout": 180},
        )
    from langchain_openai import ChatOpenAI

    if not s["OPENAI_API_KEY"]:
        raise ValueError("설정에서 OpenAI API 키를 입력하거나 Ollama를 선택해주세요.")
    return ChatOpenAI(model=s["CHAT_MODEL"], api_key=s["OPENAI_API_KEY"], timeout=90, max_retries=2)


def embeddings(values=None):
    s = values if values is not None else settings()
    if s["EMBEDDING_PROVIDER"] == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=s["EMBEDDING_MODEL"],
            base_url=s["OLLAMA_BASE_URL"],
            client_kwargs={"timeout": 180},
            async_client_kwargs={"timeout": 180},
        )
    from langchain_openai import OpenAIEmbeddings

    if not s["OPENAI_API_KEY"]:
        raise ValueError("설정에서 OpenAI API 키를 입력하거나 임베딩 제공자를 Ollama로 바꿔주세요.")
    return OpenAIEmbeddings(
        model=s["EMBEDDING_MODEL"],
        api_key=s["OPENAI_API_KEY"],
        dimensions=int(s["EMBEDDING_DIMENSIONS"]),
    )
