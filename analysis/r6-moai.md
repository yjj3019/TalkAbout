# Moai (buzzni/moai) 정밀 분석 — R6

- 분석 대상: `C:\AI-Codding\claude\TalkAbout\.analysis-repos\moai` (전체 이력 clone)
- 분석 시점 HEAD: `6f0772eb` / 브랜치 `develop`(기본) / 2026-11-05 / v0.51.0
- 방법: 저장소 수정·빌드·테스트 없음(요청 준수). 정적 분석(읽기·grep·셸 통계)만 수행.
- 인용 경로는 전부 저장소 루트 기준이며 `파일:줄` 형식(`src/store.rs:1095`)으로 표기.

---

## §0 요약

### 0.1 한눈에 보기

| 항목 | 내용 |
|---|---|
| 성격 | 저장소-내재(repo-embedded) 이슈 트래커 + TUI 탐색기 + 브라우저 보드 + 에이전트 감독(supervisor)을 한 바이너리로. "Agile, for your coding agents"(README.md:14-15) |
| 진실 원천 | `.moai/issues.jsonl` 단일 스냅샷. 저널(`.moai/journal/*.jsonl`)은 부록 전용이며 상태 계산에 읽히지 않음 |
| 핵심 철학 | "No approval gate. … `moai status` reflects back at you, never enforces."(README.md:17-18) — 승인 게이트 없음, 규율은 반사만 |
| 스택 | Rust 단일 바이너리(edition 2024, MSRV 1.88). 비동기 런타임·DB·ORM 없음(`src/web/mod.rs:6`) |
| 워크스페이스 | 루트 `moai`(0.51.0) + `crates/moai-gantt`(순수 ratatui 간트 위젯, Cargo.toml) |
| 주요 의존 | clap·serde·ratatui 0.30/crossterm·pulldown-cmark·toml_edit·ureq(rustls)·fs2·image·ring/base64·signal-hook(Unix) |
| dev-dependency | 0개. 통합 테스트는 바이너리를 직접 구동(`CARGO_BIN_EXE_moai`) |
| 활동 규모 | 2026-09-11 첫 커밋 → 2026-11-05(약 8주) / 6,693 커밋 / 머지 1,238(≈18.5%) / 태그 60 / 라이브 이슈 파일 273줄 + 아카이브 3,708줄 |
| 규율 형태 | 상태 전이 자유. 기계가 막는 것은 **조용한 손실**뿐(중복 id·픽업 경합·필드 소실) |
| 다중 벤더 | 같은 트리에 Claude Code(감독+서브에이전트)·Codex·Antigravity(가끔) 공동 작업. "Different vendors. One team."(README.md:58-59) |
| 라이선스 | MIT(LICENSE; README.md:647) |

### 0.2 핵심 판정

- moai는 "규율을 도구가 아니라 **글+훅+데이터**로 흘려보내고,
  **조용한 손실(동시 쓰기·필드 소실·인용 붕괴·세션 사망)만 기계가 막는**"
  단일-기준 설계다.
- 우리 작업장(TalkAbout: FastAPI+PostgreSQL, 룸 단위 토론, analysis/change_proposal, 실행 예약·영수증, 웹 GUI,
  멀티 벤더)과 정면으로 겹치는 실현물이며, §5에서 파일:줄 단위 적용안을 뽑았다.
- **이식 대상 6선**: 단일 쓰기 경로+락·원자 교체(`store.rs:1095-1147`) / 필드 단위 3-way 머지(`merge_driver.rs:11-21`) /
  ready·others·held 3분류+규칙 단일화(`report.rs:4361-4420`) / 감독-일꾼 할당 카드+노트 운반 보고(`supervise.rs:105-195`) /
  하트비트 라이브니스(`pulse.rs`, `docs/json.md:23-61`) / 인용·파생값을 저장하지 않고 질의 시 계산(`git.rs:6-9`, `worktree.rs:1-19`).
- **먼저 막아야 할 결함**: 시크릿 스캔 부재(D1) / 머지 드라이버·훅의 per-clone 설치·트러스트 의존(D2) /
  웹 인증·권한 부재(D5) / 신원 자기선언(D6) / 스키마 전방 호환 한정(D8).
- **결론**: 모방이 아니라 **실패 모드 방어 + 규율-테스트 문화**를 가져온다. 토론(논쟁) 축·멀티 주체 권한 축은
  moai가 의도적으로 안 가진 기능이므로(§2.6, §4 D13) 우리가 새로 설계한다.

---

## §1 아키텍처

### 1.1 저장소-내재 산물 모델 (`.moai/`)

- 트래커는 `.moai/` 한 디렉터리에 산다.
  - `issues.jsonl` — 스냅샷(진실)
  - `journal.jsonl` + `journal/<email>.jsonl` — 저널(부록 전용)
  - `config.toml` — 저장소 규칙(`prefix`, `statuses = "todo,in_progress,review,done"`, `archive_days = 2`, `[supervise]`)
  - `archive/<year>.jsonl` — 명시적 보관
  - `images/` — 이슈 그림
  - `guide.md` — 선택(`--guide file`)
- 저장소 밖에 두는 것은 픽/하트비트 표식(user config 곁)뿐(`src/pulse.rs:1-16`).
- **스냅샷이 진실이며 이벤트 접기가 없다**(CLAUDE.md "스냅샷이 진실이다 — 접지 않는다").
- 저널은 상태 계산에 **읽히지 않는다**(CLAUDE.md "저널은 상태 계산에 읽히지 않는다"):
  - 저널을 접어야 답이 나오는 질문이 생기면 → 그 답이 스냅샷 필드가 되어야 한다.
- 쓰기 순서: 락 → 락 안에서 재읽기 → 고치기 → 정규화·검증·정렬 → 원자적 교체 → 저널 추가
  (`store.rs:1079-1084`).
- "스냅샷 먼저, 저널 나중": 중간에 죽으면 저널에 줄이 하나 비는 것(이력 공백)까지 허용,
  반대 순서는 "일어나지 않은 일을 저널이 주장"하는 것과 다르다(`store.rs:1082-1084`).
