# 공유 브리프 (2라운드) — Orca · opencodex · hermes-agent 3개 저장소 정밀 분석

## 목표
아래 3개 저장소를 정밀 분석해 **TalkAbout 프로젝트에 실제로 도움이 되는 것**을 근거와 함께 찾는다.
Orca는 1라운드에서 이미 전수 분석을 마쳤다(아래 §3 요약). 2라운드의 초점은 **opencodex와 hermes-agent**이며,
Orca 결론과 **비교**해 "어느 저장소에서 무엇을 가져오는 게 최선인가"를 판정하는 것이다.

## 1. 분석 대상

| 저장소 | clone 경로 | HEAD | 스택 | 정체 |
| --- | --- | --- | --- | --- |
| `stablyai/orca` | `orca-analysis/` | `51dcef8` (2026-10-25) | TypeScript/Electron | AI Orchestrator — 45종 agent CLI를 worktree별 병렬 실행·감독 |
| `lidge-jun/opencodex` | `opencodex/` | `9f0d40a` (v2.92.0, 2026-10-24) | TypeScript/Bun | **universal provider proxy** — Codex·Claude Code·Claude Desktop·Grok Build가 "아무 LLM"을 쓰게 만든다 (`ocx start`) |
| `NousResearch/hermes-agent` | `hermes-agent/` | `930e5ed` (2026-10-25) | **Python** 3.14 | 자가 개선 AI 에이전트 — TUI·게이트웨이(Telegram/Discord/Slack/WhatsApp/Signal)·스킬 자동 생성·cron·서브에이전트·6종 터미널 백엔드. MIT |

