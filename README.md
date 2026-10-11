<img src="docs/logo.svg" width="72" alt="Shelf" align="left" hspace="12"/>

# Shelf — 자료와 나누는 대화

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Python Deep Agents **0.7.23**, FastAPI, Neo4j, React로 구성한 로컬 개인용 RAG 앱입니다.
PDF·EPUB·텍스트·웹페이지·PostgreSQL 스키마를 등록하고 출처가 있는 답변과 근거 관계도를 조회합니다.

> **English** — Shelf is a single-user, local RAG app. Register PDFs, EPUBs, web pages or a
> PostgreSQL schema; ask questions and get answers where **every factual paragraph carries a
> citation the server itself resolved** — fabricated citations are rejected rather than shown.
> Runs fully offline with Ollama, or against the OpenAI API. Chunks, summaries and extracted
> entity relations live in Neo4j; the UI shows the passages and a relation graph behind each answer.
> Docs are in Korean. See [SECURITY.md](SECURITY.md) before exposing it to anything but localhost.

![근거가 달린 답변과 출처 패널](docs/screenshots/chat-citations.jpg)

답변의 각 사실 문단에는 서버가 실제로 조회한 원문 ID에서 나온 인용 번호가 붙습니다. 오른쪽 패널에서 그 원문을 바로 확인할 수 있습니다.

![자료에서 추출한 관계도](docs/screenshots/relation-graph.jpg)

관계도 탭은 답변의 근거가 된 문단에서 추출한 개체 관계를 보여줍니다. 노드를 눌러 주변 관계를 펼치고, 간선을 눌러 그 관계의 원문을 읽습니다.

## 빠른 실행 (Windows PowerShell)

Python 3.11 이상, uv, Node.js와 **Neo4j 2026.01 이상**이 필요합니다. 검색이 벡터 인덱스 내부에서 자료 범위를 거르는 `SEARCH` 절을 쓰기 때문이며, 5.x에서는 인덱스 생성이 실패합니다. Docker Desktop은 Neo4j를 컨테이너로 실행할 때만 필요합니다. 프로젝트 루트에서 실행합니다.