- 멱등성: "읽고 그대로 쓰면 바이트가 같아야 한다"(CLAUDE.md "테스트")가 스냅샷 재작성의 방패.
- 미지 필드 보존: 모르는 값을 기본값으로 접으면 되쓰기 한 번에 원래 값이 사라진다 → 접지 않는다
  (`model.rs:132-135`).
- 아카이브: `moai archive`가 명시적 유지(기본 `archive_days = 2`, config.toml;
  `archive.rs:1` "Every mutation runs under the repository write lock").
  - 아카이브 id는 재발급 금지(`archive::Reserved`, `archive.rs:74-79`).
  - 못 읽은 아카이브 원본은 `ArchiveUnread`로만 이력(그 id들 자체도 모른다).
  - 되살림(`moai mv <id> todo --from done`)은 그 줄만 복구, 옛 묶음은 아카이브에 그대로.
- merge 속성(.gitattributes):
  - 스냅샷 `merge=moai`(필드 단위 3-way, 1.4절) — `merge=union` 금지(같은 id 두 줄 = 데이터 손상, `.gitattributes:8-13`).
  - 저널은 `merge=union` — append-only·순서 무의미·상태 계산 무관이라 정당(`.gitattributes:19-21`).
  - `* text=auto eol=lf`(Windows CRLF 방지, `.gitattributes:6`) / `docs/cli.md`·`src/web/dist/**` 생성물 선언(`:29-38`).

### 1.2 4표면: CLI / TUI / web / ctl

- 계층 원칙: 계산(순수 `report`·`query`·`model`·`nav`) — 렌더러(CLI·TUI·web) — 그립(ctl).
  - "계산이 렌더러에 있으면 나중에 TUI가 같은 것을 다시 짜야 한다"(`lib.rs:1-5`).
- CLI: `src/cmd/` 31개 부명령
  (`add`·`archive`·`backlog`·`config`·`ctl`·`defer`·`due`·`edit`·`hook`·`image`·`init`·`keys`·`link`·
  `merge_driver`·`models`·`mv`·`note`·`prime`·`project`·`read`·`ready`·`rm`·`skill`·`show`·`stats`·
  `status`·`supervise`·`tui`·`update`·`web`·`wiki`).
- 모든 명령에 `--json` 지원: "파서를 다시 짜는 대신 `--json` 소비"(`docs/json.md:1-8`).
- TUI: `src/tui/` 26파일(board·list·gantt·wiki·stats·keys·keymap·zones·layer·sorter·input·form·
  menu·picker·register·scroll·jotfile·mouse·hint·help·init_screen·archive_days 등), ratatui 0.30+crossterm.
  - 비동기 없음: 소유자 스레드가 이벤트 파싱, SIGWINCH만 signal-hook 파이프로 각성(`ctl/serve.rs:37-54`).
- web: 요청마다 **자식 `moai --json` 프로세스를 스폰**해 stdout을 그대로 옮기는 실행기(`web/mod.rs:3-5`).
  - 라우트(web/mod.rs에서 확인): `GET /api/events`(SSE)·`/api/boot`·`/api/stamp`·`/api/rows`·
    `/api/read-all|/api/read/<id>|/api/read-group/<id>`·`/api/plan/<id>`·`/api/image`·`/api/projects`·
    `/api/wiki-search`·`POST /api/ctl/…`.
  - **트래커에 쓰는 POST는 간트 계획 날짜 하나뿐**(`/api/plan/<id>` → 자식 `moai edit --shift*`),
    읽음 표시는 개인 파일, 나머지는 전부 GET(`web/mod.rs:8-13`).
  - SSE: 사건 스레드가 1초마다 표식(스냅샷 mtime/크기) 재확인(`web/mod.rs:4-5`).
  - 예산: 브라우저당 SSE 3개 / 서버 전체 32개(`web/mod.rs:31-35`), 초과 탭은 2초 폴링+30초 재연결 폴백,
    탭 식별은 IndexedDB 랜덤 브라우저 id(`docs/web.md:47`).
  - **CRDT가 아니다**: 표식+폴링 기반이며, 쓰기는 항상 자식 CLI→`store::with_write` 경로.
- ctl: 도는 창에 '주문'을 보낸다(`ls`·`state`·`actions`·`sort`·`group`·`read`·`reload`·`propose`·`answer`·`withdraw`).
  - 제안은 500자·3줄·제어문자 제거·TTL 900s, 수락은 창 주인 신원으로 실행(`docs/ctl.md "Suggest something"`).
  - 사람이 입력/도움말 중이면 `person_busy` — "남의 창을 조용히 고르지 않는다".
- 감독의 `moai watch`: 저널을 2초마다 읽어 이벤트를 만들고, 재개 커서는
  `id + ts + 같은 초 구분용 sha256 다이제스트`(E2BIG 방지, `supervise.rs:10-24, 26-85`).

### 1.3 이슈 JSONL 스키마 (라이브 파일 + `src/model.rs`)

- 한 줄 = 이슈 하나, id 정렬, 키 순서 고정(알파벳 순 금지 — `cut -c1-100`로 파일이 읽히게, `model.rs:213-215`).
- 라이브 예(1-7행): `{"id":"moai-0em3","title":"v0.20.0","kind":"milestone","status":"todo","assignee":"레이븐",
  "assignee_email":"raven@buzzni.com","created_at":"…Z","updated_at":"…"}`.

