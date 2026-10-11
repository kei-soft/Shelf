import json

from fastapi.testclient import TestClient
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app import config, graph, main, settings_api, store, worker


def test_settings_secrets_preserved_and_redacted(workspace):
    with TestClient(main.app) as client:
        payload = {"OPENAI_API_KEY": "secret-api-test", "NEO4J_PASSWORD": "secret-password-test"}
        result = client.put("/api/settings", json=payload)
        assert result.status_code == 200
        assert "secret-api-test" not in result.text and "secret-password-test" not in result.text
        assert result.json()["settings"]["OPENAI_API_KEY_SET"]
        assert config.get("OPENAI_API_KEY") == payload["OPENAI_API_KEY"]
        assert client.get("/api/settings").json()["NEO4J_PASSWORD_SET"]
        client.put("/api/settings", json={"OPENAI_API_KEY": "", "NEO4J_PASSWORD": ""})
        assert config.get("NEO4J_PASSWORD") == payload["NEO4J_PASSWORD"]
        rejected = client.put("/api/settings", json={"NEO4J_PASSWORD": "secret\npassword"})
        assert rejected.status_code == 422
        assert "secret" not in rejected.text
        assert (
            client.put("/api/settings", json=payload, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )


def test_embedding_change_requeues_without_deleting_originals(workspace):
    sid = store.add_source("book", "txt", "original.txt")["id"]
    with store.connect() as db:
        db.execute("UPDATE sources SET status='ready',active_version='old' WHERE id=?", (sid,))
    with TestClient(main.app) as client:
        assert client.put("/api/settings", json={"CHAT_MODEL": "another-chat-model"}).json()["requeued"] == 0
        response = client.put(
            "/api/settings",
            json={
                "LLM_PROVIDER": "ollama",
                "CHAT_MODEL": "qwen3:8b",
                "EMBEDDING_PROVIDER": "ollama",
                "EMBEDDING_MODEL": "bge-m3",
                "EMBEDDING_DIMENSIONS": 1024,
            },
        )
        assert response.json()["requeued"] == 1
        assert store.source(sid)["status"] == "queued"
        assert store.source(sid)["active_version"] is None
        assert store.source(sid)["locator"] == "original.txt"
        assert graph.vector_index()[0] != "chunk_vector"
        assert client.put("/api/settings", json={"NEO4J_PASSWORD": "changed"}).json()["requeued"] == 0


def test_settings_cannot_change_mid_ingestion(workspace, analyze):
    analyze(store.add_source("book", "txt", "original.txt"))
    store.claim()
    with TestClient(main.app) as client:
        assert client.put("/api/settings", json={"CHAT_MODEL": "other"}).status_code == 409
        assert config.get("CHAT_MODEL") == "gpt-4.1-mini"


def test_ollama_factories_need_no_openai_key(workspace):
    s = {
        **config.DEFAULTS,
        "LLM_PROVIDER": "ollama",
        "EMBEDDING_PROVIDER": "ollama",
        "CHAT_MODEL": "qwen3:8b",
        "EMBEDDING_MODEL": "bge-m3",
        "OPENAI_API_KEY": "",
    }
    assert isinstance(config.model(s), ChatOllama)
    assert isinstance(config.embeddings(s), OllamaEmbeddings)


def test_check_reports_embedding_dimension_and_sanitizes_errors(workspace, monkeypatch):
    class Embedder:
        def embed_query(self, text):
            return [0.1] * 1024

    monkeypatch.setattr(config, "embeddings", lambda s: Embedder())
    result = settings_api.check(settings_api.CheckInput(target="embedding"))
    assert result["dimensions"] == 1024 and not result["ok"]

    def fail(s):
        raise RuntimeError("secret-api-test")

    monkeypatch.setattr(config, "embeddings", fail)
    result = settings_api.check(settings_api.CheckInput(target="embedding"))
    assert not result["ok"] and "secret-api-test" not in json.dumps(result)


def test_running_worker_reads_saved_settings(workspace):
    # A separate connection (as used by the worker) must see writes without restarting Python.
    before = config.embedding_profile()
    with store.connect() as db:
        db.execute("INSERT INTO settings VALUES(1,?)", (json.dumps({"EMBEDDING_PROVIDER": "ollama"}),))
    assert config.embedding_profile() != before


def test_driver_recreated_after_password_change(workspace, monkeypatch):
    created = []

    class Driver:
        closed = False

        def close(self):
            self.closed = True

    def factory(*args, **kwargs):
        driver = Driver()
        created.append(driver)
        return driver

    graph.close()
    monkeypatch.setattr(graph.GraphDatabase, "driver", factory)
    first = graph.driver()
    with store.connect() as db:
        db.execute("INSERT INTO settings VALUES(1,?)", (json.dumps({"NEO4J_PASSWORD": "new"}),))
    assert graph.driver() is not first
    assert first.closed and len(created) == 2
    graph.close()


def test_plain_text_agent_output_gets_validated_schema(workspace, monkeypatch):
    import uuid
    from types import SimpleNamespace

    from app import agent

    class Model:
        def with_structured_output(self, schema, method):
            assert method == "json_schema"
            return self

        def invoke(self, messages):
            return agent.Answer(paragraphs=[agent.Paragraph(text="검증된 설명", evidence_ids=["e1"])])

    class Harness:
        def invoke(self, *a, **kw):
            return {"messages": [SimpleNamespace(content="아직 검증하지 않은 초안")]}

    monkeypatch.setattr(agent, "DATA", workspace)
    monkeypatch.setattr(agent, "model", lambda: Model())
    monkeypatch.setattr(agent, "embeddings", lambda: SimpleNamespace(embed_query=lambda q: [0.1]))
    monkeypatch.setattr(agent, "create_deep_agent", lambda **kw: Harness())
    monkeypatch.setattr(graph, "search", lambda *a: [{"id": "e1", "text": "검증된 설명"}])
    result, citations = agent.answer("질문", str(uuid.uuid4()), ["s"])
    assert result == "검증된 설명 [1]" and citations[0]["id"] == "e1"


def test_extraction_asks_ollama_to_skip_its_thinking_pass(workspace, monkeypatch):
    """Reasoning dominated ingestion: 8.3s of an 11.2s extraction, then discarded."""
    built = {}

    class Fake:
        def __init__(self, **kwargs):
            built.update(kwargs)

    import langchain_ollama

    monkeypatch.setattr(langchain_ollama, "ChatOllama", Fake)
    values = {**config.DEFAULTS, "LLM_PROVIDER": "ollama", "CHAT_MODEL": "gemma4"}

    config.model(values)
    assert built["reasoning"] is None, "ordinary chat keeps whatever the model does by default"
    assert built["num_ctx"] == config.DEFAULTS["OLLAMA_CONTEXT_LENGTH"]

    config.model(values, num_ctx=worker.EXTRACT_CONTEXT, reasoning=False)
    assert built["reasoning"] is False and built["num_ctx"] == worker.EXTRACT_CONTEXT


def test_check_names_the_failure_without_leaking_the_secret(workspace, monkeypatch):
    """One catch-all sentence made 'out of credit' look like 'wrong address'."""

    class Quota(Exception):
        pass

    Quota.__name__ = "OpenAIRateLimitError"
    secret = "sk-proj-do-not-leak-this-value"

    def blow_up(*args, **kwargs):
        raise Quota(f"429 insufficient_quota for key {secret}")

    monkeypatch.setattr(config, "model", blow_up)
    with TestClient(main.app) as client:
        body = client.post("/api/settings/check", json={"target": "model"}).json()
    assert body["ok"] is False
    assert "OpenAIRateLimitError" in body["message"]
    assert "크레딧" in body["message"], "the class name must resolve to an actionable hint"
    assert secret not in body["message"], "provider messages never cross the wire"
