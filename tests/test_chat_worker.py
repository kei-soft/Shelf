import asyncio
import sqlite3
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent, graph, main, store, worker_service


def test_general_chat_without_sources_never_uses_retrieval(workspace, monkeypatch):
    calls = []

    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="안녕하세요! 무엇을 도와드릴까요?")

    monkeypatch.setattr(agent, "model", lambda: Model())
    monkeypatch.setattr(agent, "embeddings", lambda: pytest.fail("General chat requested embeddings"))
    monkeypatch.setattr(graph, "search", lambda *a, **kw: pytest.fail("General chat requested Neo4j"))
    with TestClient(main.app) as client:
        response = client.post("/api/chat", json={"question": "안녕"})
        assert response.status_code == 200
        assert '"mode": "general"' in response.text and '"evidence": []' in response.text
        tid = client.get("/api/threads").json()[0]["id"]
        response = client.post(
            "/api/chat", json={"question": "내가 방금 뭐라고 했지?", "source_ids": [], "thread_id": tid}
        )
        assert "event: answer" in response.text
        assert ("user", "안녕") in calls[-1]
        assert ("assistant", "안녕하세요! 무엇을 도와드릴까요?") in calls[-1]


def test_general_chat_does_not_invent_citations(workspace, monkeypatch):
    monkeypatch.setattr(
        agent,
        "model",
        lambda: SimpleNamespace(
            invoke=lambda messages: SimpleNamespace(
                content=[{"type": "text", "text": "안녕하세요"}, {"type": "reasoning", "text": "hidden"}]
            )
        ),
    )
    text, evidence = agent.answer("안녕", str(uuid.uuid4()), [])
    assert text == "안녕하세요" and evidence == []


def test_question_validation_explains_actual_problem(workspace):
    with TestClient(main.app) as client:
        result = client.post("/api/chat", json={"question": "", "source_ids": []})
        assert result.status_code == 422
        assert "1~8,000" in result.json()["detail"]


def test_worker_status_follows_process_lock(workspace):
    assert not worker_service.running()
    with sqlite3.connect(workspace / "worker.lock.sqlite") as db:
        db.execute("BEGIN EXCLUSIVE")
        assert worker_service.running()
        db.rollback()
        assert not worker_service.running()


def test_worker_supervisor_launches_and_stops_owned_child(workspace, monkeypatch):
    started = []

    class Process:
        done = False

        def poll(self):
            return 0 if self.done else None

        def terminate(self):
            self.done = True

        def wait(self, timeout=None):
            return 0

    def spawn(args, **kwargs):
        process = Process()
        started.append((args, kwargs, process))
        return process

    monkeypatch.setattr(worker_service, "_process", None)
    monkeypatch.setattr(worker_service.subprocess, "Popen", spawn)

    async def run():
        task = asyncio.create_task(worker_service.supervise())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(started) == 1 and started[0][0][-1] == "app.worker"
    assert started[0][2].done


def test_progress_persists_for_ui(workspace):
    source = store.add_source("book", "txt", "book.txt")
    store.progress(source["id"], "요약·관계 추출 2/10")
    assert store.source(source["id"])["progress"] == "요약·관계 추출 2/10"


def test_thread_is_released_when_client_never_reads_the_stream(workspace, monkeypatch):
    """A disconnected client must not leave the thread permanently answering (HTTP 409 forever)."""
    import asyncio

    import app.agent

    monkeypatch.setattr(app.agent, "answer", lambda question, tid, scope: ("답변입니다", []))
    main.busy_threads.clear()

    async def abandon_the_stream():
        response = await main.chat(main.ChatInput(question="안녕"))
        assert main.busy_threads, "thread should be held while the answer is in flight"
        del response  # The generator is never iterated, exactly as on an early disconnect.
        while main.busy_threads:
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(abandon_the_stream(), 5))
    assert not main.busy_threads
    with store.connect() as db:
        roles = [r["role"] for r in db.execute("SELECT role FROM messages ORDER BY id")]
    assert roles == ["user", "assistant"], "the answer must still be persisted after a disconnect"
