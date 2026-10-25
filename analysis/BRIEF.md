# 공유 브리프 — Orca 저장소 정밀 분석

## 목표
`https://github.com/stablyai/orca.git` (clone: `D:\AI-Codding\Claude\TalkAbout\orca-analysis`, HEAD `51dcef8`, 2026-10-25)를 분석해
**TalkAbout 프로젝트에 실제로 도움이 되는 것**을 근거와 함께 찾는다. 일반적 감상·요약은 가치 없음.

## 분석 대상 저장소 (Orca)
- Electron/TypeScript 데스크톱 앱 + CLI + 모바일. "The AI Orchestrator for 100x builders."
- Codex / ClaudeCode / OpenCode / Pi / Cursor / Gemini / Grok / Droid / Amp / Copilot / Qwen / Kimi / Kiro / Mimo / Minimax / Muse / Zcode 등
  **다수 agent CLI를 worktree 단위로 병렬 실행·감독**한다.
- 규모: 296MB, `.ts` 31,889 / `.tsx` 4,103 / `.md` 152 / `.mjs` 1,119. `src/` 31,949 파일.
- 주요 경로:
  - `src/main/<provider>/` — 제공자별 통합 (codex, claude, grok, opencode, gemini, cursor, …) 및 `providers/`, `provider-process/`, `agent-launch/`, `agent-hooks/`, `acp/`, `jsonl-rpc/`, `pty/`, `runtime/`, `credentials/`, `claude-accounts/`, `codex-accounts/`, `artifacts/`, `skills/`, `memory/`, `notifications/`, `server/`, `managed-server/`, `orchestration/`, `automations/`, `persistence/`, `sqlite/`, `host/`
  - `src/cli/handlers/orchestration/` — worker 디스패치·inbox·gate·question·keepalive CLI
  - `src/renderer/`, `src/preload/`, `src/shared/`, `src/types/`, `src/packages/process-host/`
  - `skills/`, `skill-guides/`, `skill-stubs/` — 에이전트에게 배포하는 스킬
  - `docs/`, `.claude/`, `AGENTS.md`, `orca.yaml`, `.mcp.json`, `cloud/`, `mobile/`, `native/`, `examples/`, `tests/`, `config/`

## 분석 기준 프로젝트 (TalkAbout)
`D:\AI-Codding\Claude\TalkAbout` — **초대 기반 사적 공간에서 각자 자기 컴퓨터의 외부 AI 에이전트(Codex CLI, Claude Code, Grok)가
사람 소유자 통제 아래 토론하고 함께 글을 쓰는 서버**. Python + PostgreSQL.

핵심 설계(정본 `docs/ARCHITECTURE.md`):
- 플랫폼은 모델을 보유·실행하지 않는다. 참가자가 자기 환경·자기 구독으로 agent를 돌린다.
- 단일 앱 API + 서버 worker + PostgreSQL. Event가 P0 pull의 진실 원천. per-room `last_event_seq` + room row lock으로 commit 순서 보장.
- revision 다층: `identity_revision`, `room_revision`, `generation`, `membership_revision`, `membership_set_revision`, `artifact_revision`.
- 서버 `TurnScheduler`가 round-robin으로 `speak` task 생성 → assignee 고정 → claim(lease) → **runtime 1회 호출** → complete → 다음 슬롯.
  lease 만료/deadline/attempt 예산 → expired. generation·membership_revision 무효화 → cancelled.
- 외부 `reference pull worker`가 journal transaction으로 event 수신·cursor·실행 queue를 원자 저장. **LLM이 cursor·heartbeat·idempotency를 관리하지 않는다.**
- 권한은 계정 scope ∩ 방 membership ∩ credential scope 교집합. `docs/PERMISSIONS.md` 카탈로그를 모든 읽기/쓰기/SSE에 적용.
- "등록됨 / 연결됨 / 실제로 발언함"은 서로 다른 증거. 합성 테스트를 실제 공급자 검증으로 표시하지 않는다.
- P0는 text artifact만 불변 version. binary upload는 P2. MCP adapter는 후보.

관련 모듈(대략): `providers.py`(제공자 registry, `cli`/`http` transport), `adapters.py`, `agent_runtime.py`, `http_runtime.py`,
`codex_sessions.py`, `connector.py`, `runtime.py`, `worker.py`, `participation.py`, `modes.py`, `domain.py`, `core.py`, `store.py`,
`api.py`, `auth.py`, `identity.py`, `bounds.py`, `processes.py`, `provision.py`, `retention.py`, `tool_broker.py`, `mcp.py`,
`attachments.py`, `text_files.py`, `trial_*.py`, `web/`(자체 CSS+인라인 SVG, CDN·웹폰트 없음).
Python 테스트 636 / Node 196 통과. 실제 공급자 모델 응답은 부분 통과(Codex 0.164.0 발언 2건·재연결).

## 보고 규칙 (반드시 지킬 것)
1. **근거 우선**: 모든 주장에 `orca-analysis/` 기준 상대 경로 + 가능하면 라인 번호(`src/main/codex/foo.ts:120`)를 붙인다. 짧은 코드 인용 허용. 인용은 원문 그대로.
2. **추측 금지**: 확인 못 한 것은 "미확인"으로 표시. 존재한다고 추정한 파일을 근거로 쓰지 않는다.
3. **선별**: Orca에 있는 것이 전부 옮길 가치가 있지는 않다. TalkAbout이 **이미 더 잘하는 것**(Event 순서, revision 다층, pull worker journal)은
   "대조군/개선 아이디어"로만 쓰고, **TalkAbout에 없는 것·약한 것**을 우선한다.
4. 각 항목에 판정 태그: `[채택]` 바로 쓸 수 있음 / `[차용]` 개념만 참고 / `[대조]` Orca가 못하는 것을 TalkAbout이 이미 함 / `[제외]` 맞지 않음(이유 필수).
5. 각 항목에 **구현 비용** (S/M/L)과 **어디에 붙일지** (TalkAbout 파일/모듈 지목)를 적는다. 파일명은 실제 존재하는 것만.
6. 출력은 **한국어**. 코드 식별자는 원어 유지.
7. Orca 라이선스는 MIT(`LICENSE`). 외부 코드 복사 시 저작자 표시·라이선스 고지 필요 — 해당 시 명시.
8. 보고서는 300~700줄. 표·짧은 코드블록을 적극 사용. 결론이 서로 모순되면 모순이라고 적는다.
9. 작업 범위 밖 파일(다른 팀원의 보고서)은 수정하지 않는다. 자기 보고서 파일만 쓴다.

## 산출물
`analysis/` 아래 지정된 파일 1개만 쓴다. 최종 Lead가 통합한다.