```powershell
Copy-Item .env.example .env
# API 키와 접속 정보는 앱의 '연결 및 모델 설정'에서 입력할 수도 있습니다.
# Docker를 쓴다면 최초 Neo4j 서버 비밀번호는 .env의 NEO4J_PASSWORD로 정합니다.
uv sync --frozen --cache-dir .cache/uv
docker compose up -d  # Docker로 Neo4j를 실행할 경우에만
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

API를 실행하면 자료 분석 워커도 자동으로 시작됩니다. 워커가 종료되면 다시 시작하며, 이미 별도 워커가 실행 중이면 중복 실행하지 않습니다.

```powershell
uv run --frozen --cache-dir .cache/uv uvicorn app.main:app --host 127.0.0.1 --port 8000
```

브라우저: **http://127.0.0.1:8000** · API 문서: **http://127.0.0.1:8000/docs**
Neo4j Browser: **http://127.0.0.1:7474**

Windows에서는 `./run.ps1`로 API와 워커를 함께 실행할 수도 있습니다. Neo4j는 Docker 또는 별도 설치 중 선택해 실행하세요.
이미 실행 중인 API가 있으면 먼저 종료하세요. Ctrl+C로 API와 자동 실행한 워커가 함께 종료되며 워커 로그는 `data/worker.log`입니다.

별도 워커를 직접 관리하려면 `.env`에 `SHELF_AUTOSTART_WORKER=false`를 넣고 `uv run --frozen --cache-dir .cache/uv python -m app.worker`를 다른 터미널에서 실행합니다.

1. `자료 추가`로 문서, 웹 URL 또는 PostgreSQL 스키마를 등록합니다. **등록만으로는 분석하지 않습니다.**
2. 목록의 분석 버튼(▶)을 누르면 워커가 청크·요약·임베딩·관계를 생성합니다. 분석 중에는 중단 버튼(■)으로 멈출 수 있고, 중단하거나 실패한 자료는 다시 분석하거나 삭제할 수 있습니다. 삭제하면 Neo4j의 Source·Chunk·Assertion과 원본 파일이 함께 제거됩니다. 준비 완료가 되면 자료를 선택합니다.
3. 자료를 선택하면 근거 기반 답변, 선택하지 않으면 일반 대화로 답합니다. 인용 버튼으로 원문을 확인합니다.
4. 관계도 탭에서 노드를 눌러 주변 관계를 펼치거나 간선을 눌러 근거를 읽습니다.
5. 자료 범위를 바꾸면 새 대화가 시작됩니다. 기존 대화는 최근 대화에서 다시 엽니다.

일반 대화에는 Neo4j나 임베딩이 필요하지 않습니다. 채팅 모델만 연결되면 인사·일반 질문·후속 대화를 처리합니다. 일반 대화는 업로드 자료나 실시간 웹/DB를 읽은 답변이 아니며 자료 출처가 표시되지 않습니다.
자료의 `대기 중`은 분석 시작 전 상태이고, 선택한다고 분석이 시작되는 구조가 아닙니다. 업로드 후 워커가 자동 처리합니다. 분석 중에는 `임베딩 생성`, `요약·관계 추출 3/23`, `Neo4j에 저장`처럼 진행 상황이 표시됩니다. 자료는 준비 완료 후 선택할 수 있습니다.

실제 자료의 본문은 선택한 모델/임베딩 서버에 전송됩니다. 둘 다 로컬 Ollama를 선택하면 OpenAI API로 자료를 보내지 않습니다. Ollama의 cloud 모델이나 외부 Ollama 주소를 사용하면 로컬 처리가 아닙니다.

## 화면에서 설정하기

왼쪽 아래 **연결 및 모델 설정**에서 다음을 입력하고 `설정 저장`을 누릅니다.

- 채팅 제공자(OpenAI/Ollama), 모델 이름
- 임베딩 제공자, 모델 이름, 벡터 차원
- Ollama 서버 주소, 문맥 길이, 설치된 모델 목록 확인
- OpenAI API 키, Neo4j 주소·사용자·접속 비밀번호·DB 이름
- PostgreSQL 읽기 전용 접속 URL

각 `연결 확인` 버튼은 입력 중인 값으로 테스트하며 자동 저장하지 않습니다. `채팅 모델 확인`은 도구 호출을 실제로 실행하고, `임베딩 확인`은 짧은 텍스트를 임베딩하므로 API 선택 시 소량의 사용료가 발생할 수 있습니다.

비밀키는 `data/app.sqlite`에 평문으로 저장합니다. GET/저장 응답에는 원래 비밀키 대신 설정 여부만 반환하며, 비밀키 입력란을 비워두면 기존 값이 유지됩니다. 브라우저 localStorage에는 저장하지 않습니다. 해당 파일도 비밀 설정 파일처럼 보호해주세요.
우선순위는 **화면에서 저장한 값 > 프로세스 환경변수 > .env > 기본값**입니다. API와 워커는 저장값을 다음 작업부터 읽으며 재시작할 필요가 없습니다. 분석/답변 생성 중에는 변경을 거부합니다.

검색 모델·차원 또는 대상 Neo4j 서버/DB를 바꾸면 원본은 유지하고 모든 자료를 다시 분석합니다. 워커를 실행해두세요. 새 검색 모델의 벡터는 별도 Neo4j 인덱스와 프로필로 격리합니다. 다른 서버로 옮기기 전 서버에 남은 그래프는 자동 삭제하지 않습니다.

## Ollama로 실행

Ollama를 설치하고 실행한 뒤 모델을 준비합니다. 다음은 설정 창의 초기 예시입니다.

```powershell
ollama pull qwen3:8b
ollama pull bge-m3
```

설정 창에서 채팅 제공자는 `Ollama`, 모델은 `qwen3:8b`, 임베딩 제공자는 `Ollama`, 모델은 `bge-m3`, 차원은 `1024`로 선택합니다. `채팅 모델 확인`과 `임베딩 및 차원 확인` 후 저장하세요. 서버 주소 기본값은 `http://127.0.0.1:11434`입니다. OpenAI API 키는 필요 없습니다.

