"""Local-only configuration. Secret values never leave the server after saving."""

import json
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from neo4j import GraphDatabase
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config, store

router = APIRouter(prefix="/api/settings")

# Matched as substrings of the exception class name, so provider prefixes (OpenAIRateLimitError,
# ollama's ResponseError) still land on the right hint.
FAILURES = {
    "RateLimit": "요청 한도를 넘었거나 크레딧이 소진되었습니다. 제공자의 결제·사용량 상태를 확인해주세요.",
    "Authentication": "인증이 거부되었습니다. API 키를 다시 확인해주세요.",
    "PermissionDenied": "이 키로는 해당 모델을 사용할 수 없습니다.",
    "NotFound": "모델 이름을 찾을 수 없습니다. 철자와 설치 여부를 확인해주세요.",
    "APIConnection": "제공자 서버에 연결하지 못했습니다. 네트워크와 주소를 확인해주세요.",
    "ConnectError": "서버에 연결하지 못했습니다. 주소와 실행 상태를 확인해주세요.",
    "Timeout": "응답 시간을 초과했습니다.",
    "AuthError": "Neo4j 인증에 실패했습니다. 사용자와 비밀번호를 확인해주세요.",
    "ServiceUnavailable": "Neo4j 서버에 연결하지 못했습니다.",
}


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    LLM_PROVIDER: Literal["openai", "ollama"] | None = None
    CHAT_MODEL: str | None = Field(default=None, min_length=1, max_length=200)
    EMBEDDING_PROVIDER: Literal["openai", "ollama"] | None = None
    EMBEDDING_MODEL: str | None = Field(default=None, min_length=1, max_length=200)
    EMBEDDING_DIMENSIONS: int | None = Field(default=None, ge=1, le=4096)
    OLLAMA_BASE_URL: str | None = Field(default=None, max_length=2000)
    OLLAMA_CONTEXT_LENGTH: int | None = Field(default=None, ge=4096, le=262144)
    OPENAI_API_KEY: str | None = Field(default=None, max_length=2000)
    NEO4J_URI: str | None = Field(default=None, max_length=2000)
    NEO4J_USER: str | None = Field(default=None, min_length=1, max_length=200)
    NEO4J_PASSWORD: str | None = Field(default=None, max_length=2000)
    NEO4J_DATABASE: str | None = Field(default=None, min_length=1, max_length=200)
    SOURCE_DATABASE_URL: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_values(self):
        for key, value in self.model_dump(exclude_none=True).items():
            if isinstance(value, str) and any(c in value for c in "\r\n\0"):
                raise ValueError("설정 값에는 줄바꿈을 입력할 수 없습니다.")
            if key in {"NEO4J_URI", "OLLAMA_BASE_URL"}:
                parts = urlsplit(value)
                schemes = (
                    {"http", "https"}
                    if key == "OLLAMA_BASE_URL"
                    else {"bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"}
                )
                if (
                    parts.scheme not in schemes
                    or not parts.hostname
                    or parts.username
                    or parts.password
                    or parts.query
                    or parts.fragment
                    or parts.path not in {"", "/"}
                ):
                    raise ValueError("접속 주소의 형식을 확인해주세요. 사용자/비밀번호는 별도 입력합니다.")
            if key == "SOURCE_DATABASE_URL" and value and not value.startswith("postgresql+psycopg://"):
                raise ValueError("PostgreSQL 주소는 postgresql+psycopg://로 시작해야 합니다.")
        return self

    def changes(self):
        return {
            k: v for k, v in self.model_dump(exclude_none=True).items() if not (k in config.SECRETS and not v)
        }


@router.get("")
def read():
    return config.public_settings()


