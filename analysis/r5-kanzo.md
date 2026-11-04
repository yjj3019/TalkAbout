# kanzo 전수 분석 — 사람과 에이전트가 공유하는 초경량 칸반 + MCP

분석 대상: `.analysis-repos/kanzo` (github.com/emanuele-em/kanzo)
커밋: `862399f` (2026-11-02) · **AGPL-3.0** · Rust 2024, 단일 바이너리
분석 방식: **정적 분석만**. 빌드/설치/실행/서버/테스트 실행 없음. 코드 인용은 리포 루트 기준 `path:line`.
열람 범위: `src/`의 `main.rs`·`home.rs`·`schema.sql`·`store.rs`(전체의 약 1/3)·`mcp.rs`(전체)·`web.rs`(라우팅·Guard·changes)·
`share.rs`(전반부)·`sync.rs`(전반부)·`export.rs`(전반부)·`service.rs`(전반부)·`app.js`(폴링부)·README/Cargo.toml. `index.html`(35KB)과
`update.rs`, `store.rs`/`web.rs`의 나머지는 부분 열람이다(§7).

> **라이선스 경고**: kanzo는 AGPL-3.0-only(`Cargo.toml:6`, `README.md:204-206`)다. **코드를 TalkAbout에 복사할 수 없다**.
> 이 문서는 아이디어·계약·UX·실패 사례만 참고한다.

---

## §0 요약 — 핵심 발견

| 주제 | 지점 | 내용 | 신뢰도 |
|---|---|---|---|
| 제품 형태 | `README.md:4-8`, `Cargo.toml:1-8` | 브라우저 칸반 + MCP 서버 + CLI가 한 바이너리(4.5MB, SQLite 포함). 계정·서버·설정 없음, `kanzo` 실행 시 `localhost:4242`. | 높음(문서+코드) |
| 실행 모델 | `src/main.rs:108-130` | **current_thread** Tokio 런타임 + `max_blocking_threads(8)`. "한 사람 + 에이전트별 `kanzo mcp` 프로세스" 전제로 스레드를 최소화. | 높음 |
| 데이터 모델 | `src/schema.sql:4-34`, `src/store.rs:144-148` | `projects`/`tasks`/`comments`(+`images`). 상태는 TEXT: `todo → doing → review → done`, 선택적 `backlog`. `human` 불리언, `position REAL`, `created_at/updated_at`. | 높음 |
| 에이전트 경계 | `src/mcp.rs:396-413`, `src/store.rs:660-681` | `human=1` 카드는 `list_tasks`에서 숨기고 **`next_task`가 절대 집지 않는다**(SQL `human = 0`). "사람 몫"을 스키마 수준에서 분리. | 높음 |
| 원자적 인수 | `src/store.rs:660-681` | `next_task`: `BEGIN IMMEDIATE` 안에서 top todo를 골라 곧바로 `doing`으로 이동·반환. **lease/만료 없음** — 실패하면 사람이 카드를 되돌린다. | 높음 |
| 동시성/영속성 | `src/store.rs:1-2,157-167,196-199`, `src/home.rs:121-146` | 연결마다 `busy_timeout=5s`, **WAL이 아니라 rollback journal(TRUNCATE)**, 쓰기는 `BEGIN IMMEDIATE`. 프로세스 간에는 `kanzo.lock` 공유/배타 락. | 높음 |
| 변경 감지 | `src/schema.sql:36-48`, `src/web.rs:373-385`, `src/app.js:1152-1165` | 모든 테이블 트리거가 `meta.changes`를 +1. `/api/changes`가 `{changes: "<counter>-<db_identity>"}` 반환 → 클라이언트는 **토큰이 다를 때만** 보드 재로드. 숨김 탭은 폴링 생략. | 높음 |
| MCP 계약 | `src/mcp.rs:318-538` | tool **10개**, 응답은 10~60토큰의 짧은 평문(토큰 절약, `mcp.rs:1`). stdio(`kanzo mcp`) + HTTP `/mcp`(`web.rs:147-149`). | 높음 |
| 공유/동기화 | `src/share.rs:31-60`, `src/sync.rs:28-71`, `README.md:91-118` | 같은 LAN은 `share on`/`join`: join한 보드는 요청을 호스트로 **포워딩**. 다른 네트워크는 **private git repo로 DB 동기화**(`--keep-local/--keep-remote`). | 높음(문서+코드) |
| 보안 기본값 | `src/home.rs:20-24`, `src/web.rs:182-189,299-332` | 기본 `127.0.0.1`. 설정 주석에 "**로그인 없음**: 포트에 닿는 사람은 보드와 MCP를 읽고 바꿀 수 있다"고 명시. Host/Origin 검사 + DNS rebinding 방어, CSP/nosniff/no-referrer. | 높음 |
| 마이그레이션 | `src/store.rs:13-17,213-243` | 번호 매긴 `MIGRATIONS` + `COMPATIBLE_FROM=4` + `compatible` meta. **최신 바이너리가 만든 DB를 구버전이 열 수 있는지**를 데이터로 판정, 불가하면 "kanzo update" 안내. | 높음 |
| 테스트 | 모듈 내 `#[test]` 36개 | store/home/share/sync/mcp 등 **모듈 인라인 단위 테스트**. 외부 e2e/브라우저 테스트 없음(리포에 `tests/` 없음). | 높음 |

