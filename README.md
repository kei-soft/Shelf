<img src="docs/logo.svg" width="72" alt="Shelf" align="left" hspace="12"/>

# Shelf — 자료와 나누는 대화

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

PDF·EPUB·텍스트·웹페이지·PostgreSQL 스키마를 등록하고, **출처가 검증된 답변**과 근거 관계도를 받는 로컬 개인용 RAG 앱입니다. Ollama만으로 완전히 오프라인 구동하거나 OpenAI API를 쓸 수 있습니다.

> **English** — Shelf is a single-user, local RAG app over PDFs, EPUBs, web pages and a PostgreSQL
> schema. Every factual paragraph in an answer must carry an evidence ID the server itself resolved
> that turn; fabricated or unciteable claims are rejected rather than shown. Runs fully offline with
> Ollama, or against the OpenAI API. Docs below are in Korean. Read [SECURITY.md](SECURITY.md)
> before exposing it to anything but localhost.

![근거가 달린 답변과 출처 패널](docs/screenshots/chat-citations.jpg)

![자료에서 추출한 관계도](docs/screenshots/relation-graph.jpg)

---

## 목차

[무엇이 다른가](#무엇이-다른가) · [요구 사항](#요구-사항) · [설치](#설치) · [실행](#실행) · [사용법](#사용법) ·
[모델 설정](#모델-설정) · [Neo4j](#neo4j) · [PostgreSQL](#postgresql-선택) · [OCR](#ocr-선택) ·
[데이터와 전송 범위](#데이터와-전송-범위) · [동작 방식](#동작-방식) · [개발](#개발) · [한계](#한계)

---

## 무엇이 다른가

**인용이 검증됩니다.** 답변의 모든 사실 문단은 서버가 **그 턴에 실제로 조회한** 원문 ID를 가져야 합니다. 모델이 ID를 지어내거나 근거 없이 주장하면, 그 문단은 화면에 나오지 않고 거절됩니다. 인용 번호는 모델이 쓰는 것이 아니라 서버가 붙입니다.

**자료가 명령을 내리지 못합니다.** 업로드한 문서 본문은 신뢰할 수 없는 입력으로 다룹니다. 에이전트에게는 검색 관련 도구 4~6개만 주고, 프레임워크가 기본 제공하는 파일시스템·서브에이전트 도구는 제거했습니다.

**검색 범위가 인덱스 안에서 걸립니다.** 선택한 자료와 활성 버전으로 Neo4j 벡터 인덱스 **내부에서** 후보를 거릅니다. 자료가 늘어도 좁은 선택이 검색 결과를 잃지 않습니다.

**분석을 직접 시작하고 멈춥니다.** 등록만으로는 아무 일도 일어나지 않습니다. 분석 버튼을 눌러 시작하고, 진행 중에 중단할 수 있으며, 삭제하면 Neo4j 데이터·원본 파일·대화 체크포인트가 함께 사라집니다.

---

## 요구 사항

| 항목 | 요구 |
|---|---|
| Python | 3.11 이상 (uv 사용) |
| Node.js | 프런트엔드 빌드용 |
| **Neo4j** | **2026.01 이상** |
| Ollama 또는 OpenAI 키 | 채팅·임베딩 모델 |
| Docker | 선택 — Neo4j를 컨테이너로 띄울 때만 |

> **Neo4j 2026.01 이상이 필수입니다.** 검색이 벡터 인덱스 내부에서 자료 범위를 거르는 Cypher `SEARCH` 절을 사용하며, 5.x에서는 인덱스 생성 단계에서 실패합니다.

---

## 설치

```bash
cp .env.example .env          # Windows: Copy-Item .env.example .env
uv sync --frozen --cache-dir .cache/uv
npm --prefix frontend ci      # Windows: npm.cmd
npm --prefix frontend run build
```

`.env`는 선택입니다. API 키와 접속 정보는 앱의 **연결 및 모델 설정** 화면에서 입력해도 됩니다.

Neo4j를 Docker로 띄운다면 최초 서버 비밀번호를 `.env`의 `NEO4J_PASSWORD`로 정한 뒤:

```bash
docker compose up -d
```

---

## 실행

```bash
uv run --frozen --cache-dir .cache/uv uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows에서는 `./run.ps1` 하나로 의존성 확인부터 실행까지 처리합니다.

| 주소 | 용도 |
|---|---|
| http://127.0.0.1:8000 | 앱 |
| http://127.0.0.1:8000/docs | API 문서 |
| http://127.0.0.1:7474 | Neo4j Browser |

API를 실행하면 **자료 분석 워커가 자동으로 함께 시작**됩니다. 워커가 죽으면 다시 띄우고, 이미 다른 워커가 돌고 있으면 중복 실행하지 않습니다. Ctrl+C로 함께 종료되며 로그는 `data/worker.log`입니다.

워커를 직접 관리하려면 `.env`에 `SHELF_AUTOSTART_WORKER=false`를 넣고 별도 터미널에서:

```bash
uv run --frozen --cache-dir .cache/uv python -m app.worker
```

---

## 사용법

1. **자료 추가** — 문서, 웹 URL 또는 PostgreSQL 스키마를 등록합니다. 등록 직후 상태는 `분석 대기`이며, **등록만으로는 분석하지 않습니다.**
2. **분석 시작** — 목록의 ▶ 버튼을 누르면 워커가 청크·임베딩·요약·관계를 만듭니다. 진행 상황이 `요약·관계 추출 48/93`처럼 표시됩니다.
3. **중단·재분석·삭제** — 분석 중에는 ■ 버튼으로 멈출 수 있습니다. 중단하거나 실패한 자료는 다시 분석하거나 삭제합니다.
4. **대화** — `준비 완료`가 된 자료를 선택하고 질문합니다. 자료를 선택하지 않으면 일반 대화로 답합니다.
5. **근거 확인** — 인용 번호나 출처 칩을 눌러 원문을 읽고, **관계도** 탭에서 노드를 눌러 주변 관계를 펼치거나 간선을 눌러 그 관계의 원문을 봅니다.

자료 범위를 바꾸면 새 대화가 시작됩니다. 기존 대화는 **최근 대화**에서 다시 엽니다.

**일반 대화**(자료 미선택)에는 Neo4j도 임베딩도 필요 없습니다. 채팅 모델만 연결되면 동작하며, 업로드 자료나 웹·DB를 읽지 않으므로 출처가 표시되지 않습니다.

---

## 모델 설정

왼쪽 아래 **연결 및 모델 설정**에서 입력하고 `설정 저장`을 누릅니다. 각 `연결 확인` 버튼은 입력 중인 값으로 테스트하며 자동 저장하지 않습니다.

- `채팅 모델 확인`은 **도구 호출을 실제로 실행**합니다. Deep Agents가 도구 호출을 요구하므로 포트만 확인해서는 의미가 없습니다.
- `임베딩 및 차원 확인`은 짧은 텍스트를 임베딩해 실제 출력 차원이 설정과 맞는지 봅니다.
- API 제공자를 선택했다면 두 버튼 모두 소량의 사용료가 발생합니다.

설정 우선순위는 **화면 저장값 > 환경변수 > `.env` > 기본값**입니다. 저장하면 다음 작업부터 적용되며 재시작이 필요 없습니다. 분석이나 답변 생성 중에는 저장을 거부합니다.

### Ollama (완전 로컬)

```bash
ollama pull qwen3:8b
ollama pull bge-m3
```

설정 창에서 채팅 `Ollama` / `qwen3:8b`, 임베딩 `Ollama` / `bge-m3` / 차원 `1024`을 지정합니다. 서버 주소 기본값은 `http://127.0.0.1:11434`이고 OpenAI 키는 필요 없습니다.

도구 호출이 가능한 모델이어야 합니다. 모델에 따라 지시 이행 품질이 크게 다르므로, 바꾼 뒤에는 `채팅 모델 확인`으로 검증하세요. 문맥 길이 기본값 32768은 메모리가 부족하면 줄입니다.

참고: [Qwen3 8B](https://ollama.com/library/qwen3:8b) · [BGE-M3](https://ollama.com/library/bge-m3)

### OpenAI

설정 창에 API 키를 넣고 채팅·임베딩 제공자를 `OpenAI`로 바꿉니다. 채팅과 임베딩을 **따로** 고를 수 있으므로, 임베딩만 로컬에 두는 구성도 가능합니다.

> **임베딩 모델이나 차원을 바꾸면 등록된 모든 자료가 재분석됩니다.** 원본은 유지되며, 새 벡터는 별도 인덱스로 격리해 섞이지 않게 합니다. 채팅 모델만 바꾸는 것은 재분석을 일으키지 않습니다.

---

## Neo4j

`compose.yaml`은 **Neo4j 서버 하나만** 띄웁니다. 앱과 Ollama에는 Docker가 필요하지 않습니다. Neo4j Desktop이나 직접 설치, 외부 서버를 쓴다면 `docker compose`를 건너뛰고 설정 창에 접속 정보만 넣으면 됩니다.

설정 창의 Neo4j 비밀번호는 **접속에 사용할 비밀번호**입니다. 서버 비밀번호를 바꾸는 기능이 아닙니다. Docker 초기 비밀번호는 `.env`의 `NEO4J_PASSWORD`로 정해지며, 기존 데이터 볼륨이 있으면 `.env`를 바꿔도 서버 비밀번호는 그대로이므로 **기존 비밀번호**를 입력해야 합니다.

대상 Neo4j 서버나 DB를 바꾸면 자료를 다시 분석합니다. **이전 서버에 남은 그래프는 자동으로 지우지 않습니다.**

---

## PostgreSQL (선택)

```bash
cp database.example.json database.json
```

`.env`에 추가합니다. 비밀번호의 특수문자는 URL 인코딩이 필요합니다.

```dotenv
SOURCE_DATABASE_URL=postgresql+psycopg://rag_reader:YOUR_PASSWORD@localhost:5432/YOUR_DATABASE
DATABASE_CONFIG=database.json
```

`database.json`의 `schemas`, `tables`, `queries`를 실제 스키마에 맞게 편집하세요. 예시의 `products` 테이블과 `low_stock` 조회는 그대로 쓸 수 없습니다.

**에이전트는 SQL을 작성하거나 실행할 수 없습니다.** `database.json`에 미리 등록한 조회의 **이름과 매개변수만** 전달하며, 매개변수는 타입 검증을 거칩니다. 모든 조회에 `SET TRANSACTION READ ONLY`, 5초 statement timeout, 2초 lock timeout이 적용되고 결과는 최대 100행입니다. 조회 시각과 매개변수가 근거에 기록됩니다.

DB 관리자는 전용 계정에 필요한 테이블의 SELECT만 부여하시기 바랍니다. 스키마 등록은 **행 데이터를 임베딩하지 않습니다** — 실제 수치는 등록된 조회로만 가져옵니다. DB 조회 도구는 PostgreSQL 자료를 선택한 대화에서만 제공됩니다.

---

## OCR (선택)

텍스트가 거의 없는 PDF 페이지는 기본적으로 **실패로 표시**합니다. 누락을 조용히 숨기지 않기 위해서입니다.

스캔본을 다루려면 Tesseract와 `kor`, `eng` 언어 데이터를 설치한 뒤:

```bash
uv sync --extra ocr --cache-dir .cache/uv
```

```dotenv
OCR_ENABLED=true
OCR_LANGUAGE=kor+eng
# 필요 시 TESSDATA_PREFIX 를 tessdata 디렉터리로 지정
```

페이지 위치는 보존되지만 표 구조·수식·도표 해석은 보장하지 않습니다.

---

## 데이터와 전송 범위

| 무엇 | 어디에 |
|---|---|
| 원본 파일, 대화, 설정 | `data/` |
| 청크·임베딩·개체 관계 | 연결한 Neo4j |
| 비밀키 (API 키, 비밀번호) | `data/app.sqlite`에 **평문** |

**자료 본문은 선택한 모델 서버로 전송됩니다.** 채팅과 임베딩을 모두 로컬 Ollama로 두면 컴퓨터 밖으로 나가지 않습니다. OpenAI를 고르거나, Ollama의 `*-cloud` 태그 모델 또는 외부 Ollama 주소를 쓰면 해당 서버로 전송됩니다.

비밀키는 브라우저로 **되돌려 보내지 않습니다.** 설정 조회 응답은 설정 여부(불리언)만 반환하고, 입력란을 비워두면 기존 값이 유지됩니다. `.env`, `data/`, `database.json`은 Git에서 제외됩니다.

자료를 삭제하면 Neo4j의 Source·Chunk·Assertion, 원본 파일, 그 자료를 쓰던 대화의 에이전트 체크포인트가 함께 제거됩니다. **대화 기록 자체는 남습니다.**

---

## 동작 방식

### 수집

- 파일 최대 30MB, 자료당 최대 2,000청크
- 청크는 700토큰 / 80토큰 겹침이며 페이지·EPUB spine 위치를 보존
- 웹은 입력한 **단일 HTML 페이지**만 수집 — 크롤링·로그인·JavaScript 렌더링은 없음
- 웹 연결은 공개 IP로 제한하고 검증한 주소에 직접 연결해 **DNS rebinding을 차단**. 리디렉션은 최종 URL을 다시 등록해야 함
- 내용 해시로 중복 재분석을 생략하고, **새 버전이 완전히 만들어진 뒤에야** 활성 포인터를 전환
- 청크 하나를 파싱하지 못해도 **그 청크만 요약 없이 색인**하고 나머지는 살림

### 검색

- 벡터 검색과 full-text 검색 후보를 RRF로 결합
- 벡터 쪽은 `SEARCH` 절로 **인덱스 내부에서** 자료 범위와 활성 버전을 필터링
- full-text 쪽은 후보를 뽑은 뒤 거르므로 자료가 아주 많아지면 recall 평가가 필요

### 답변

- 에이전트가 검색·원문 조회·관계 탐색·전체 요약 도구를 사용
- 최종 답변은 구조화 출력으로 받고, 모델이 형식을 지키지 못하면 근거 목록을 제시해 **한 번 더 요청**
- 모든 사실 문단의 ID를 서버 조회 결과와 대조해 검증 — 통과하지 못하면 거절

### 저장과 복구

- 작업 큐는 SQLite. 워커가 재시작되면 중단된 작업을 복구하고, 실패한 작업은 화면에서 재시도
- 관계는 원문 청크에 연결된 **미검토 추출 결과**입니다. 이름 정규화만 적용하므로 동명이인은 구별하지 못합니다

---

## 개발

```bash
uv run --frozen --cache-dir .cache/uv pytest -q
uv run --frozen --cache-dir .cache/uv ruff check app tests
npm --prefix frontend run build
```

테스트는 **Neo4j나 Ollama 없이** 동작합니다. 실제 모델을 쓰는 테스트 하나만 `RUN_OLLAMA_TESTS=1`일 때 실행됩니다.

프런트엔드 개발 서버는 `npm --prefix frontend run dev`이며, `/api` 요청을 API로 전달합니다. API가 8000이 아닌 포트에 있으면 `VITE_API_TARGET`으로 지정하세요.

| 파일 | 역할 |
|---|---|
| `app/main.py` | 자료·대화·근거·그래프 API, SSE 응답 |
| `app/agent.py` | Deep Agents 도구, 대화 체크포인트, **인용 검증** |
| `app/worker.py` | 작업 큐 처리, 임베딩, 요약·관계 추출 |
| `app/ingest.py` | 문서·웹·OCR 추출과 청크 생성 |
| `app/graph.py` | Neo4j 트랜잭션, 하이브리드 검색, 관계 탐색 |
| `app/database.py` | PostgreSQL 허용 조회와 스키마 수집 |
| `app/store.py` | SQLite 작업 큐와 대화 메타데이터 |
| `frontend/src/main.jsx` | 자료함·채팅·출처·관계도 UI |

---

## 한계

- **단일 사용자, 단일 API 프로세스, 단일 워커**입니다. 인증·권한 격리·다중 워커가 없습니다
- API는 `127.0.0.1`에만 바인딩합니다. 외부 공개 전에 인증과 자료별 권한이 필요합니다
- 파서가 로컬 프로세스에서 실행되므로 **신뢰할 수 있는 자료만** 올리세요
- 답변은 인용 검증을 마친 뒤 한 번에 전송합니다. 검증 전 토큰 스트리밍은 하지 않습니다
- **인용 ID 검증을 통과했다는 것이 답변 내용의 정확성을 뜻하지는 않습니다.** 문장과 근거 사이의 의미적 정합성까지 보증하지 않습니다
- 전체 요약 도구는 자료의 모든 청크 요약을 읽습니다. 긴 책의 요약 품질은 별도 평가가 필요합니다
- 갱신은 수동입니다. 웹·DB의 정기 자동 갱신은 없습니다

---

## 참고 문서

- [Deep Agents](https://pypi.org/project/deepagents/) — 구현 버전 0.7.23
- [Deep Agents 커스터마이징](https://docs.langchain.com/oss/python/deepagents/customization)
- [Neo4j 벡터 인덱스](https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/)

## 보안

인증이 없고 API 키를 평문으로 저장합니다. 개인 컴퓨터에서 `127.0.0.1`로만 사용하세요. 자세한 내용과 프롬프트 인젝션 방어 설계는 [SECURITY.md](SECURITY.md)를 참고하세요.

## 라이선스

[MIT](LICENSE) © keisoft