| 필드 | 의미 | 주석 |
|---|---|---|
| `id` | `moai-<4자 base36>`(36^4) / 자식 `moai-<root>.<3자>`(36^3, 다중 허용, `id.rs` "꼴") | id는 `crate::id::id_of` 하나가 읽는 단일 자(`archive.rs:62-64`) |
| `title` | 제목 | 못 읽는 줄 가리키는 말에 제목 토막 사용(`model.rs:90-113`) |
| `kind` | `issue`(생략 시 기본)·`epic`·`milestone`·`backlog`(저장 꼴 `idea`, `model.rs:136-165`) | **축1 무엇인가**. 새 값을 추가하면 옛 바이너리는 그 줄을 못 읽는다(`model.rs:132-135`) |
| `status` | 자유 문자열(enum 아님, `model.rs:184-190`) | **축2 어디 있나**. `statuses` 설정으로 칸 추가 |
| `tags` | 배열(라이브: `["chore"]`, `["review"]`) | 성격 |
| `assignee`·`assignee_email` | 문자열+이메일 병기(객체화는 옛 줄 파싱 파괴, CLAUDE.md "사람은 이름과 메일로 든다") | |
| `epic`·`milestone` | 상위 참조 | 끊긴 참조는 읽기 관대 — `(없는 에픽)` 묶음 |
| `priority` | 1-3(기본 2=미기재, `model.rs:9-10`) | p0는 도는 마일스톤 밖 핫픽스 슬롯 |
| `created_at`·`updated_at`·`status_since` | RFC3339 UTC | 업데이트·이동 시각을 각각 구분 저장 |
| `started_at`·`done_at` | 첫 시작/마지막 done, **모르면 빈 값**(0·거짓과 구분, `model.rs:301-319`) | 통계는 이 필드만 사용(저널 접지 않음) |
| `due_on`·`starts_on` | 날짜(`"2026-10-28"`) | `due_*` 규칙으로 우선순위 상향 |
| `body` | 마크다운, **유일하게 길 수 있어 항상 맨 뒤**(`model.rs:215`) | 64KB 상한(§3, §4 D1) |
| `deferred_at` | 미룸(**축3** defer) | 미루기를 칸으로 두면 wip/경고가 미룬 것을 세는 실패 회피(CLAUDE.md "축은 셋이다") |

- 저널 레코드(라이브 `.moai/journal/raven_buzzni_com.jsonl`):
  `{"ts":"…Z","id":"…","kind":"create|status|note|rm","by":"…","by_email":"…","title"/"text"/"from"/"to"/"note":…}`.
- **저널 kind는 넷뿐**(필드 변경을 저널로 적지 않음 — 저널이 events.jsonl이 되는 길 차단, CLAUDE.md).
- 파일명이 `by_email`에서 파생 → 경로 안전 경계(SECURITY.md:32-36).
- model line(`model: company/model (grade — why)`)은 `show --json`의 `work[]` 원료 — 저널 종류로 계산하지 않음.

### 1.4 동시성·충돌 해소

- **쓰기 경로 단일화**(`store::with_write`, `store.rs:1095-1147`):
  1. 파일 락(5초, `LockBusy`; `store.rs:18`)
  2. **락 안에서 재읽기**("둘이 같은 옛 상태를 고쳐 쓰면 나중 rename쪽이 앞 이슈를 조용히 지운다", `store.rs:1165-1166`)
  3. 정규화·검증·정렬(바뀐 줄만 검사)
  4. temp+rename 원자 교체
  5. 저널 추가 → 락 해제
- 실패는 `Trouble` 열거형 자료로만(`store.rs:26-50`:
  `DuplicateId`(broken)·`LockBusy`(locked)·`LockUnheld`·`SnapshotOnLock`·`JournalLost`·`ArchiveCleanup`·`ArchiveUnread`·`Invalid`·`NoJournalEmail`).
- **픽업 경합(CAS)**: `moai mv <id> in_progress --from todo` — 본 칸이 이미 달라졌으면
  옮기지 않고 `stale`+비영 종료("남이 집은 일을 뒤늦게 덮지 않는다", CLAUDE.md).
  `defer`·`backlog promote`도 `--from` 허용.