**TalkAbout 관점 한 줄**: kanzo는 "**결정적 단일 보드 + MCP 계약 + 변경 토큰 + 원자적 인수**"의 좋은 축소판이다.
TalkAbout은 PG·DAG·lease·세대 경계를 이미 갖고 있으므로 **동시성 모델을 베낄 필요는 없고**, MCP tool 설계·human 전용 항목·
변경 토큰·마이그레이션 호환성·내보내기(에이전트 문맥용)를 가져올 가치가 있다.

---

## §1 구조·데이터 모델

### 1.1 프로세스/런타임
- `kanzo` 서브커맨드: `mcp`, `init`, `sync`, `export`, `share`, `join`, `leave`, `update`, `service install|uninstall`(`src/main.rs:40-92`).
- 기본 실행은 `web::serve(home, !no_open)`(`main.rs:145-146`). 서브커맨드가 없으면 보드를 띄우고 브라우저를 연다.
- `mcp`는 stdio 서버(`main.rs:147`), 보드가 떠 있으면 HTTP `/mcp`도 동시에 제공(`web.rs:147-149`, `mcp.rs:63-68`).
- 런타임은 `new_current_thread` + blocking 스레드 8개(`main.rs:115-120`). "보드는 한 사람, 에이전트마다 mcp 프로세스"라는 전제가 코드에 박혀 있다.

### 1.2 데이터 폴더(`~/kanzo`, `KANZO_HOME`, `--home`)
- `config.toml`(port/listen/mcp_http), `.gitignore`, `kanzo.db`, `images/`, `kanzo.lock`, `network`(공유 상태), 동기화 시 `.git`(`home.rs:13-42,83-96`).
- 첫 실행이 템플릿을 만든다(`home.rs:196-201`). `write_whole`은 `.part`에 쓴 뒤 rename — 반쯤 쓴 파일이 실제 이름을 갖지 않는다(`home.rs:222-230`).
- `store()`는 호출마다 **공유 락**을 잡고 Store를 연다(`home.rs:121-146`). 동기화는 배타 락(`home.rs:167-174`). 락을 못 잡으면
  `Error::Busy` → HTTP 503("kanzo sync is updating the board; try again")로 매핑(`home.rs:44,132-134`, `web.rs:345-349`).
- exFAT/네트워크 공유처럼 파일 락이 없는 FS에서는 **경고만 하고 계속** 진행한다(`home.rs:135-139,187-194`).

### 1.3 스키마
- `tasks.status`는 TEXT(컬럼명 고정 아님). `STATUSES = ["todo","doing","review","done"]`, `backlog`는 설정으로 앞에 추가(`store.rs:144-172`).
- `human` 불리언: 사람 전용 카드. `position REAL`: 컬럼 내 수동 정렬. `updated_at`은 상태가 바뀔 때만 갱신(`store.rs:601-646`).
- 이미지: `images` 테이블 + `~/kanzo/images` 파일 1벌(여러 곳에서 참조해도 한 번만 저장, `home.rs:152-165`).
- 변경 카운터: 모든 테이블의 insert/update/delete 트리거가 `meta.changes`를 증가(`schema.sql:36-48`). 이것이 UI 갱신의 유일한 신호다.