모두 `D:\AI-Codding\Claude\TalkAbout\` 아래에 clone되어 있다.

### 1.1 opencodex 구조 (실측)
- 211MB, 12,883파일. `.md` 5,305 / `.ts` 5,300 / `.tsx` 417.
- `.md`의 5,305개는 **`devlog/`** 에 몰려 있다(개발 일지). `docs-site/` 456, `structure/` 243, `skills/` 14.
- `src/`: `adapters`, `advisor`, `bridge`, `chat`, `chatgpt`, `claude`, `cli`, `client`, `clients`, `codex`, `combos`, `companion`, `compatibility`, `config`, `generated`, `github`, `grok`, `images`, `integrations`, `lab`, `lib`, `link`, `messaging`, `oauth`, `plugins`, `protocols`, `providers`, `quota`, `quota-pool`, `remote`, …
- 최상위: `app/`, `desktop/`, `gui/`, `native/`, `bin/`, `docker/`, `scripts/`, `tests/`, `readme/`, `assets/`.
- 핵심 가치 가설: **codex/claude/grok CLI의 와이어 프로토콜·OAuth·스트리밍을 프록시 수준에서 다룬다**. TalkAbout의 `cli` transport·native 인증·quota 문제에 직결.

### 1.2 hermes-agent 구조 (실측)
- 223.9MB, 21,220파일. `.py` 10,181 / `.ts` 3,617 / `.tsx` 1,441 / `.md` 1,645 / `.yaml` 1,043.
- 최상위가 **플랫 모듈 약 80개**: `hermes_state*.py`(상태 계층, 70여 개), `hermes_logging.py`, `hermes_startup_watchdog.py`, `hermes_dir_acl.py`, `hermes_time.py`, `hermes_yaml.py`, `toolsets.py`, `toolset_distributions.py`, `model_tools.py`, `run_agent.py`, `cli.py`, `batch_runner.py`, `mini_swe_runner.py`, `trajectory_compressor.py`, `registration_lifecycle.py`, `utils.py`.
- 주요 디렉터리: `acp_adapter/`(20파일, `server.py` 57KB·`gateway_server.py` 69KB·`permissions.py`·`edit_approval.py`·`elicitation.py`·`model_catalog.py`·`provenance.py`·`events.py`), `agent/`, `apps/`, `cron/`, `docker/`, `evals/`, `gateway/`, `hermes_cli/`, `hermes_platform/`, `locales/`, `native/`, `nix/`, `optional-mcps/`, `optional-skills/`, `plugin-catalog/`, `plugins/`, `pm/`, `providers/`, `scripts/`, `skills/`, `tests/`, `tests-js/`, `tools/`, `tui_gateway/`, `ui-tui/`, `web/`, `website/`.
- 루트 문서: `AGENTS.md`, `SOUL.md`, `SECURITY.md`, `README*.md`(zh/ur/es), `CONTRIBUTING*.md`.
- 핵심 가치 가설: **TalkAbout과 같은 스택(Python)에서 ACP·provider·상태계층·게이트웨이를 실제로 구현한 유일한 참조**. 특히 `acp_adapter`는 ACP **서버** 쪽 구현이다(TalkAbout은 ACP **클라이언트** 쪽에서 막혀 있음).

## 2. 분석 기준 프로젝트 (TalkAbout)
`D:\AI-Codding\Claude\TalkAbout` — **초대 기반 사적 공간에서 각자 자기 컴퓨터의 외부 AI 에이전트(Codex CLI, Claude Code, Grok)가 사람 소유자 통제 아래 토론하고 함께 글을 쓰는 서버**. Python + PostgreSQL.

핵심 설계(정본 `docs/ARCHITECTURE.md`, 상태 정본 `docs/STATUS.md`, 격차표 `docs/RELEASE_GAP_MATRIX.md`):
- 플랫폼은 모델을 보유·실행하지 않는다. 참가자가 자기 환경·자기 구독으로 agent를 돌린다.
- Event가 진실 원천. per-room `last_event_seq` + room row lock. revision 다층(`identity`/`room`/`generation`/`membership`/`membership_set`/`artifact`).
- 서버 `TurnScheduler`가 round-robin `speak` task 생성 → claim(lease) → **runtime 1회 호출** → complete. lease/deadline/attempt 예산 → expired, generation 무효화 → cancelled.
- 외부 `reference pull worker`가 journal transaction으로 event 수신·cursor·실행 queue를 원자 저장. **LLM이 cursor·heartbeat·idempotency를 관리하지 않는다.**
- 권한 = 계정 scope ∩ membership ∩ credential scope. `docs/PERMISSIONS.md` 카탈로그를 모든 읽기/쓰기/SSE에 적용.
- "등록됨 / 연결됨 / 실제로 발언함"은 다른 증거. 합성 테스트를 실제 공급자 검증으로 표시하지 않는다.
- 현재 남은 관문(`docs/STATUS.md:44-52`): **실제 2-runtime 대화(S2)**, **Grok 기본 lease·반복 턴(ACP 응답 ID 불일치)**, 운영 배포, MFA·분산 rate limit, paging/archival/disk quota, binary upload(P2), **POSIX 실행 차단**, S4(격리 명령/MCP) 미연결.

관련 모듈: `providers.py`(`cli`/`http` transport registry), `adapters.py`, `agent_runtime.py`(codex app-server·claude stream-json·grok ACP), `http_runtime.py`, `codex_sessions.py`, `connector.py`, `runtime.py`, `processes.py`(Windows Job Object만), `worker.py`(SQLite journal), `participation.py`, `modes.py`, `domain.py`, `core.py`, `store.py`, `api.py`, `auth.py`, `identity.py`, `bounds.py`, `tool_broker.py`(fake only, 미배선), `mcp.py`(MCP 서버 표면), `attachments.py`, `text_files.py`, `provision.py`, `retention.py`, `trial_*.py`, `web/`(자체 CSS·인라인 SVG·CDN 없음).
검증: Python 636 + Node 196 통과. 실제 공급자 S1은 Codex/Claude/Grok 각각 통과, S2는 NOTRUN.

## 3. 1라운드(Orca) 결론 — 중복 분석 금지, 비교 기준으로만 사용
상세: `analysis/00-SYNTHESIS.md` 및 `01~05`. 핵심만:
- **가져올 것**: ACP 프레임 규율(숫자 id만, 불일치 id는 무시+유한 타임아웃, 초과 라인 개별 정산), 실패·한도 어휘 타입화(`usage_limit_reached{window,resetsAt}`), capability 선언↔구현 검증, MCP 비파괴 설치(dry-run digest·못 읽으면 중단), stall 감지, 완료 통지(5초 쿨다운·포커스 억제·unread 선기록), secrets redaction 단일 choke point, daemon respawn throttle(5회/60초)·shutdown 상한(5초), turn 상관 ID+JSON 로그.
- **TalkAbout이 이미 우위**: Event 순서·revision 다층, pull journal(`call_started→uncertain`), stderr 허용목록, 자식 env allowlist, Windows Job Object, tool fail-closed, 합성↔실제 증거 분리, ko/en parity 테스트, cursor+snapshot rebase.
- **제외**: PTY 스크롤백·상주 daemon·원격 실행·worktree·Electron UI·blocking ask/reply·`src/main/memory`(RAM 통계).
- **1라운드에서 발견한 TalkAbout 결함 6건**(`analysis/00-SYNTHESIS.md:§7`): journal 락 오류→stop 승격(`worker.py:391-400`), 무제한 `shutdown()`(`runtime.py:51-59`), ack이 배달 증거 아님(`participation.py:73-76`), reduced-motion CSS 오염(`web/style.css:1692-1709`), 실행 전 실패가 attempt 소모(`domain.py:117-125`), 관측 계층 부재(`logging` import 0건).
- **정정 4건**(같은 문서 §8): Orca는 프롬프트 마스킹을 하지 않는다/교차 자격증명은 활성 누출 아님/완료 통지는 SSE와 별개 문제 등.

**2라운드에서 Orca를 다시 파지 말 것.** 단, 아래 경우에는 Orca를 인용해도 된다: (a) 다른 저장소가 Orca보다 **명확히 낫다**고 판정할 때, (b) 1라운드 결론이 **틀렸다**고 판단될 때(그러면 근거와 함께 명시).

## 4. 보고 규칙 (반드시)
1. **근거 우선**: 모든 주장에 저장소 루트 기준 상대 경로 + 라인(`hermes-agent/acp_adapter/server.py:120`). 짧은 코드 인용 허용. 인용은 원문 그대로.
2. **추측 금지**: 확인 못 한 것은 "미확인"으로. 존재를 추정한 파일을 근거로 쓰지 않는다. grep 기반 부재 주장은 "찾지 못했다"로 한정.
3. **선별**: TalkAbout이 이미 더 잘하는 것은 `[대조]`로만. **TalkAbout에 없는 것·약한 것**을 우선.
4. 판정 태그: `[채택]` 바로 쓸 수 있음 / `[차용]` 개념만 / `[대조]` 이미 우위 / `[제외]` 맞지 않음(이유 필수).
5. 각 항목에 **구현 비용** S(≤1일)/M(2~5일)/L(1주+)와 **TalkAbout 부착 위치**(실존 파일만), 그리고 **어느 저장소가 최적인지**(orca/opencodex/hermes 중 택1 + 이유).
6. 출력은 **한국어**. 코드 식별자는 원어 유지.
7. 라이선스: orca MIT(`orca-analysis/LICENSE`), hermes-agent MIT(`hermes-agent/LICENSE`), opencodex는 `opencodex/LICENSE`를 **직접 확인**해 명시할 것. 외부 코드 복사 시 고지 필요.
8. 보고서 300~700줄. 표·짧은 코드블록 적극 사용. 서로 모순되면 모순이라고 적는다.
9. 자기 보고서 파일만 쓴다. 다른 팀원 산출물·분석 대상 저장소를 수정하지 않는다(clone은 read-only).
10. **devlog/ 5,305개 같은 대량 문서는 전수 읽지 말 것** — 목차·최근 항목 표본으로 가치만 판정하고 근거를 밝힌다.

## 5. 산출물
`analysis/` 아래 지정된 파일 1개. Lead가 `analysis/r2-00-CROSS-REPO.md`로 통합한다.
