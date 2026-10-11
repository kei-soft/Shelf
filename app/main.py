import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import config, graph, ingest, store, worker_service
from app.config import DATA
from app.settings_api import router as settings_router


@asynccontextmanager
async def lifespan(app):
    store.init()
    worker_task = (
        asyncio.create_task(worker_service.supervise())
        if os.getenv("SHELF_AUTOSTART_WORKER", "true").lower() not in {"false", "0"}
        else None
    )
    try:
        yield
    finally:
        if worker_task:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        graph.close()


app = FastAPI(title="Shelf RAG", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
busy_threads = set()

app.include_router(settings_router)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Pydantic errors include rejected inputs by default, including submitted secrets.
    fields = [".".join(map(str, error["loc"])) for error in exc.errors()]
    detail = "입력 형식이 올바르지 않습니다."
    if request.url.path == "/api/chat":
        detail = (
            "질문은 1~8,000자의 텍스트로 입력해주세요."
            if "body.question" in fields
            else "대화 요청 형식이 올바르지 않습니다. 페이지를 새로고침하고 다시 시도해주세요."
        )
    return JSONResponse(
        {
            "detail": detail,
            "fields": fields,
        },
        status_code=422,
    )


# The Vite dev server is a genuinely different origin that proxies /api here, so it stays listed.
# Everything else must match the origin the page was served from -- hardcoding the API's own port
# broke the UI whenever the server moved, and Windows reserves 8000 often enough that it does.
DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    # TrustedHostMiddleware already pins Host to loopback, so matching it is not self-certifying.
    same_origin = bool(host) and origin in {f"http://{host}", f"https://{host}"}
    if origin and not same_origin and origin not in DEV_ORIGINS:
        return JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    # Asset filenames are content-hashed, but index.html names them and is fetched by URL. Letting
    # a browser cache it pins the UI to whichever bundle it saw first, across every rebuild.
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(Exception)
async def failure(request, exc):
    return JSONResponse(
        {"detail": f"{type(exc).__name__}: 서비스 연결과 서버 설정을 확인해주세요."}, status_code=503
    )


@app.exception_handler(ValueError)
async def bad_input(request, exc):
    return JSONResponse({"detail": str(exc)[:500]}, status_code=400)


@app.get("/api/health")
def health():
    s = config.settings()
    try:
        graph.run("RETURN 1 AS ok")
        neo4j = True
    except Exception:
        neo4j = False
    return {
        "neo4j": neo4j,
        "model_configured": (s["LLM_PROVIDER"] == "ollama" or bool(s["OPENAI_API_KEY"]))
        and (s["EMBEDDING_PROVIDER"] == "ollama" or bool(s["OPENAI_API_KEY"])),
        "model_provider": s["LLM_PROVIDER"],
        "database_configured": Path(s.get("DATABASE_CONFIG", "database.json")).exists(),
        "worker": worker_service.status(),
    }


@app.get("/api/worker")
def worker_status():
    return worker_service.status()


@app.get("/api/sources")
def sources():
    return [{k: v for k, v in source.items() if k != "locator"} for source in store.sources()]


@app.post("/api/sources/upload", status_code=201)
async def upload(file: UploadFile):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".epub", ".txt", ".md", ".sql"}:
        raise HTTPException(400, "PDF, EPUB, TXT, MD, SQL 파일을 지원합니다.")
    path = DATA / "originals" / (str(uuid.uuid4()) + suffix)
    size = 0
    try:
        with path.open("wb") as out:
            while block := await file.read(1024 * 1024):
                size += len(block)
                if size > ingest.MAX_BYTES:
                    raise HTTPException(413, "파일은 30MB 이하여야 합니다.")
                out.write(block)
        if not size:
            raise HTTPException(400, "빈 파일입니다.")
        item = store.add_source(Path(file.filename).name[:200], suffix[1:], str(path))
        return {"id": item["id"], "status": item["status"]}
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


class UrlInput(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@app.post("/api/sources/url", status_code=201)
def add_url(body: UrlInput):
    ingest.public_url(body.url)
    item = store.add_source(body.url, "url", body.url)
    return {"id": item["id"], "status": item["status"]}


@app.post("/api/sources/database", status_code=201)
def add_database():
    from app.database import db_config

    db_config()
    item = store.add_source("PostgreSQL 스키마", "database", "configured-postgresql")
    return {"id": item["id"], "status": item["status"]}


# A source is never analysed on registration; the user starts and stops each run.
IDLE = ("draft", "ready", "failed", "cancelled")
BUSY = ("processing", "cancelling")


@app.post("/api/sources/{sid}/analyze")
def analyze(sid: str):
    with store.connect() as db:
        result = db.execute(
            f"UPDATE sources SET status='queued',progress='분석 대기열',error=NULL,updated_at=? "
            f"WHERE id=? AND status IN {IDLE}",
            (store.now(), sid),
        )
        if not result.rowcount:
            raise HTTPException(409, "자료가 없거나 이미 분석 중입니다.")
    return {"ok": True}


@app.post("/api/sources/{sid}/cancel")
def cancel(sid: str):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM sources WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "자료가 없습니다.")
        if row["status"] == "queued":
            # Claiming is blocked by this transaction, so no worker can be running it yet.
            state, note = "cancelled", "분석을 중단했습니다"
        elif row["status"] == "processing":
            # The worker owns the row and flips it to 'cancelled' once it actually stops.
            state, note = "cancelling", "중단하는 중…"
        else:
            raise HTTPException(409, "분석 중인 자료만 중단할 수 있습니다.")
        db.execute(
            "UPDATE sources SET status=?,progress=?,error=NULL,updated_at=? WHERE id=?",
            (state, note, store.now(), sid),
        )
    return {"status": state}