---

## §2 MCP 계약 (전수 10개)

| # | tool | 입력(요지) | 의미·반환 | 비고 |
|---|---|---|---|---|
| 1 | `list_projects` | — | 프로젝트 slug/이름/repo/열린 개수. 현재 repo와 일치하면 `<- current repo` | `mcp.rs:318-346` |
| 2 | `create_project` | slug, name?, repo? | 프로젝트 생성, "Created ..." | `mcp.rs:348-360` |
| 3 | `link_repo` | project, repo | 기존 프로젝트에 GitHub repo 연결 | `mcp.rs:362-370` |
| 4 | `list_tasks` | project?, status? | 보드 순서의 task 목록. **`human` 카드는 숨기고 개수만 알림**("N human tasks not shown: for the user, not for agents") | `mcp.rs:372-423` |
| 5 | `get_task` | id | 제목/설명/tag/이미지 경로/댓글 포함 상세 | `mcp.rs:425-437` |
| 6 | `create_task` | project?, title, description?, status?, tags?, human? | 기본 `todo`; `human`·`tags`는 생성 직후 patch | `mcp.rs:439-458` |
| 7 | `update_task` | id + 바꿀 필드만 | patch 의미. 상태/제목/설명/tag/human | `mcp.rs:460-485` |
| 8 | `add_comment` | id, body, author? | 진행 보고용 댓글. author 생략 시 클라이언트 신원/`agent` | `mcp.rs:487-502` |
| 9 | `next_task` | project? | **top todo를 `doing`으로 원자 이동 후 반환**, 사람 카드는 제외 | `mcp.rs:504-523`, `store.rs:660-681` |
| 10 | `delete_task` | id | 댓글·이미지까지 삭제(되돌릴 수 없음) | `mcp.rs:524-538` |

설계 포인트:
- **토큰 경제**: tool 설명은 한 줄, 응답은 평문 10~60토큰(`mcp.rs:1`). 에이전트가 매 호출마다 JSON 스키마를 해석하는 비용을 줄인다.
- **문맥 해석**: 프로젝트를 인자 없이도 결정한다. HTTP MCP로 join한 경우 `x-kanzo-folder`/`x-kanzo-repos`/`x-kanzo-images` 헤더로
  호출자의 repo 목록을 받아 "현재 repo"를 찾는다(`mcp.rs:78-152,237`; `share.rs:23-26`). stdio MCP는 보드 쪽 `cwd` 문맥을 쓴다(`mcp.rs:132-141`).
- **신원**: 인증이 아니라 **author 문자열**이다. 같은 보드에 붙은 어떤 프로세스든 임의 author를 쓸 수 있다(`mcp.rs:494-499`). 사람/에이전트 구분은 `human` 플래그와 author 표기뿐.
- **실패 모델**: tool은 대부분 `CallToolResult` 평문 오류("내용")이고, store 오류는 `run_for`가 문자열로 변환(`mcp.rs:285-316`). TalkAbout처럼 코드화된 에러 taxonomy는 없다.

---

## §3 서버·동시성·영속성

- **WAL을 쓰지 않는다**: "connection per operation"이라 WAL의 `-wal`/`-shm` 생성·정리 비용이 더 크다고 주석에 명시(`store.rs:1-2`).
  `journal_mode=TRUNCATE`, `busy_timeout=5s`, `foreign_keys=ON`, `synchronous`는 SQLite 기본 FULL(`store.rs:157-167`).
- **쓰기 직렬화**: `BEGIN IMMEDIATE`로 즉시 쓰기 락을 잡고, 동시 writer는 busy timeout 동안 큐잉(`store.rs:196-199`). 읽기는 락 없이.
- **원자적 인수**: `next_task`가 SELECT→UPDATE를 한 트랜잭션에서 처리하므로 두 에이전트가 같은 카드를 동시에 집을 수 없다(`store.rs:660-681`).
  단, **인수 후 만료/회수 장치가 없다**. 에이전트가 죽으면 카드는 `doing`에 남고 사람이 옮겨야 한다.
