"""Opt-in real Ollama harness check; Neo4j retrieval is replaced by one known passage."""

import os
import uuid

import pytest

from app import agent, config, graph, worker


@pytest.mark.skipif(os.getenv("RUN_OLLAMA_TESTS") != "1", reason="Requires local qwen3:8b and bge-m3")
def test_ollama_deepagents_grounded_answer(workspace, monkeypatch):
    settings = {
        **config.DEFAULTS,
        "LLM_PROVIDER": "ollama",
        "CHAT_MODEL": "qwen3:8b",
        "OLLAMA_CONTEXT_LENGTH": 8192,
        "EMBEDDING_PROVIDER": "ollama",
        "EMBEDDING_MODEL": "bge-m3",
        "EMBEDDING_DIMENSIONS": 1024,
    }
    monkeypatch.setattr(agent, "DATA", workspace)
    monkeypatch.setattr(agent, "model", lambda: config.model(settings))
    monkeypatch.setattr(agent, "embeddings", lambda: config.embeddings(settings))
    passage = {
        "id": "fixture-1",
        "text": "이 검증 자료에서 최소 재고 기준은 10개입니다.",
        "title": "검증 자료",
        "location": "본문",
    }
    monkeypatch.setattr(graph, "search", lambda *a, **kw: [passage])
    monkeypatch.setattr(graph, "passages", lambda *a, **kw: [passage])
    monkeypatch.setattr(graph, "relations", lambda *a, **kw: [])
    text, citations = agent.answer("자료에 따르면 최소 재고 기준은 몇 개입니까?", str(uuid.uuid4()), ["test"])
    assert "10" in text
    assert citations and citations[0]["id"] == "fixture-1"
    knowledge = (
        config.model(settings)
        .with_structured_output(worker.Knowledge, method="json_schema")
        .invoke("다음 본문을 한국어로 요약하고 명시된 관계만 추출하세요: 서울은 대한민국의 수도입니다.")
    )
    assert knowledge.summary and knowledge.relations