def forget_checkpoints(sid):
    """Drop the agent state of every thread scoped to this source.

    A checkpoint holds the passages the agent read, so deleting a source has to reach them too.
    Visible history in `messages` stays: those threads can no longer be continued anyway, because
    `chat` rejects a scope naming a source that is gone.

    Runs before the source row is removed so a failure here leaves the delete retryable.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver

    with store.connect() as db:
        rows = db.execute("SELECT id, scope FROM threads").fetchall()
    scoped = [r["id"] for r in rows if sid in json.loads(r["scope"])]
    if not scoped:
        return 0
    with SqliteSaver.from_conn_string(str(DATA / "checkpoints.sqlite")) as saver:
        for tid in scoped:
            saver.delete_thread(tid)
    return len(scoped)


@app.delete("/api/sources/{sid}")
def delete(sid: str):
    forget_checkpoints(sid)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "자료가 없습니다.")
        if row["status"] in BUSY:
            # Deleting now would let the running worker publish a version for a gone source.
            raise HTTPException(409, "분석을 중단한 뒤 삭제해주세요.")
        # Queue claim is blocked until graph deletion succeeds; failure rolls back.
        versions = [r[0] for r in db.execute("SELECT id FROM versions WHERE source_id=?", (sid,))]
        if versions or row["active_version"]:
            graph.delete(sid)
        db.execute("DELETE FROM versions WHERE source_id=?", (sid,))
        db.execute("DELETE FROM sources WHERE id=?", (sid,))
    # Raw data is retained only until explicit source deletion. Version snapshots are source-specific hashes.
    if row["kind"] not in {"url", "database"}:
        path = Path(row["locator"]).resolve()
        if path.parent == DATA / "originals":
            path.unlink(missing_ok=True)
    for version in versions:
        (DATA / "originals" / version).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/evidence/{eid:path}")
def evidence(eid: str):
    rows = graph.passages([s["id"] for s in store.sources()], ids=[eid])
    if not rows:
        raise HTTPException(404, "자료가 삭제되었거나 갱신된 근거입니다.")
    return rows[0]


@app.get("/api/sources/{sid}/original")
def original(sid: str):
    source = store.source(sid)
    if not source or source["kind"] in {"database", "url"}:
        raise HTTPException(404, "다운로드 가능한 원본이 없습니다.")
    return FileResponse(source["locator"], filename=source["title"])


@app.get("/api/threads")
def threads():
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM threads ORDER BY created_at DESC")]


@app.get("/api/threads/{tid}")
def history(tid: str):
    with store.connect() as db:
        return [
            {**dict(r), "evidence": json.loads(r["evidence"])}
            for r in db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY id", (tid,))
        ]


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    thread_id: str | None = None


@app.post("/api/chat")
async def chat(body: ChatInput):
    if not body.question.strip():
        raise HTTPException(400, "질문을 입력해주세요.")
    scope = sorted(set(body.source_ids))
    valid = {s["id"] for s in store.sources() if s["active_version"]}
    if not set(scope) <= valid:
        raise HTTPException(
            400,
            "선택한 자료가 아직 분석 중이거나 삭제되었습니다. 준비 완료 후 선택하거나, 자료 선택을 해제해 일반 대화로 질문해주세요.",
        )
    tid = body.thread_id or str(uuid.uuid4())
    try:
        uuid.UUID(tid)
    except ValueError:
        raise HTTPException(400, "잘못된 대화 ID입니다.")
    if tid in busy_threads:
        raise HTTPException(409, "이 대화의 답변을 생성 중입니다.")
    with store.connect() as db:
        row = db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
        if row and json.loads(row["scope"]) != scope:
            raise HTTPException(409, "자료 범위가 바뀌었습니다. 새 대화를 시작해주세요.")
        if not row:
            db.execute(
                "INSERT INTO threads VALUES(?,?,?,?)",
                (tid, body.question[:50], json.dumps(scope), store.now()),
            )
    busy_threads.add(tid)
    store.message(tid, "user", body.question)
    from app.agent import answer

    # The client may never read the stream, so nothing inside the generator can be trusted to run.
    # The task owns both the answer write and the thread release; the stream only reports them.
    task = asyncio.create_task(asyncio.to_thread(answer, body.question, tid, scope))

    def persist(finished):
        try:
            text, citations = finished.result()
        except Exception:
            pass  # Surfaced to the client as an error event; there is no answer to store.
        else:
            store.message(tid, "assistant", text, citations)
        finally:
            busy_threads.discard(tid)

    task.add_done_callback(persist)

    async def stream():
        def event(kind, value):
            return "event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False, default=str) + "\n\n"

        try:
            yield event(
                "status",
                {
                    "thread_id": tid,
                    "message": "자료를 검색하고 출처를 확인하고 있습니다."
                    if scope
                    else "답변을 작성하고 있습니다.",
                },
            )
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=10)
                if not done:
                    yield ": keep-alive\n\n"
            text, citations = await task
            yield event(
                "answer",
                {
                    "text": text,
                    "evidence": citations,
                    "thread_id": tid,
                    "mode": "rag" if scope else "general",
                },
            )
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else f"{type(exc).__name__}: 연결 설정을 확인해주세요."
            )
            yield event("error", {"message": message[:500]})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class GraphInput(BaseModel):
    source_ids: list[str] = Field(max_length=100)
    evidence_ids: list[str] | None = Field(default=None, max_length=200)
    entity_id: str | None = None


@app.post("/api/graph")
def subgraph(body: GraphInput):
    return graph.relations(body.source_ids, ids=body.evidence_ids, entity_id=body.entity_id)


dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