- **수동 정렬**: `position REAL` 중간값 삽입, 간격이 `1e-9` 미만이면 전체 renumber(`store.rs:601-658`). UI 드래그 정렬용.
- **마이그레이션 호환성**: `MIGRATIONS` 배열 + `schema_version`/`compatible` meta(`store.rs:13-42,213-243`).
  - `version > latest` → "이 보드는 더 새 kanzo가 저장했다, `kanzo update`"로 거부(`store.rs:228-236`).
  - `compatible <= latest`면 구버전 바이너리도 연다 → **여러 기기가 서로 다른 버전으로 같은 DB를 공유**하는 sync 시나리오를 배려.
  - `kanzo.db`를 git으로 주고받는 제품이라 마이그레이션 실패가 곧 데이터 손실이므로, 이 안전장치가 핵심이다.
- **변경 토큰**: `changes = "{meta.changes}-{db_identity}"`(`web.rs:373-375`). `db_identity`는 unix inode / Windows mtime(`home.rs:102-119`) —
  git pull로 DB 파일이 통째로 교체되어 카운터가 우연히 같아도 UI가 새로 고침된다. 영리한 디테일.
- **서버 구조**: axum 라우터 1개, `/mcp` nest, `/api/*`, `/app.js`·`/logo.png`는 컴파일 타임 임베딩(`web.rs:145-180`).

---

## §4 공유·보안

- **기본은 로컬**: `listen=127.0.0.1`, `config.toml` 주석에 "로그인 없음 — 포트에 닿는 사람은 읽고 쓸 수 있다"고 **명시적으로 경고**(`home.rs:20-24`).
- **DNS rebinding/Host 방어**: `Guard`가 Host 허용 목록(localhost·IP·자기 hostname)과 동일 Origin을 검사(`web.rs:182-189,299-326`).
  Origin 헤더가 없으면 허용(브라우저 아닌 클라이언트), 있으면 `http://<host>`와 정확히 일치해야 한다.
- **LAN 게스트**: loopback이 아닌 요청은 `guard.lan()`일 때만 허용하고 IP별 last-seen/이름을 기록(`web.rs:280-320`).
  `share on`이면 `listen`을 열고, `join`한 기기는 **모든 요청을 호스트로 포워딩**(`web.rs:285-289`; `share.rs:31-60`).
- **응답 헤더**: CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache`(`web.rs:326-330`).
- **본문 한도**: 이미지 포함 요청 32MB(`share.rs:28-29`, `web.rs:163`).
- **위협 모델의 정직함**: 인증/권한이 없다. 같은 LAN의 누구나 보드와 MCP를 조작할 수 있고, `share on`은 그 범위를 네트워크로 넓힌다.
  README도 "홈 네트워크"를 전제한다(`README.md:91-118`). **TalkAbout의 room 권한·human 경계와 정반대**이므로 보안 모델은 참고하지 말 것.

---

## §5 UI·CLI·테스트

### 5.1 UI
- 단일 `index.html` + `app.js`(60KB) 임베딩. 컬럼 접기(backlog/done), `⌘K`/`/` 검색, `n` 새 카드, `f` 필터, `h` human-only 토글(`README.md:61`, `app.js:1139-1165`).
- 갱신은 **폴링**: `GET /api/changes` → 토큰이 다를 때만 `/api/board` 재로드(`app.js:1152-1165`). `document.hidden`이면 폴링 자체를 건너뛴다.
  SSE/WebSocket 없음. 소규모 로컬 보드에서 충분하고, 서버는 상태를 들고 있지 않다.
- human 전용 카드는 사람 아이콘으로 표시되고, 필터 `h`로 모아 볼 수 있다(`app.js:663,479-485`).

### 5.2 CLI/자동화
- `kanzo export --format markdown|json [--project slug] [--output file]`: 프로젝트/태스크/댓글/이미지 경로까지 내보낸다(`main.rs:61-72`, `export.rs:10-120`).
  Markdown은 상태별 그룹 + repo 링크, JSON은 `exported_at`/`statuses`/`projects[].tasks[]` 구조(`export.rs:16-61,99-120`).
- `kanzo sync [--repo URL] [--keep-local|--keep-remote]`: git 저장소로 DB 동기화(`main.rs:49-60`, `sync.rs:28-71`).
  URL이 `-`로 시작하면 옵션 주입으로 간주해 거부(`sync.rs:38-41`), SSH/HTTPS 후보를 순차 시도, 인증 실패 시 `gh auth login` 안내(`sync.rs:73-87`).
- `kanzo service install|uninstall`: launchd/systemd/Task Scheduler 등록(`service.rs:12-35`). `kanzo update`: 자기 교체 + 서비스 재시작(`main.rs:182-198`).

### 5.3 테스트
- 리포에 별도 `tests/` 디렉터리가 없고, 모듈 안 `#[test]` **36개**가 전부다. store(마이그레이션·정렬·인수), home(락·설정), share/sync(분기) 중심.
- 장점: DB/FS를 tempdir로 실제로 열어 검증(`tempfile` dev-dependency, `Cargo.toml:28`). 단점: HTTP 라우터·MCP 왕복·UI e2e는 자동화 없음.