- **머지 드라이버**(`src/cmd/merge_driver.rs`, 필드 단위 3-way):
  - 줄을 id로 짝지어 이슈별 3-way.
  - 한쪽만 바꾼 필드 → 그쪽 / 둘 다 같은 값 → 그 값 / 둘 다 다르게 → `settle` 또는 **충돌**(`merge_driver.rs:16-18`).
  - 미지 필드(rest)도 같은 자로: 새 바이너리 필드를 옛 바이너리가 병합하며 지우면 조용한 손실(`merge_driver.rs:20-21`).
  - 출력은 **원문 보존**(재직렬화하면 키 순서가 바뀌어 멱등성 파괴, `merge_driver.rs:74-83`).
  - 결정 불가 갈래는 표식을 쓰고 비영 종료("표식 없이 실패하면 git이 '충돌'만 말하고
    사람이 `git add` 한 번으로 저쪽을 통째로 버린다", `merge_driver.rs:38-42`).
  - 못 읽는 줄도 id 머리만 성하면 짝짓고, id조차 못 읽으면 **개수로** 푼다(merge_driver.rs:23-31).
  - 설치: `moai merge-driver --install`(클론마다, `.gitattributes:14-15`) — D2.
- **감독은 저장소당 하나**(`SKILL.md:42-48`) + "한 번에 하나씩" — 병렬도는 감독의 선택.

### 1.5 에이전트 연동 실현부 (supervisor → Codex/Antigravity)

- **감독 = Claude Code 세션** 하나 // moai 바이너리는 세션을 띄우지도, 구동하지도, headless로 돌리지도 않는다
  (`supervise.rs:1`; docs/agents.md "moai carries no messaging between agents").
- 스킬(`.claude/moai-plugin/skills/`): moai, moai-wiki, moai-supervise, moai-plan, moai-recover, moai-tmux,
  moai-cmux, moai-saycode.
  - Codex/Antigravity 공용은 `.agents/skills/`, 훅은 `.codex/hooks.json`·`.agents/hooks.json`.
- **할당 카드**(`moai supervise message`, `supervise.rs:130-195`) —
  `Read first: moai show <id>` / `Model:` / `Effort:` / `Worker: codex` / `Subagents:` / `Writing:` /
  `Root branch:` / `Base branch:`(마일스톤 가지 또는 develop) / `Milestone:` / `Due:` / `Root:`(절대경로) /
  `Person:` / `[supervise] extra_lines`((`<id>/<root>` 치환; 오타 키·제어문자·줄바꿈·이중 기재 **거절**,
  `supervise.rs:172-181`).
- **보고 = 노트 운반**: 일꾼(Codex)은 `-C "<root>" note …`로 `Unfolded:`(백로그를 에픽으로, worker-codex.md:28-35)
  → `Review-ready:`(워크트리·가지·merge-base 해시, worker-codex.md:62-71)
  → `report:`(worker-codex.md:88-96) 노트를 **루트 파일**에 남기고 멈춘다.
- **재시작 안전**: `Report-checked:` 마크가 재개 기준 — 감독이 죽어도 노트에서 재개(SKILL.md).
- **모델은 사람 소유**: `[models]`(plan·develop·complex·review·docs·bulk·codex·review_grade·review_raise)
  사용자 설정, 저장소는 `.moai/config.toml`로 권장만(사람 키가 이김, SKILL.md:24-28).
- **리뷰**: 에픽 끝 1회, 범위는 `git merge-base <base> HEAD`부터, 등급은 `review_grade`(+옵트인 `review_raise`),
  원문은 scratchpad `<scratchpad>/<epic>/{brief,review}.md`(에픽별 디렉터리 — 병렬 리뷰어 상호 덮어쓰기 방지).
  "리뷰어의 말은 원문 보존", 훅 규칙 3이 리뷰 이슈+각도+결정 말을 강제.
- **신원**: `--user "Name (email)"` / `MOAI_ACTOR` / git config 순. 없으면 쓰기만 멈춤(읽기는 묻지 않음).
- **라이브니스**: 픽 기록 + 하트비트(`pulse.rs:39-44` safe_sid, `session_lost_minutes`) →
  `lost_session` → `show --json`의 `session:{state, agent, session_id, last_event, last_seen, silent_minutes, recover[]}`
  (`docs/json.md:23-61`) → `moai-recover` 스킬이 죽은 세션 무리를 `claude --resume`으로 되살림.
  - "하트비트 없음 = unknown이지 lost가 아니다"(`docs/json.md:27` — 거짓 값 방지).

### 1.6 Gantt / 산출물·릴리스 생산물

- `crates/moai-gantt`: ratatui 위젯. **moai 타입을 모른다**, "지역 기준 초/날짜"만 받는다(`lib.rs:1-4`).
  - 계획/실제 막대, 의존 link 라우팅(빈 칸에만 선, `lib.rs:130-178`), 드래그(`drag_apply`).
  - `moai web`의 막대 끌기는 자식 `moai edit --shift*`로 실제 날짜를 쓰고 되돌릴 명령(`undo`)을 `--json`으로( `docs/json.md:63-74`).
- i18n: 5언어 JSON + `src/i18n.rs` 번들(include_str!), en 기준·ko 완비·ja/zh 부분·es 최소(i18n/README.md:149-151).
- 웹 번들: `web/`(TypeScript panel) → `scripts/gen-web.sh` → `src/web/dist/` 커밋(컴파일에 node 불필요),
  번들-소스 해시 일치 테스트(`the_web_bundle_is_built_from_these_sources`, i18n/README.md:160-163).
- 릴리스 도구: `bump-version.sh auto|--next`(CHANGELOG `[Unreleased]` 절로 판 결정, `bump-version.sh:1-13, 26-38`) /
  `check-version.sh` / `cross-version-smoke.sh` / `release-posts/<판>/post.md+media/`(태그 전 커밋, compose.py가 부재를
  한 줄로 알리되 막지 않음) / `tag-on-merge.yml`(main 머지+`release` 환경 승인 후 태그) / `release.yml`
  (아티팩트+`SHA256SUMS`) / CI 5잡(dev clippy+test / docs-only 스킵 / MSRV / Windows / 릴리스 20MB 예산, CONTRIBUTING.md).
- 예산: 릴리스 바이너리 20MB·클린 빌드 5분(CLAUDE.md "예산").

---

## §2 협업·규율 모델

### 2.1 무강제(no enforcement) 철학

- README.md:17-18:
  "No approval gate. Anything can be created and anything can be moved.
  Discipline is something `moai status` reflects back at you, never something it enforces."
- `moai status` 보드(README 빠른 둘러보기): `todo 36 ▸ in_progress 5 ? review 0 ✓ done 900 /
  ! Started at once 5 (an epic counts as one)` — **보고문이지 규칙이 아니다**.
- **종료 코드 계약**: 상태 경고는 종료 코드 0.
  - "경고로 비영 종료하는 순간 에이전트가 '실패'로 읽는다. 그러면 이건 린트고, 린트는 곧 게이트다"(CLAUDE.md).
  - 데이터가 깨졌을 때만 비영(중복 id·락 경합 등).
- 훅도 **세션을 절대 실패시키지 않는다** — 규칙을 깬 도구 호출 하나만 거절(docs/agents.md "What the hooks do").
- 경고 목록(`report::status_in`, report.rs:6239): `started_once`·`wip_overload`·`stale_progress`·
  `stranded`(산 워크트리 없음)·`lost_session`·`duplicate_id`(유일 치명)·`backlog_pile`·`unreadable_line`.
  - 전부 `kind`+`notice/fatal`로 출력, 종료 코드 0 유지(부분 문자열 대조 금지, `docs/json.md:60`).

### 2.2 상태·종류·미루기 3축

- **축3**: kind(무엇) / status(어디) / deferred_at(지금 볼 것).
- 묶음의 칸·완료는 멤버에서 읽는 **파생값** — 저장하지 않는다.
  - "복구 명령의 존재 자체가 그 컬럼이 있으면 안 됐다는 자백"(CLAUDE.md — beads의 `recompute-blocked` 실패).
- backlog은 **kind**: board/ready에 안 옴, `backlog promote`가 에픽(+이슈)으로 펼침(원문은 닫힌 채 남음, `Unfolded-from:`).
- 세 포크(AGENTS.md): (1) 지금 할 일 `moai add` / 나중 것 `moai backlog add` /
  (2) 지금 안 할 것 `moai defer <id> -m '<why>'` — **done 위장 금지**(미룬 멤버는 에픽의 목적을 접는 결정) /
  (3) 여러 파일 요청 → 에픽+이슈 3~7개 **계획을 먼저** 보이고 사람 확인 후 코드(감독이 `moai-plan`으로 펼침).
- **담당 없는 줄도 '내 것'이 아니다**: `Owner` 열거(`Theirs`·`Unowned`·`Mine`)로 가르고, 담당 없음도 남의 것
  (`report.rs:4425-4430`).
- 기한: `due_on` → `due_priority` 상향(p1→p0), 상속 기한은 p1까지, `moai due apply`가 미리 적용.

### 2.3 same-tree 공동 작업

- 루트-센트릭: 에이전트는 루트에 서서 절대 경로로 워크트리(`.worktrees/moai-<id>`)를 다룬다.
  - 워크트리에서 부른 moai는 루트 트래커 자동 사용(`worktree.rs:8-11`).
  - 옆 워크트리 스냅샷은 `--worktree` 겹침(display-only) — 파일로 합쳐 쓰면 merge 주의(worktree.rs:1-19).
- 훅 규칙 2: "집은 줄이 모두 워크트리를 가지면 루트 체크아웃 쓰기를 막고 그 파일의 워크트리 경로를 거절문으로."
- 트래커 커밋 규율: 집기·노트·보고·`Next:`는 커밋하지 않음 — **같은 에픽당 닫을 때 한 번**
  (그동안 남 쓴 줄까지 함께, CLAUDE.md "트래커 커밋은 에픽마다 닫을 때 한 번").
  - `prepare-commit-msg` 훅이 `Executed-By: Name <email>` 추가(스쿼시·머지·이미 있는 amend는 건드리지 않음, CONTRIBUTING.md).
- 재시작 안전: 보고·결정·`Next:`·`Decided alone:`가 트래커(노트)에 남는다 → 감독/일꾼 재시작 안전(SKILL.md).
- 병합 후 버그: `Regression-of: <epic id>` 줄 — **어림 금지**("거짓 값은 빈 값보다 나쁘다", CLAUDE.md).

### 2.4 이슈-커밋 연결

- 커밋은 이슈에 **저장하지 않고** git log에서 그때그때 읽는다:
  제목의 `(moai-xxxx)` + 본문 `Refs:`/`Closes:`/`Fixes:` 트레일러(`git.rs:562-573`, `git.rs:631-710`).
- 왜: 트래커 커밋은 제 해시를 미리 모르고, squash·rebase 한 번에 저장본이 낡는다(`git.rs:6-9`).
- 표현: `show`의 commits 칸 / `show --json`의 `commits`·`commits_error`(`no_git`·`not_a_repo`·`stream`·`encoding`·`failed`).
- `git_leaks.rs`(§3)가 "어느 저장소의 이력을 읽는가"를 고정(중첩 저장소 이력 오독 방지, `git_leaks.rs:30-102`).

### 2.5 규율이 테스트로 살아 있는 문화 (드리프트 방지)

- `docs/cli.md`(생성물) ↔ 바이너리 도움말 일치(CLAUDE.md "테스트").
- 번들-소스 해시 일치(`the_web_bundle_is_built_from_these_sources`).
- `moai init --check`가 심은 파일의 stale 탐지, 난이도 표 문자 대조(`the_rubric_is_the_review_table`).
- 멱등성/미지 필드 보존/동시 쓰기 테스트, 명령 스윕(`the_json_sweep_covers_every_command`).
- 결론: 글+훅+테스트 3벌이 같은 결정을 같은 이슈 id(`moai-*`)로 가리킨다 — "한쪽만 고치면 붉어진다".

### 2.6 한 주기(라운드) 진행 — 실제 운영 절차 요약

- 감독 라운드(docs/agents.md "One round" 1-6): `moai ready --json`(내 것) →
  재시작한 감독만 부활→`due apply` → 차분히: 새 에픽은 3~7개 이슈로 펼치고「집은 일 안에」규칙 지킴 →
  일꾼에게 할당 카드 → `moai watch`로 이벤트 대기 → 보고 확인(`Report-checked:`) → 머지/리뷰/닫기.
- 일꾼 절차(worker-codex.md): 할당을 읽음 → `Unfolded:`를 남김(백로그일 때) → 코드 → `Review-ready:`(경로·가지·해시) →
  `report:` → 작업 후 멈춤 → 감독이 도구 세션을 종료.
- 이 주기 전체에서 **moai는 조정만 하고 강제하지 않는다** — 규율은 훅 5규칙+글+노트.
- **moai는 논쟁/토론 기능을 의도적으로 안 가진다**: 배정-보고 단방향. 토론 조율(인수 제안)은
  ctl `propose`·훅 규칙 5(`--take`)가 유일한 '사람 문' — 우리 워크숍의 논쟁 축은 이 어휘만 빌리고 새로 짓는다(§4 D13).

---

## §3 보안·경계

- **기본 노출면**: 이슈 본문·노트·제목 전부 git에 평문 커밋.
  - 저널은 append-only라 한 번 커밋되면 **영영 남는다**(model.rs:14-16 — 에이전트가 리뷰 원문 36~40만 자를 두 번 붙인 사고, `moai-m9a8`).
  - `MAX_TEXT_BYTES=64KB`(model.rs:21), "잘라 적지 않고 거절 — 요약+파일 보존을 지시"(model.rs:19, 35-52).
  - **시크릿 패턴 스캔 없음**(훅 규칙 5개, AGENTS.md) — SECURITY.md:61-62가 "저장소 내용"을 out of scope로 선언 → D1.
- **읽기 경계(`held.rs`, moai-itsu)**: `issues.jsonl -> /proc/self/pagemap` 하나로 모든 명령이 메모리를 다 쓰던 사고(held.rs:1-3).
  - 링크 푼 자리가 체크아웃 밖 / `.git/` 안 → 읽지 않음(`Unheld::Outside/IntoGit`, held.rs:41-51).
  - FIFO는 `O_NONBLOCK`로 열어 열기에서 멈추지 않음(held.rs:10-12); 락은 안쪽 링크조차 안 따름(held.rs:47-51).
  - 실패 원인은 자료(`Unheld`)로 전달 — 판정을 부분 문자열로 하지 않는 기계 표면(fail.rs 계약).
- **git 호출 위생(`git_leaks.rs:44-133`)**: `GIT_DIR`류 24개 + `GIT_CONFIG_PARAMETERS`류 걷기
  (설정 값을 이겨 남의 저장소 저널에 남의 이름을 적는 길 차단, `git_leaks.rs:70-76`).
  - 목록 단일 파일 + git의 `--local-env-vars`와 대조 테스트(주석 34-39, 44-82).
- **웹**: 루프백 기본 + `Origin` 검사(POST 전부, `http.rs origin_ok`; web/mod.rs:13) + CSP·nosniff·no-referrer(http.rs:299-321)
  + 스트림 예산 + 타임아웃 다수(web/mod.rs:30-49) + `--host` 시 "누구나 읽는다" 경고(docs/web.md:24-26).
  - **인증·권한 없음** → D5.
- **업데이트 체크**: GitHub API `tag_name` 한 필드만, 제어문자·비버전 문자열 거절, https 유지, `user:password@` 미기록
  (`latest.rs:1613-1626`; SECURITY.md:43-59).
- **설치**: `SHA256SUMS` 필수(우회 플래그 없음), 기존 moai 확인 후에만 교체, 서명/SBOM은 미구현
  ("trust root is the GitHub release", SECURITY.md:66-90).
- **신원·트러스트**: 신원 자기선언(`--user`/`MOAI_ACTOR`) — 인증 없음 → D6.
  - Codex 훅은 /hooks 트러스트를 사람이 해야 함(트러스트 전 훅 0개).
  - Codex 샌드박스 해제 설정은 커밋 금지 — 문서 규약에 기대는 경계(문서 규약 = 규율, §4 D7).

**경계 요약 표**:

| 경계 | 방어(구현) | 남는 틈 |
|---|---|---|
| 트래커 파일 읽기 | held.rs(링크/FIFO/크기 한정) | 시크릿 내용(0) |
| 쓰기 | 단일 경로+락+재읽기+3-way 머지 | 락이 머신-로컬(D3) |
| git 호출 | git_leaks.rs 환경 걷기+대조 테스트 | 구버전 바이너리 상태(D2) |
| 웹 | 루프백+Origin+CSP+타임아웃 | 인증/권한(0, D5) |
| 신원 | 픽 기록+하트비트(간접) | 자기선언(검증 0, D6) |
| 설치 | SHA256SUMS 필수+실행 확인 | 서명/SBOM 없음 |
| 에이전트 쓰기 | 훅 5규칙(도구 호출 걸림) | `git apply`·`patch`·스크립트 미커버(D7) |

---

## §4 결함·품질 이슈 (실제 확인된 문제, file:line)

- **D1. 시크릿 스캔 부재 + 저널 영구성** (위험: 높음)
  - 본문·노트 평문+append-only(`model.rs:14-16`), 스캔 규칙 없음(SECURITY.md:61-62), 64KB 거절은 요약+파일 보존 안내뿐(`model.rs:35-52`).
  - 워크숍(구현 산출물·생성 콘텐츠가 흐르는 곳)에서 최대 리스크. 우리는 작성 시점 스캔+역사 스캔을 설계에 포함.
- **D2. per-clone 설치·트러스트 의존**
  - `merge=moai`는 `moai merge-driver --install`이 있어야 서고, 미설치 클론은 기본 텍스트 머지로 내려감(속성 무시, `.gitattributes:14-15`).
  - Codex 훅은 /hooks 트러스트 핸들러별 해시가 사람 몫(docs/agents.md "Codex").
  - "안 심은 저장소가 안 심은 것보다 합치기 어려워지는" 쪽으로 설계(merge_driver.rs:28-31) — 의도된 트레이드오프이나 배포 불변식 아님.
- **D3. 락이 머신-로컬(fs2)**
  - shared 볼륨 두 머신 동시 쓰기 = 락이 안 보임(`store.rs:18`, `store.rs:1157-1163`), 같은 필드 동시 변경은 사람 개입(`merge_driver.rs:16-18`).
  - 273줄 규모에선 정당하나, 워크숍 규모(트랙별 병렬)로 키우면 충돌 표식 정리 비용이 는다.
- **D4. 스냅샷 전면 재작성 O(n)**
  - 쓰기마다 전체 temp+rename(`store.rs:1079-1084`); 아카이브(3,708줄)로 경감하나 근본 해결 아님.
  - 쓰기·훅 경로가 매번 재정렬·검증 — 성능보다 "쓰기가 선형+전체 락"이라는 특성.
- **D5. 웹 인증·권한 부재** (위험: 높음)
  - 루프백 기본·`--host` 경고(docs/web.md:24-26)뿐, 탭 식별은 IndexedDB 랜덤 id(진짜 신원 아님, docs/web.md:47).
  - 멀티 사용자 서버 배포 시 전면 노출 + ctl 제안/읽음 남용 표면.
- **D6. 신원 자기선언**
  - `MOAI_ACTOR`/`--user`는 자기선언(docs/agents.md "Who the agent is") — 벤더 에이전트가 남의 이름으로 `by:`/담당 주장 가능.
  - 픽 기록은 '세션'까지 추적(pulse.rs:39-44)하나 검증은 없음.
- **D7. 훅 커버리지 구멍**
  - 규칙 2가 Edit/Write/apply_patch/셸 리다이렉트 모양만 봄 — `git apply`·`patch`·스크립트 내부 쓰기 **미커버**
    ("not seen (moai-qrja)", docs/agents.md 규칙 2). 장치 외 구간의 책임이 글에 전가.
- **D8. 스키마 전방 호환 한정**
  - 새 `kind` 값은 옛 바이너리가 못 읽으며 **접지 않을 뿐**(모르는 값으로 접으면 되쓰기 한 번에 소실, `model.rs:132-135`).
  - 보호는 미지 필드 보존 테스트+전체 보존뿐 — 혼재 버전 클라이언트에는 스큐 계획 필수.
- **D9. 64KB 상한 = 정보 손실 지점**
  - "잘라 적지 않고 거절"(원문 일부 소실 방지, model.rs:19) — 초과분 요약+파일 보존 지시(`model.rs:42-45`).
  - `moai edit <id> -b`는 본문 통째 교체 → 붙임 실수 시 유실 위험(AGENTS.md "이미 있는 이슈에 붙일 때").
- **D10. 다국어 불완전**
  - ja/zh/es 부분(i18n/README.md:149-151; ja/zh 혼재, es 페이지 키 없음), 코드 박힌 한국어 자리 다수(i18n/README.md:101-147).
  - 번역 키 엄격 테스트(en 기준·ko 완비)로 관리되나 다국어 자체는 미완성.
- **D11. 저장소 규약 승격 위험**
  - `[supervise]` 멀티라인 배열은 v0.49 이전 바이너리에서 **모든 명령을 거절**(CHANGELOG v0.50.0:39).
  - '모든 moai 바이너리 업그레이드 완료' 전제의 배포 정책 취약 — 워크숍 규칙 파일 승격 금지 학습점.
- **D12. 3벌(글·훅·테스트) 유지 부담**
  - doc-구현 문자 대조·명령 스윕·번들 해시 — 규율이 테스트로 승화된 만큼 비용도 비선형(저지속 부채).
- **D13. 전제의 경계 (워크숍 적용 시 명시)**
  - "한 저장소=한 소유/신뢰 영역"·"사람이 감독"·"한 머신"이 전제 — 논쟁(인수)·멀티 주체 권한은 moai가 **의도적으로 안 가진 축**.
  - 우리는 '조용한 손실 방어+규율-테스트 문화'만 빌리고 조정 계층을 새로 짓는다(§5, §6).

---

## §5 개선점 도출 (대상: 다중-벤더 AI 에이전트 토론+코딩+산출물 워크숍)

적용 대상: TalkAbout(FastAPI+PostgreSQL, 룸 단위 토론, analysis/change_proposal(unified diff) 버전 산출물,
실행 예약·영수증, 웹 GUI, 옵트인 워크 프로필, 멀티 벤더). 형식: **[파일:줄 → 좋은 점 → 우리 적용안] (즉시/중기)·영향·비용·위험**.

### 5.1 즉시 적용

1. [store.rs:1095-1147 → 단일 쓰기 경로+락+락 안 재읽기+원자 교체+'스냅샷 먼저 저널 나중']
   → TalkAbout 실행 예약·산출물 쓰기를 '트랜잭션→락→락 내 재조회→검증→커밋→영수증 기록' 헬퍼 하나로 통일.
   '저널 유실=이력 공백 알림'(실패 아님, store.rs:1082-1084) → 영수증 기록 실패를 치명이 아닌 알림으로.
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
2. [merge_driver.rs:11-21 → "조용한 손실 금지 + 필드/헝크 단위 3-way + 미지 조각 보존 + 결정 불가는 사람"]
   → change_proposal 병합 정책: '기준 대비 양쪽 변경 탐지'(1단계 자동 해소는 중첩 없는 헝크만), 늦게 쓴 쪽 승리 금지.
   (즉시=진단 / 중기=헝크 단위 엔진 / 영향 중간 / 비용 높음 / 위험 중간)
3. [report.rs:4361-4420 + report.rs:4422-4442 → ready/others/held 3분류, 담당 없음도 '내 것'이 아님, 밀린 일도 노출]
   → 워크숍 픽업 큐 API(ready/others/held) + '도는 라운드 밖은 p0만' 규칙을 큐 한 곳에만 + 밀린 일 노출.
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
4. [supervise.rs:130-195 + worker-codex.md → 할당 카드(모델·베이스·마일스톤·루트·from) + 노트 운반 보고 + `Report-checked:` 재개]
   → TalkAbout 배정 카드(Vendor/Model/Effort/Due/Base/Milestone/Root/from) + 진행자 재시작 안전.
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
5. [git.rs:562-573, 631-710 → 인용·파생값을 저장하지 않고 질의 시 계산]
   → 산출물-커밋/메시지 인용 DB 저장 금지(원본 레코드 id만), 인용 사본은 squash·rebase·복제에서 낡는다(git.rs:6-9).
   (즉시(원칙)+중기(마이그레이션) / 영향 중간 / 비용 낮음 / 위험 낮음)
6. [model.rs:35-52(fit_bytes, '잘라 적지 않고 거절') → 발언/보고 상한 + 초과는 참조(스토리지 파일 링크) 보존]
   → 토론 발언 크기 제한+원문 아카이브 참조 규칙+로그 제목 토막 표시(대화록 JSONL 통째 붙이기 방지).
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
7. [pulse.rs:1-16 + docs/json.md:23-61(하트비트, unknown≠lost, recover[] 생성) → 실행 예약 last_seen+state+복구 절차 문장]
   → TalkAbout 영수증/예약에 '라이브 아님' 표시+복구 커맨드. (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
8. [docs/json.md:1-8(파서 재구현 금지, --json 소비) + CLAUDE.md '테스트'(바이트 비교·동시 쓰기 테스트 보존·명령 스윕)
   → TalkAbout API DTO-내부 모델 분리+회귀를 바이트/스냅샷 고정+규칙을 DTO 생성기 하나로].
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)
9. [i18n/README.md:1-30(빈 값 금지·`{n}` 자리 보존·새 키 감지) + 번들-소스 해시(160-163)
   → 워크숍 UI/문서 다국어 관리 테스트 4개 선행(ko 우선이면 en 기준+ko 완비부터)].
   (즉시 / 영향 낮음 / 비용 낮음 / 위험 낮음)

### 5.2 중기 적용

10. [web/mod.rs:3-13 + http.rs:321(SSE 1초 표식+폴백, 자식 실행기) → 워크숍 워크 보드 '뷰 버전 SSE+쓰기 단일화+브라우저별 예산']
    (중기 / 영향 중간 / 비용 중간 / 위험 중간)
11. [git_leaks.rs:44-133(환경변수 화이트리스트 단일 파일+`--local-env-vars` 대조) → 벤더 CLI 실행 시 전달 변수 화이트리스트+추가 시 대조 테스트]
    (워크 프로필 실행 경로 필수 / 중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
12. [CLAUDE.md '모델 역할'+model line(vendor/model(grade — why), 토큰 없음) → 산출물·발언에 vendor·model·grade 필수+영수증에 실행 모델]
    (중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
13. [bump-version.sh:26-38 + tag-on-merge.yml + release-posts/미디어-비차단 → 워크숍 '판본 발행' 워크플로우(변경 이력이 판 결정, 일치만 기계)]
    (중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
14. [D2/D7 교훈 → '장치 미적용 상태를 status가 반드시 경고' 원칙 + 워크숍 가이드 '설치 확인' 필수 단추 — 막지 않되 반드시 알림]
    (중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
15. [archive.rs:74-79(id 예약) + `Regression-of:`(CLAUDE.md) → 산출물 회귀 추적(삭제 대신 보관+버전 고정 id)+원인 버전 표식 강제]
    (중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
16. [dogfooding(도구가 자기 개발을 스스로 관리) → TalkAbout '모범 룸' 1개 pilot — 규율이 파일/테스트로 사는 이유를 실측]
    (중기 / 영향 중간 / 비용 낮음 / 위험 낮음)
17. [D5/D6/D1 대응(커스텀) → 웹 세션 인증+룸/산출물 ACL+ctl(제안·읽음) 권한+작성 시점 시크릿 스캔+산출물 diff 경고]
    (중기 / 영향 높음 / 비용 중간 / 위험 높음 — D5 대응)

### 5.3 우선순위 요약 표

| 순위 | 항목 | 근거(파일:줄) | 시기 | 영향/비용/위험 |
|---|---|---|---|---|
| 1 | 단일 쓰기 경로+락·영수증 | store.rs:1095-1147 | 즉시 | 낮/낮/낮 |
| 2 | ready/others/held 큐+규칙 단일화 | report.rs:4361-4420 | 즉시 | 낮/낮/낮 |
| 3 | 인용·파생 저장 금지 | git.rs:562-573, 631-710 | 즉시(원칙) | 중/낮/낮 |
| 4 | 발언 상한+참조 보존 | model.rs:35-52 | 즉시 | 낮/낮/낮 |
| 5 | 하트비트+unknown/lost | pulse.rs, docs/json.md:23-61 | 즉시 | 낮/낮/낮 |
| 6 | --json DTO+회귀 스냅샷 | docs/json.md:1-8 | 즉시 | 낮/낮/낮 |
| 7 | 배정 카드+노트 운반 보고 | supervise.rs:105-195 | 즉시 | 낮/낮/낮 |
| 8 | SSE 표식 보드 | web/mod.rs:3-13 | 중기 | 중/중/중 |
| 9 | 3-way diff 병합 | merge_driver.rs:15-18 | 중기 | 중/높/중 |
| 10 | 시크릿 스캔·웹 ACL | SECURITY.md:61-62, docs/web.md:24-26 | 중기 | 높/중/높 |

---

## §6 미확인·한계

- **빌드/실행 안 함**: Rust workspace(edition 2024, MSRV 1.88, LTO) 빌드·테스트(1,500+개)·TUI/웹 실화면·실머지·설치 실행 not performed.
  - 전부 정적 분석. `docs/cli.md`(생성물)·`src/web/dist/`(번들)는 생성 파일이라 전수 대조 없음.
  - `hook.rs`·`report.rs`·`merge_driver.rs`(수천~만 줄) 중 핵심 경로만 직접 읽음.
- **git 전수 검증 제한**: 6,693커밋·1,238머지·60태그는 수치·샘플링, 원격 가지 20개는 이름만.
  - 첫 12커밋·HEAD 표본·머지 패턴(--no-ff, `moai-*` 제목)·커밋 주체 혼재 관측 — 이슈 id 참조 밀도 전수 분석은 수행 안 함.
- **CRDT/동시성 실측 없음**: '1초 표식'(web/mod.rs:4-5)·'락 안 재읽기'(store.rs:1165-1166)는 코드 확인;
  동시 7세션·NFS/공유 볼륨·동시 브라우저 탭 실측 없음. 머지 드라이버 settle 규칙(merge_driver.rs:771 이후) 전수 대조 없음.
- **외부 운영 미관측**: GitHub Actions 실행 로그·Discord·릴리스 자산 서명·실 감독 세션 없음. `scripts/site-demo` 데모 클립 미시청(이진).
- **한글 인코딩**: 콘솔 출력 일부가 콘솔 코드페이지 때문에 깨져 보였으나 파일 자체는 UTF-8 확인 — 인용은 UTF-8 원문 기준.
- **미확인 명령**: `moai tui` 키 전수·`moai skill install` 심기 전수·`moai recover` 절차·`moai stats`·위키 검색·watch 2초 폴링 부하.
- **후속 권고**: (1) D1 실사(히스토리 시크릿 스캔) (2) 락 경합 실축(랜덤 동시 mv) (3) SSE 예산 초과 동작
  (4) `moai ready --json` 필드 전수 문서화 (5) `prepare-commit-msg`·`pre-push` 실행 경로 확인.

---

## 부록 A. 분석에 사용한 주요 표본 (읽기 목록)

- 핵심 소스: `src/store.rs`(쓰기 경로·락·Trouble)·`src/model.rs`(스키마)·`src/cmd/merge_driver.rs`(머지)·
  `src/report.rs`(ready·status·Owner)·`src/git.rs`(커밋 연결)·`src/git_leaks.rs`(환경 위생)·
  `src/held.rs`(읽기 경계)·`src/web/mod.rs`+`src/web/http.rs`(웹)·`src/ctl/serve.rs`(TUI·ctl)·
  `src/supervise`/`src/cmd/supervise.rs`(감독)·`src/pulse.rs`(하트비트)·`src/lib.rs`·`src/id.rs`·`src/archive.rs`·
  `.moai/config.toml`·`.moai/issues.jsonl`·`.moai/journal/raven_buzzni_com.jsonl`·`.moai/archive/2026.jsonl`.
- 문서: README·CLAUDE.md·AGENTS.md·docs/{agents,ctl,web,json,recovery,workflow,explorer,images,sql,windows,glossary}.md·
  SECURITY.md·CONTRIBUTING.md·i18n/README.md·skill(moai-supervise·worker-codex·moai-tmux·moai-cmux)·release-posts/(구조)·
  scripts/{bump-version,compose}.py·.gitattributes·.github/workflows/*.yml·CHANGELOG.md(샘플).
- 통계 명령: 커밋/머지/태그 수·원격 가지 목록·`src/`·`src/cmd/` 파일 목록·`git log --oneline -40`.