Deep Agents는 도구 호출이 가능한 모델이 필요합니다. 설치된 다른 모델도 사용할 수 있지만 모델 크기, 메모리와 도구 호출 정확도에 따라 결과가 달라집니다. 문맥 길이 기본값은 32768이며 메모리가 부족하면 줄여주세요.
공식 모델 정보: [Qwen3 8B](https://ollama.com/library/qwen3:8b), [BGE-M3](https://ollama.com/library/bge-m3).

## Docker의 역할

현재 `compose.yaml`은 **Neo4j 서버 하나만** 실행합니다. 앱과 Ollama를 실행하는 데 Docker가 필요한 것은 아닙니다.
Neo4j Desktop/직접 설치/외부 Neo4j 서버를 쓰면 `docker compose`를 실행하지 않고 설정 창에 해당 접속 정보를 입력하면 됩니다.

설정 창의 Neo4j 비밀번호는 **접속에 사용할 비밀번호**입니다. Neo4j 서버의 비밀번호를 바꾸는 기능이 아닙니다. Docker 초기 비밀번호는 `.env`의 `NEO4J_PASSWORD`로 정합니다. 기존 Neo4j 데이터 볼륨이 있으면 `.env`를 바꿔도 서버 비밀번호는 바뀌지 않으므로 기존 비밀번호를 입력하세요.

## PostgreSQL

```powershell
Copy-Item database.example.json database.json
```

`.env`에 다음을 추가합니다. 비밀번호의 특수문자는 URL 인코딩해야 합니다.

```dotenv
SOURCE_DATABASE_URL=postgresql+psycopg://rag_reader:YOUR_PASSWORD@localhost:5432/YOUR_DATABASE
DATABASE_CONFIG=database.json
```

`database.json`의 `schemas`, `tables`, `queries`를 실제 DB 구조에 맞게 편집합니다.
예시 `products` 테이블이나 `low_stock` SQL은 사용자의 실제 스키마에 맞춰 바꿔야 합니다.
접속 URL은 설정 창에서 입력할 수도 있으며 저장 후에는 다시 반환하지 않습니다. `database.json`과 `.env` 및 `data/`는 Git에서 제외됩니다.

DB 관리자가 전용 계정에 필요한 테이블의 SELECT만 부여하고 기본 읽기 전용 트랜잭션을 설정합니다.
앱도 매 조회에 `SET TRANSACTION READ ONLY`, 5초 statement timeout, 2초 lock timeout을 적용합니다.
에이전트는 등록된 조회 이름과 매개변수만 전달하며 SQL 자체를 작성하거나 실행할 수 없습니다.
결과는 최대 100행이며 조회 시각과 매개변수가 근거에 기록됩니다.

스키마 등록은 실제 행 데이터를 임베딩하지 않습니다. 실제 수치는 허용된 조회를 사용합니다.
DB 조회 도구는 PostgreSQL 자료를 선택한 대화에서만 제공됩니다.

## 자료 처리와 검색

- 파일 최대 30MB, 자료당 최대 2,000청크. 청크는 페이지/EPUB spine 항목을 보존하며 700토큰, 80토큰 겹침입니다.
- 웹은 입력한 **단일 HTML 페이지**를 수집합니다. 크롤링 확장·로그인·JavaScript 렌더링은 포함하지 않습니다.
- 웹 연결은 공개 IP로 제한하고 연결 주소를 고정해 DNS rebinding을 차단합니다. 리디렉션은 최종 URL을 다시 등록합니다.
- 문서별 내용 해시로 중복 재분석을 생략합니다. 새로운 버전이 모두 완성되어야 Neo4j 활성 포인터를 전환합니다.
- 벡터와 full-text 검색 후보를 RRF로 결합합니다. 후보 100개에 자료 범위를 적용하므로 대규모 자료함에서 검색 누락이 생길 수 있습니다. 실제 질문으로 recall을 평가해 후보 수나 pre-filter 검색으로 확장합니다.
- 관계는 원문 청크와 연결된 미검토 Assertion입니다. 이름 정규화만 적용하므로 동명이인·동음이의어를 구별하는 개체 검토 기능은 후속 작업입니다.
- 전체 요약 도구는 해당 자료의 모든 청크 요약을 읽습니다. 긴 책의 요약 품질은 별도 평가가 필요합니다.
- 설정 창에서 임베딩 모델/차원을 변경하면 자료가 재분석 대기열에 등록됩니다. 새 프로필별 인덱스를 사용해 벡터 혼합을 막습니다.
- 작업 대기열은 SQLite에 저장됩니다. 워커 재시작 시 중단 작업을 복구하고, 실패 작업은 UI에서 재시도합니다.
- 갱신 버튼은 웹/DB의 최신 내용을 다시 가져옵니다. 정기 자동 갱신은 포함하지 않습니다.

## OCR

텍스트가 부족한 PDF 페이지는 기본적으로 실패로 표시하여 누락을 숨기지 않습니다.
스캔 책은 `uv sync --extra ocr --cache-dir .cache/uv` 실행 후 Tesseract와 `kor`, `eng` 언어 데이터를 설치하고 `.env`에 다음을 설정합니다.

```dotenv
OCR_ENABLED=true
OCR_LANGUAGE=kor+eng
# 필요 시 TESSDATA_PREFIX를 설치된 tessdata 디렉터리로 지정
```

OCR 결과의 페이지 위치는 보존되지만 표 구조, 수식, 이미지 도표 해석은 보장하지 않습니다.

## 개발 및 검증

```powershell
uv run --frozen --cache-dir .cache/uv pytest -q
uv run --frozen --cache-dir .cache/uv ruff check app tests
npm.cmd --prefix frontend run build
```

프런트 개발은 `npm.cmd --prefix frontend run dev`로 실행합니다. `/api` 요청은 8000번 API로 전달됩니다.

주요 코드:

| 파일 | 역할 |
|---|---|
| `app/main.py` | 자료·대화·근거·그래프 API, SSE 응답 |
| `app/agent.py` | Deep Agents 도구, 대화 체크포인트, 인용 검증 |
| `app/worker.py` | 영속 작업 큐 처리와 지식 추출 |
| `app/ingest.py` | 문서/웹/OCR 추출, 청크 생성 |
| `app/graph.py` | Neo4j 트랜잭션, 검색, 관계 탐색 |
| `app/database.py` | PostgreSQL 허용 조회와 스키마 수집 |
| `frontend/src/main.jsx` | 자료함·채팅·출처·관계도 UI |

현재 SSE는 진행 상태와 **인용 검증을 마친 최종 답변**을 전송합니다. 검증 전 토큰을 보여주는 스트리밍은 하지 않습니다.
인용 검증은 ID의 존재를 확인하며 문장과 근거 사이 의미적 정합성까지 보증하지 않습니다.

## 운영 범위

현재는 **단일 사용자, 단일 API 프로세스, 단일 워커**의 로컬 앱입니다. API는 127.0.0.1에 바인딩합니다.
다중 사용자 인증·권한 격리·인터넷 공개·여러 API 워커는 아직 지원하지 않습니다.
Neo4j 포트도 localhost에만 열립니다. 외부 서비스로 배포하기 전 인증·자료별 권한·업로드 자원 격리가 필요합니다.
파서는 로컬 프로세스에서 실행되므로 신뢰하는 자료만 업로드하세요.

원본·대화·화면 설정은 `data/`에, 그래프는 연결한 Neo4j에 저장됩니다. Docker 구성이라면 그래프는 named volume에 저장됩니다. `data/app.sqlite`와 `.env`에는 비밀키가 들어갈 수 있으므로 공유하지 마세요.
개별 자료 삭제는 원본과 인덱스를 제거합니다. 기존 대화의 본문에 이미 포함된 내용은 별도로 관리해야 합니다.

## 공식 문서

- https://pypi.org/project/deepagents/ (구현 버전 0.7.23)
- https://docs.langchain.com/oss/python/deepagents/customization
- https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/

## 보안

이 앱은 **인증이 없고** API 키를 `data/app.sqlite`에 **평문으로** 저장합니다. `127.0.0.1`에만 바인딩해 개인 컴퓨터에서 사용하세요. 자세한 내용과 프롬프트 인젝션 방어 설계는 [SECURITY.md](SECURITY.md)를 참고하세요.

## 라이선스

[MIT](LICENSE)