---

## §6 TalkAbout 적용안 (Python/FastAPI, AGPL — 아이디어만)

### 매핑 표

| kanzo 요소 | 위치 | TalkAbout 대응 | 판정 |
|---|---|---|---|
| `next_task` 원자 인수 | `store.rs:660-681` | `domain.py` plan node 큐잉/claim | **부분 채택**(의미론만) |
| `human` 카드 숨김 | `mcp.rs:396-413` | MCP 노출 최소화·human 전용 항목 | **채택** |
| 짧은 평문 MCP 응답 | `mcp.rs:1,318+` | `talkabout/mcp.py` tool 응답 | **채택** |
| 변경 토큰(`counter-db_identity`) | `web.rs:373-385`, `app.js:1152-1165` | 웹 폴링/스냅샷 신선도 | **채택 후보** |
| 마이그레이션 `compatible` 바닥 | `store.rs:13-42,213-243` | room payload/스키마 버전 정책 | **채택 후보** |
| `export` markdown/JSON | `export.rs` | 계획/방 상태를 에이전트 문맥으로 | **채택** |
| repo 문맥 해석(헤더) | `mcp.rs:78-152` | MCP `room`/`project` 기본값 | 보류(제품 형태 다름) |
| 파일 락 + 배타 sync | `home.rs:121-185` | PG 트랜잭션 | 불필요 |
| WAL 대신 TRUNCATE | `store.rs:1-2` | PG 사용 | 불필요(단, 로컬 캐시 도입 시 참고) |
| 무인증 LAN 공유 | `home.rs:20-24`, `share.rs` | TalkAbout 권한 모델 | **거부** |
| REAL 위치 + renumber | `store.rs:601-658` | 수동 정렬이 필요할 때 | 보류 |
| 단일 바이너리/임베딩 UI | `main.rs`, `web.rs` | 서버 배포 | 참고 |

### 채택 (Adopt)

1. **MCP tool 표면을 kanzo식으로 설계** — TalkAbout `mcp.py`에 work-plan/board tool을 추가한다면:
   - 목록 tool은 **짧은 평문**으로, 카드 1건 = 1줄, 숨긴 항목은 개수만 알린다(`mcp.rs:396-413`).
   - "다음 실행 항목 인수" tool은 **한 트랜잭션에서 선택+상태 전이**를 끝낸다(`store.rs:660-681`).
     TalkAbout은 이미 claim/lease가 있으므로 새 tool은 기존 claim을 호출하는 얇은 래퍼로 두고, **중복 tool 의미론을 만들지 않는다**.
   - human 전용 항목(예: 사람이 직접 확인해야 하는 계획 노드)은 **목록/인수에서 제외**하고 개수만 노출 — 노출 최소화 원칙과 일치.