@router.put("")
async def save(body: SettingsInput):
    from app.main import busy_threads

    if busy_threads:
        raise HTTPException(409, "답변 생성이 끝난 뒤 설정을 저장해주세요.")
    changes = body.changes()
    before = config.settings()
    after = {**before, **changes}
    reindex = config.embedding_profile(before) != config.embedding_profile(after) or any(
        before[k] != after[k] for k in ["NEO4J_URI", "NEO4J_DATABASE"]
    )
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM sources WHERE status='processing'").fetchone():
            raise HTTPException(409, "자료 분석이 끝난 뒤 설정을 저장해주세요.")
        row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        persisted = json.loads(row[0]) if row else {}
        persisted.update(changes)
        db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json.dumps(persisted),))
        count = 0
        if reindex:
            # Never analysed sources stay in draft; the user starts those themselves.
            count = db.execute(
                "UPDATE sources SET status='queued',active_version=NULL,error=NULL "
                "WHERE status IN ('ready','failed','cancelled')"
            ).rowcount
    return {"settings": config.public_settings(after), "requeued": count}


class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["ollama", "model", "embedding", "neo4j", "postgresql"]
    settings: SettingsInput = Field(default_factory=SettingsInput)


class Probe(BaseModel):
    ok: bool


@router.post("/check")
def check(body: CheckInput):
    s = {**config.settings(), **body.settings.changes()}
    try:
        if body.target == "ollama":
            with httpx.Client(timeout=5, trust_env=False) as client:
                result = client.get(s["OLLAMA_BASE_URL"].rstrip("/") + "/api/tags")
                result.raise_for_status()
                names = [m["name"] for m in result.json()["models"]]
            return {"ok": True, "message": f"Ollama 연결됨 · 설치 모델 {len(names)}개", "models": names}
        if body.target == "model":
            # Exercises tool calling, which Deep Agents requires, rather than merely checking a port.
            llm = config.model(s)
            result = llm.with_structured_output(Probe, method="function_calling").invoke(
                "Call the structured output tool with ok=true. This is a connection test."
            )
            if not result or not result.ok:
                raise ValueError("모델이 도구 호출 응답을 반환하지 않았습니다.")
            return {"ok": True, "message": "채팅 모델 연결 및 도구 호출 확인 완료"}
        if body.target == "embedding":
            vector = config.embeddings(s).embed_query("연결 확인")
            dims = len(vector)
            return {
                "ok": dims == int(s["EMBEDDING_DIMENSIONS"]),
                "dimensions": dims,
                "message": f"임베딩 출력 {dims}차원"
                + (" · 차원 설정을 맞춰주세요." if dims != int(s["EMBEDDING_DIMENSIONS"]) else " · 정상"),
            }
        if body.target == "neo4j":
            with GraphDatabase.driver(
                s["NEO4J_URI"],
                auth=(s["NEO4J_USER"], s["NEO4J_PASSWORD"]),
                connection_timeout=5,
                max_transaction_retry_time=0,
            ) as driver:
                driver.execute_query("RETURN 1 AS ok", database_=s["NEO4J_DATABASE"])
            return {"ok": True, "message": "Neo4j 인증 및 데이터베이스 연결 확인 완료"}
        from sqlalchemy import create_engine

        if not s["SOURCE_DATABASE_URL"].startswith("postgresql+psycopg://"):
            return {"ok": False, "message": "PostgreSQL 접속 주소를 입력해주세요."}
        db = create_engine(s["SOURCE_DATABASE_URL"], connect_args={"connect_timeout": 5})
        try:
            with db.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SELECT 1")
        finally:
            db.dispose()
        return {"ok": True, "message": "PostgreSQL 읽기 전용 연결 확인 완료"}
    except Exception as exc:
        # Driver/provider error *messages* may carry keys or connection URLs, so only the exception
        # class crosses the wire. That is still enough to separate "out of credit" from "wrong
        # address", which one catch-all sentence never was.
        name = type(exc).__name__
        hint = next((text for key, text in FAILURES.items() if key in name), "")
        return {
            "ok": False,
            "message": f"연결 확인 실패({name}): "
            + (hint or "주소·인증 정보·모델 설치 및 도구 호출 지원 여부를 확인해주세요."),
        }
