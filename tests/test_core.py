import json
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint

from app import config, database, graph, ingest, main, store, worker
from app.agent import Answer, Paragraph, render


def test_public_url_blocks_internal_hosts(monkeypatch):
    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    for url in ["http://localhost", "http://169.254.169.254", "file:///tmp/x", "https://u:p@example.com"]:
        with pytest.raises(ValueError):
            ingest.public_url(url)


def test_rebinding_rejected_before_connection(monkeypatch):
    calls = 0

    def resolve(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8" if calls == 1 else "127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with pytest.raises(ValueError):
        ingest.fetch_url("https://example.com")


def test_chunks_keep_locations_and_tail():
    rows = ingest.chunks([("p.1", "재고 관리 " * 100), ("p.2", "마지막 페이지")], size=40, overlap=5)
    assert rows[-1]["location"] == "p.2"
    assert "마지막" in rows[-1]["text"]
    assert [r["ordinal"] for r in rows] == list(range(len(rows)))
    assert len(rows) > 2
    with pytest.raises(ValueError):
        ingest.chunks([("p.1", "")])


def test_citations_fail_closed():
    row = {"id": "real", "text": "근거"}
    answer = Answer(paragraphs=[Paragraph(text="설명", evidence_ids=["real", "real"])])
    text, citations = render(answer, {"real": row})
    assert text == "설명 [1]" and len(citations) == 1
    with pytest.raises(ValueError):
        render(answer, {})
    with pytest.raises(ValueError):
        render(Answer(paragraphs=[Paragraph(text="근거 없는 사실")]), {})
    assert render(Answer(paragraphs=[Paragraph(text="자료가 부족합니다", kind="limitation")]), {})[1] == []


def test_parameter_validation_rejects_injection_shape():
    database.validate_params({"threshold": "integer"}, {"threshold": 10})
    for bad in [{"threshold": "1; DROP TABLE x"}, {"threshold": True}, {"threshold": 1, "sql": "x"}]:
        with pytest.raises(ValueError):
            database.validate_params({"threshold": "integer"}, bad)


def test_queue_claim_and_failed_version_retention(workspace, analyze):
    source = analyze(store.add_source("book", "txt", "test.txt"))
    assert store.claim()["id"] == source["id"]
    assert store.claim() is None
    with store.connect() as db:
        db.execute("UPDATE sources SET active_version='old',status='failed' WHERE id=?", (source["id"],))
    assert store.source(source["id"])["active_version"] == "old"


def test_hybrid_search_filters_inside_the_index_and_fuses(monkeypatch):
    """Scope and version must bind inside the vector index, not after it.

    Post-filtering an ANN result set silently returned nothing once the corpus held sources the
    query vector did not point at (measured: 0 of 8 hits for a far source across 40 sources).
    """
    calls = []

    def run(statement, **params):
        calls.append((statement, params))
        if "s.active_version" in statement:
            return [{"id": "scope", "version": "v9", "title": "book"}]
        return [{"chunk": {"id": "same", "text": "body", "source_id": "scope"}, "score": 0.8}]

    monkeypatch.setattr(graph, "run", run)
    rows = graph.search('재고 + " : ?', [0.1, 0.2], ["scope"])
    assert len(rows) == 1 and rows[0]["score"] == pytest.approx(2 / 61)
    assert rows[0]["title"] == "book", "titles now come from the resolved sources, not the hit"

    vector = next(s for s, _ in calls if "SEARCH c IN" in s)
    assert "queryNodes" not in vector, "the deprecated procedure must be gone"
    assert "WHERE c.source_id IN $scope AND c.version IN $versions LIMIT" in vector, (
        "the filter belongs inside the SEARCH parentheses, where the index applies it"
    )
    assert all(p["scope"] == ["scope"] for _, p in calls)
    assert all(p["versions"] == ["v9"] for _, p in calls if "versions" in p)
    assert calls[-1][1]["query"] == "재고"


def test_search_skips_neo4j_when_no_source_is_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(graph, "run", lambda statement, **p: calls.append(statement) or [])
    assert graph.search("질문", [0.1], ["scope"]) == []
    assert len(calls) == 1, "nothing to search once no selected source has an active version"


def test_api_upload_origin_and_source_scope(workspace, monkeypatch):
    monkeypatch.setattr(graph, "delete", lambda sid: None)
    with TestClient(main.app) as client:
        assert client.post("/api/sources/upload", files={"file": ("a.exe", b"bad")}).status_code == 400
        assert (
            client.post(
                "/api/sources/upload",
                files={"file": ("a.txt", b"ok")},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        result = client.post("/api/sources/upload", files={"file": ("../../book.txt", "재고 자료".encode())})
        assert result.status_code == 201
        sid = result.json()["id"]
        assert store.source(sid)["locator"].startswith(str(workspace))
        assert "locator" not in client.get("/api/sources").json()[0]
        assert client.post("/api/chat", json={"question": "질문", "source_ids": [sid]}).status_code == 400
        with store.connect() as db:
            db.execute("UPDATE sources SET active_version='v',status='ready' WHERE id=?", (sid,))
            tid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO threads VALUES(?,?,?,?)", (tid, "test", json.dumps(["different"]), store.now())
            )
        assert (
            client.post(
                "/api/chat", json={"question": "질문", "source_ids": [sid], "thread_id": tid}
            ).status_code
            == 409
        )
        assert client.delete("/api/sources/" + sid).status_code == 200
        assert store.sources() == []


def test_worker_does_not_publish_on_extraction_failure(workspace, monkeypatch, analyze):
    path = workspace / "originals" / "book.txt"
    path.write_text("재고 정책", encoding="utf-8")
    analyze(store.add_source("book", "txt", str(path)))
    source = store.claim()

    class Embedder:
        model = "fake"

        def embed_documents(self, texts):
            return [[0.1] * 1536 for _ in texts]

    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self

        def batch(self, inputs, config=None):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(worker, "embeddings", lambda: Embedder())
    monkeypatch.setattr(worker, "model", lambda **kwargs: Model())
    monkeypatch.setattr(graph, "publish", lambda *args: pytest.fail("Partial version published"))
    with pytest.raises(RuntimeError):
        worker.process(source)
    assert store.source(source["id"])["active_version"] is None


def test_sse_chat_persists_validated_answer(workspace, monkeypatch):
    import app.agent

    monkeypatch.setattr(app.agent, "answer", lambda *args: ("근거 답변 [1]", [{"id": "c", "number": 1}]))
    source = store.add_source("book", "txt", "unused")
    with store.connect() as db:
        db.execute("UPDATE sources SET active_version='v',status='ready' WHERE id=?", (source["id"],))
    with TestClient(main.app) as client:
        response = client.post("/api/chat", json={"question": "질문", "source_ids": [source["id"]]})
        assert response.status_code == 200
        assert "event: status" in response.text and "event: answer" in response.text
        tid = client.get("/api/threads").json()[0]["id"]
        messages = client.get("/api/threads/" + tid).json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["evidence"][0]["id"] == "c"
        assert tid not in main.busy_threads


def test_real_deepagents_harness_with_fake_model(workspace, monkeypatch):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import app.agent as agent_module

    class ToolModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "test-tool-model"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "Answer",
                                    "args": {
                                        "paragraphs": [{"text": "재고 정책 설명", "evidence_ids": ["c"]}]
                                    },
                                    "id": "call_answer",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

    class Embedder:
        def embed_query(self, query):
            return [0.1, 0.2]

    monkeypatch.setattr(agent_module, "DATA", workspace)
    monkeypatch.setattr(agent_module, "model", lambda: ToolModel())
    monkeypatch.setattr(agent_module, "embeddings", lambda: Embedder())
    monkeypatch.setattr(graph, "search", lambda *a: [{"id": "c", "text": "재고 정책 설명"}])
    result, citations = agent_module.answer("질문", str(uuid.uuid4()), ["source"])
    assert result == "재고 정책 설명 [1]"
    assert citations[0]["id"] == "c"


def test_builtin_deepagents_tools_never_reach_the_model(workspace, monkeypatch):
    """deepagents injects a virtual filesystem and a `task` subagent this app never uses.

    They are inert against the default StateBackend, but they crowd tool choice for local
    models, and the subagent would read untrusted passages without this app's system prompt.
    """
    from langchain_ollama import ChatOllama

    import app.agent as agent_module

    seen = []

    def spy(self, tools, **kwargs):
        seen.extend(t.name if hasattr(t, "name") else t["name"] for t in tools)
        raise RuntimeError("captured the model request")

    monkeypatch.setattr(ChatOllama, "bind_tools", spy)
    monkeypatch.setattr(agent_module, "DATA", workspace)
    monkeypatch.setattr(agent_module, "model", lambda: ChatOllama(model="gemma4"))
    monkeypatch.setattr(agent_module, "embeddings", lambda: Embedder())
    monkeypatch.setattr(graph, "search", lambda *a: [{"id": "c", "text": "본문"}])
    with pytest.raises(Exception):
        agent_module.answer("질문", str(uuid.uuid4()), ["source"])

    assert seen, "the model was never asked for a completion"
    assert "search_materials" in seen and "Answer" in seen
    assert not set(seen) & set(agent_module.UNUSED_TOOLS), seen
    assert "task" not in seen, "the general-purpose subagent must stay disabled"


class Embedder:
    def embed_query(self, query):
        return [0.1, 0.2]


def test_analysis_is_user_started_stoppable_and_deletable(workspace, monkeypatch, analyze):
    """Registration must not analyse; a stopped run must become deletable and clear Neo4j."""
    deleted = []
    monkeypatch.setattr(graph, "delete", deleted.append)
    path = workspace / "originals" / "book.txt"
    path.write_text("재고 정책", encoding="utf-8")

    with TestClient(main.app) as client:
        sid = store.add_source("book", "txt", str(path))["id"]
        assert store.source(sid)["status"] == "draft"
        assert store.claim() is None, "a draft must never be picked up by the worker"

        assert client.post(f"/api/sources/{sid}/analyze").status_code == 200
        assert store.source(sid)["status"] == "queued"

        # Stopping before the worker claims it needs no worker handshake.
        assert client.post(f"/api/sources/{sid}/cancel").json()["status"] == "cancelled"
        assert store.claim() is None

        # Stopping a claimed run hands the row to the worker, which must finish the transition.
        client.post(f"/api/sources/{sid}/analyze")
        store.claim()
        assert client.post(f"/api/sources/{sid}/cancel").json()["status"] == "cancelling"
        assert client.delete(f"/api/sources/{sid}").status_code == 409, "still owned by the worker"

        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO versions VALUES('v1',?)", (sid,))
        with pytest.raises(worker.Cancelled):
            worker.ensure_active(sid)  # what stops the ingestion loop mid-run
        worker.mark_cancelled(sid)  # what the worker does once it has actually stopped
        assert store.source(sid)["status"] == "cancelled"

        assert client.delete(f"/api/sources/{sid}").status_code == 200
        assert deleted == [sid], "deleting a stopped source must still clear its Neo4j rows"
        assert store.source(sid) is None
        assert not path.exists()


def test_extraction_caps_are_enforced_outside_the_schema():
    """Length caps must not reach the model as `maxLength` but must still bind the result.

    llama.cpp expands `maxLength` into per-character grammar rules, which taxes every generated
    token; providers such as OpenAI ignore the keyword outright. Both are handled by clipping.
    """
    schema = json.dumps(worker.Knowledge.model_json_schema())
    assert "maxLength" not in schema, "string caps belong in Python, not in the decoding grammar"
    assert '"maxItems": 5' in schema, "the item count is what actually bounds generation"

    info = worker.Knowledge.model_validate(
        {
            "summary": "  " + "요" * 900 + "  ",
            "relations": [{"subject": "가" * 300, "predicate": "p", "object": "o"}] * 9,
        }
    )
    assert len(info.summary) == 600 and not info.summary.startswith(" ")
    assert len(info.relations) == 5
    assert len(info.relations[0].subject) == 100


def test_unparseable_chunk_is_indexed_without_a_summary(workspace, monkeypatch, analyze):
    """One chunk the model garbles must not discard the embeddings already paid for."""
    from langchain_core.exceptions import OutputParserException

    path = workspace / "originals" / "book.txt"
    path.write_text("재고 정책 " * 900, encoding="utf-8")
    analyze(store.add_source("book", "txt", str(path)))
    source = store.claim()

    class Embedder:
        model = "fake"

        def embed_documents(self, texts):
            return [[0.1] * 1536 for _ in texts]

    knowledge = worker.Knowledge(summary="요약", relations=[])
    retries = []

    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self

        def batch(self, prompts, config=None):
            raise OutputParserException("Invalid json output: * 항목")

        def invoke(self, prompt):
            retries.append(prompt)
            # The single retry rescues every chunk except the second one.
            if len(retries) == 2:
                raise OutputParserException("Invalid json output: * 항목")
            return knowledge

    published = {}
    monkeypatch.setattr(worker, "embeddings", lambda: Embedder())
    monkeypatch.setattr(worker, "model", lambda **kwargs: Model())
    monkeypatch.setattr(worker, "EXTRACT_CONCURRENCY", 1)
    monkeypatch.setattr(config, "get", lambda name, default=None: 1536)
    monkeypatch.setattr(graph, "publish", lambda s, v, chunks, a: published.update(chunks=chunks))

    worker.process(source)
    chunks = published["chunks"]
    assert len(chunks) > 2, "the whole document must still be published"
    assert all(c["embedding"] for c in chunks), "every chunk keeps its embedding"
    assert "summary" not in chunks[1], "the garbled chunk carries no summary"
    assert chunks[0]["summary"] == "요약" and chunks[2]["summary"] == "요약"


def test_render_survives_a_models_misplaced_ids_and_filler():
    """A grounded answer must not be thrown away over an intro line or a misfiled id."""
    from app.agent import salvage

    evidence = {"v1:3": {"id": "v1:3", "text": "코제트"}, "v1:9": {"id": "v1:9", "text": "미리엘"}}

    # The model writes the ids into the prose instead of the field; they are recovered, not invented.
    text, ids = salvage("장발장은 코제트를 구합니다. (evidence_ids: ['v1:3'])", evidence)
    assert ids == ["v1:3"] and "evidence_ids" not in text and "v1:3" not in text
    assert text == "장발장은 코제트를 구합니다."
    # Whatever wrapper the model used goes with the id: bare, Korean-labelled, or unclosed.
    for prose in [
        "장발장은 코제트를 구합니다. (v1:3, v1:9)",
        "장발장은 코제트를 구합니다. (근거: v1:3, v1:9)",
        "장발장은 코제트를 구합니다. (근거: v1:3, v1:9",
        "장발장은 코제트를 구합니다. [출처: v1:3, v1:9]",
    ]:
        text, ids = salvage(prose, evidence)
        assert ids == ["v1:3", "v1:9"], prose
        assert text == "장발장은 코제트를 구합니다.", prose
    # A whole line the model devoted to citing goes; the sentences around it stay.
    cited_line = '장발장은 코제트를 구합니다.\n- **증거**: evidence_ids ["", "1"]\n코제트는 성장합니다. v1:9'
    text, ids = salvage(cited_line, evidence)
    assert ids == ["v1:9"]
    assert text == "장발장은 코제트를 구합니다.\n코제트는 성장합니다."
    assert salvage("자베르는 추적합니다. (증거: 0, 1)", evidence)[0] == "자베르는 추적합니다."
    # The blank line the removed citation sat on goes with it.
    gapped = "첫 문단입니다.\n\n- 증거: [1]\n\n둘째 문단입니다. v1:3"
    assert salvage(gapped, evidence)[0] == "첫 문단입니다.\n\n둘째 문단입니다."

    # Ordinary parentheses and digits in prose must survive.
    text, _ = salvage("장발장(1815년)은 코제트를 구합니다. v1:3", evidence)
    assert text == "장발장(1815년)은 코제트를 구합니다."
    assert salvage("1832년 봉기는 (파리에서) 일어났습니다. v1:3", evidence)[0] == (
        "1832년 봉기는 (파리에서) 일어났습니다."
    )
    assert salvage("근거가 전혀 없는 문장", evidence) == ("근거가 전혀 없는 문장", [])

    answer = Answer(
        paragraphs=[
            Paragraph(text="다음과 같이 정리할 수 있습니다."),  # filler: nothing to cite
            Paragraph(text="장발장은 코제트를 구합니다. (evidence_ids: ['v1:3'])"),
            # Field filled *and* inlined: the prose must still come out clean.
            Paragraph(text="미리엘 주교가 등장합니다. (v1:9)", evidence_ids=["v1:9"]),
        ]
    )
    body, citations = render(answer, evidence)
    assert "정리할 수 있습니다" not in body, "the uncited filler paragraph is dropped"
    assert body == "장발장은 코제트를 구합니다. [1]\n\n미리엘 주교가 등장합니다. [2]"
    assert [c["id"] for c in citations] == ["v1:3", "v1:9"]

    # Nothing citable at all still fails closed.
    with pytest.raises(ValueError):
        render(Answer(paragraphs=[Paragraph(text="근거 없는 주장")]), evidence)
    with pytest.raises(ValueError):
        render(Answer(paragraphs=[Paragraph(text="x", evidence_ids=["made-up"])]), evidence)


def test_origin_check_follows_the_server_instead_of_a_hardcoded_port(workspace):
    """Pinning the API's own port broke the UI whenever the server moved, and it moves often:
    Windows reserves 8000 and 11434 into its dynamic exclusion range on some boots."""
    with TestClient(main.app, base_url="http://127.0.0.1:9999") as client:
        same = client.post("/api/chat", json={"question": "x"}, headers={"Origin": "http://127.0.0.1:9999"})
        assert same.status_code != 403, "the page's own origin must work on whatever port it got"

        dev = client.post("/api/chat", json={"question": "x"}, headers={"Origin": "http://localhost:5173"})
        assert dev.status_code != 403, "the Vite dev server proxies here and stays allowed"

        for bad in ["https://evil.example", "http://127.0.0.1:9998", "http://evil.localhost:9999"]:
            blocked = client.post("/api/chat", json={"question": "x"}, headers={"Origin": bad})
            assert blocked.status_code == 403, bad


def test_deleting_a_source_also_drops_the_agent_state_that_quoted_it(workspace, monkeypatch):
    """A checkpoint stores the passages the agent read, so a delete has to reach it too."""
    import json as _json

    from langgraph.checkpoint.sqlite import SqliteSaver

    monkeypatch.setattr(graph, "delete", lambda s: None)
    path = workspace / "originals" / "book.txt"
    path.write_text("재고 정책", encoding="utf-8")
    sid = store.add_source("book", "txt", str(path))["id"]
    scoped, untouched = str(uuid.uuid4()), str(uuid.uuid4())
    with store.connect() as db:
        db.execute("INSERT INTO threads VALUES(?,?,?,?)", (scoped, "t", _json.dumps([sid]), store.now()))
        db.execute("INSERT INTO threads VALUES(?,?,?,?)", (untouched, "t", _json.dumps([]), store.now()))

    def checkpoint(tid):
        with SqliteSaver.from_conn_string(str(workspace / "checkpoints.sqlite")) as saver:
            config = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
            saver.put(config, empty_checkpoint(), {"source": "input", "step": 0}, {})

    def stored(tid):
        with SqliteSaver.from_conn_string(str(workspace / "checkpoints.sqlite")) as saver:
            return len(list(saver.list({"configurable": {"thread_id": tid}})))

    checkpoint(scoped)
    checkpoint(untouched)
    assert stored(scoped) == 1 and stored(untouched) == 1

    with TestClient(main.app) as client:
        assert client.delete(f"/api/sources/{sid}").status_code == 200
    assert stored(scoped) == 0, "the source's passages must not survive in the agent state"
    assert stored(untouched) == 1, "unrelated conversations keep theirs"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 2, "history stays readable"