2. **변경 토큰으로 웹 갱신 판정** — TalkAbout 웹은 SSE+폴링을 이미 쓰지만, "스냅샷이 최신인가"를 판정하는 값이 여러 개다.
   kanzo의 `"{changes}-{db_identity}"`처럼 **단일 불투명 토큰**(예: `{room_revision}:{last_seq}:{plan_run_id}`)을 `/api/changes`에 두고
   클라이언트가 토큰 비교만 하게 하면, 계획 실행·메시지·참여자 변경을 한 경로로 무효화할 수 있다(`web.rs:373-385`, `app.js:1152-1165`).
   단 TalkAbout은 SSE가 있으므로 **폴링 fallback/가시성 절약**에 한정한다.
3. **에이전트 문맥용 export** — `export`의 markdown/JSON처럼, 방의 계획·노드 상태·댓글을 **읽기 전용 스냅샷 파일**로 내보내는 CLI/API를 두면
   외부 에이전트가 MCP 없이도 문맥을 얻는다(`export.rs:16-120`). TalkAbout의 text-artifact/artifact API와 자연스럽게 연결된다.
4. **마이그레이션 "구버전 호환 바닥"** — TalkAbout이 room payload/JSONB의 스키마 버전을 다룰 때, "이 payload를 읽을 수 있는 최소 코드 버전"을
   데이터에 저장하고 **복구 불가 마이그레이션만 바닥을 올린다**는 규율(`store.rs:13-17,213-243`)은 롤백·혼합 배포에서 유용하다.
5. **`human` 플래그의 MCP 비노출** — TalkAbout의 `public_room`/MCP projection에서 human 전용 metadata를 **목록에서 빼고 존재만 알리는** 패턴은
   이미 plan `instruction` 비노출 원칙과 동형이다(`mcp.rs:396-413`). 새 tool 추가 시 같은 규칙을 명시적으로 문서화.

### 보류 (Hold)
- **REAL 위치 + renumber**(`store.rs:601-658`): TalkAbout 웹에서 카드 드래그 정렬이 필요해질 때만. PG에서는 `numeric`/정수 gap + renumber가 더 단순.
- **repo 문맥 자동 해석**(`mcp.rs:78-152`): 에이전트가 "어느 repo에서 왔는지"로 room/project를 고르는 UX는 매력적이나, TalkAbout은 room이 명시적이다.
- **단일 바이너리/임베딩 정적 자산**(`web.rs:145-180`): 배포 단순성은 좋지만 FastAPI/Postgres 구조와 맞지 않는다.

### 거부 (Reject)
- **무인증 LAN 공유/`join` 포워딩**: TalkAbout의 권한·human 경계와 정면 충돌. 같은 기능이 필요하면 인증·권한·감사가 먼저다.
- **git으로 DB 파일 동기화**: 텍스트가 아닌 바이너리 DB를 git merge에 맡기는 모델. TalkAbout은 PG가 원천이므로 해당 없음.
- **lease 없는 `next_task`**: 에이전트가 죽으면 카드가 `doing`에 고착된다. TalkAbout의 claim/lease/uncertain이 우위.

---

## §7 미확인/한계

- 빌드/실행/테스트 실행 금지에 따라 `cargo test`(36개)와 실제 서버 동작은 검증하지 않았다. 성능 수치(4.5MB, 2ms 시작, 3,000 task 17ms, `README.md:195-200`)는 **README 주장**이며 측정하지 않았다.
- 부분 열람: `src/app.js`(60KB)의 대부분, `src/index.html`(35KB), `src/web.rs`의 핸들러 본문, `src/store.rs` 후반(검색/이미지/export 보조), `src/share.rs`/`src/sync.rs` 후반, `src/update.rs`, `src/service.rs` 후반. 따라서 UI 세부·sync 충돌 해결 알고리즘·자동 업데이트 검증은 미확인이다.
- AGPL-3.0 준수 여부는 **법률 자문이 아니다**. 아이디어 참고는 무관하지만, 코드/스키마 SQL/문구를 복사하면 AGPL 의무가 발생할 수 있다.
- kanzo의 보안 모델(무인증)은 의도적 단순화다. "보안이 약하다"는 평가가 아니라 **TalkAbout에 이식하면 안 된다**는 뜻이다.
