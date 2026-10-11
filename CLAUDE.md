# Shelf RAG

로컬 단일 사용자 RAG 앱. FastAPI + deepagents + Neo4j + React.

## 손대지 말 것 — 이 앱의 보장이 걸려 있음
- `agent.py render()` 인용 fail-closed 검증
- `ingest.py fetch_url()` SSRF/DNS rebinding 방어
- `database.py` 파라미터 검증 + READ ONLY 트랜잭션 — 임의 SQL 경로를 만들지 말 것
- `graph.publish()` 단일 트랜잭션 + `active_version` 포인터 스위치

## 명령
```powershell
uv run --frozen --cache-dir .cache/uv pytest -q     # 테스트 (Ollama 실측은 $env:RUN_OLLAMA_TESTS='1')
uv run ruff check .
npm.cmd --prefix frontend run build
./run.ps1                                            # API + 워커 (127.0.0.1:8000)
```
서버 실행 중엔 venv 파일이 잠기므로 `--no-sync` 사용.
