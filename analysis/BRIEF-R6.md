# 공유 브리프 (6라운드) — artiboard · session-peer · moai 3개 저장소 **협업 작업장** 정밀 분석

## 0. 목표

이번 라운드는 외부 저장소 3개를 정밀 분석해, **"서로 다른 업체의 AI 에이전트끼리 토론도 하고 코딩도 하면서 산출물을 공동으로 작업하는 작업장"**을
만드는 데 쓸 개선점을 근거와 함께 도출한다. 기능·계약·보안·성능·운영 전반을 보되, **판정 기준은 위 목표**이다.

산출물은 두 층이다.

- **(A) 각 저장소 자체의 정밀 분석 + 결함** — 메인테이너에게 보낼 수 있는 수준의 구체 진단(파일:라인 근거).
- **(B) 대상 작업장 적용안** — TalkAbout(다중 업체 에이전트가 방에서 토론·산출물(분석/통합 diff) 제출·opt-in 작업 프로필)에
  어떤 메커니즘/계약/설계를 가져올지, 즉시/단기/중기로 구분.

## 1. 분석 대상 (2026-11-05 풀 클론 — git 이력 포함)

| 저장소 | 로컬 경로 | HEAD(2026-11-05) | 스택 | 정체 |
| --- | --- | --- | --- | --- |
| `yulmucoffee/artiboard` | `.analysis-repos/artiboard` | `b7ff343` (main) | Node ≥22 · MCP SDK · Supabase/SQLite · zod | 사람+코딩 에이전트 공용 태스크 보드 — claude.ai artifact 하나로, 또는 자체호스팅 + Claude Code/Codex용 MCP 서버 |
| `abruption/session-peer` | `.analysis-repos/session-peer` | `902a5fe` (main, v1.4.2) | Python 3.9+ · websockets · cryptography · MCP | Claude Code·Codex·Antigravity 세션 발견·메시징 — 로컬/SSH/MCP/E2E 암호화 릴레이 |
| `buzzni/moai` | `.analysis-repos/moai` | `6f0772eb` (develop) | Rust · TUI + web · Gantt crate · 저장소-내재 `.moai/` | "Agile, for your coding agents" — 저장소 안에 사는 이슈 트래커, Claude Code supervisor → Codex/Antigravity worker, 강제 없는 규율 |
| `openclaw/openclaw` | `.analysis-repos/openclaw` | `40b34f2f4` (main, depth-1) | TS/NestJS 계열 | 에이전트 플랫폼 — `memory-core` 드리밍(Light→REM→Deep)이 외부 조사 대상. 점별 검증 보고서 참조 |
| `NousResearch/hermes-agent` | `.analysis-repos/hermes-agent` | `8e7cf55e` (main, depth-1) | Python | 메모리 제공자 훅·`write_approval`·세션 종료 추출. R2와 다른 **메모리 각도**만 검토 |
| `CopilotKit/openmuse` | `.analysis-repos/openmuse` | `a4944f4` (main) | TypeScript · CopilotKit + AG-UI | 개인 에이전트 — 브라우저·터미널·파일, 계속 도는 작업, 인간 승인 |
| `wolfqing/OpenGrokBot` | `.analysis-repos/opengrokbot` | `cf6f17c` (main, depth-1) | TypeScript · gateway + teammate | 자체호스팅 AI 팀원 — teammate별 컴퓨터(브라우저), BYOK 모델, 작업 루프 |

**라이선스(2026-11-05 GitHub API 확인): 5개 전부 MIT. 코드를 복사하지 않는다(구조·스키마·프로토콜 참조만). 사용자가 요청한 '라이선스 문제 시 완전히 새로운 로직으로 구현' 원칙: MIT이므로 원 소스를 참고해 TalkAbout 규약에 맞게 새로 구현한다.**

## 2. 기준 프로젝트 (TalkAbout)

`C:\AI-Codding\claude\TalkAbout` — 초대 기반 사적 공간에서 각자 자기 컴퓨터의 외부 AI 에이전트(Codex CLI·Claude Code·Grok)가
사람 소유자 통제 아래 토론·산출물을 만드는 서버. Python 3.12 + FastAPI + PostgreSQL.
이미 정해진 사실(추측 아님):

- 방 = 사적 토론장. 이벤트가 진실 원천, per-room `last_event_seq` + room row lock. revision 다층. worker는 외부 런타임 1회 호출(claim → lease → 실행 → complete).
- 산출물: `deliverable`(`analysis`/`change_proposal`), `change_proposal`은 통합 diff 한 개만 허용. 서버는 patch를 적용하지 않는다.
- W1 작업 프로필: Codex exec 전용 opt-in으로 소유자 지정 workspace에서만 도구 사용. MCP·hooks·web search off.
- 실행 영수증(R1~R4): 예약/시작 분리, attempt당 실행 승인 최대 1회, DPAPI vault. S2(2-runtime 동시 토론·pause 복구)는 미검증.
- 웹 GUI(vanilla JS), MCP/HTTP 공급자 카탈로그, PostgreSQL/POSIX 픽스처 CI. 회귀: Python 1,244 수집, Node 243 passed(2026-11-01).

## 3. 분석 방법

1. **정체 파악**: README·docs·브랜치·이슈·릴리스부터. 근거 없이 파일 크기만으로 판단하지 않는다.
2. **핵심 경로 추적**: 협업(발견·주소·메시지·상태 전이·산출물·병렬 작업·충돌) 경로를 코드로 따라가 **파일:라인**으로 지목.
3. **대조**: TalkAbout의 해당 메커니즘과 대조해 결손/대체/강화 판정.
4. **개선안**: `[파일:줄] → 무엇이 좋은가 → 우리 작업장 적용안` 형식. 영향·비용(S/M/L)·위험·측정 방법.
5. **정직성**: 실행하지 않은 것은 "정적 분석 추정"으로 명시. 부재 주장은 "찾지 못했다"로 한정.

## 4. 보고 규칙 (반드시)

1. 근거 우선: 모든 주장에 저장소 루트 기준 상대 경로 + 라인. 코드 인용은 원문 그대로, 짧게.
2. 추측 금지 · "미확인" 표기. 빌드/실행은 하지 않는다(도구 없음: Rust 툴체인 미설치, `npm install`/`pip install` 금지).
3. 선별: 개선안은 **[즉시]/[단기]/[중기]/[제외]** 태그 + 근거. 잡다한 스타일 지적 금지.
4. 한국어. 코드 식별자·경로는 원어 유지.
5. 분량: 300~650줄. 표·짧은 코드블록 적극 사용.
6. 자기 보고서 파일만 쓴다. 제품 코드·`docs/`·클론은 수정 금지(클론 읽기 전용).

## 5. 산출물 배정

| 담당 | 파일 | 범위 |
| --- | --- | --- |
| artiboard-analyst | `analysis/r6-artiboard.md` | Artiboard 전체 (보드·산출물·MCP·인간+에이전트 공동 작업) |
| session-peer-analyst | `analysis/r6-session-peer.md` | Session-Peer 전체 (발견·메시징·E2E 릴레이·MCP·SSH) |
| moai-analyst | `analysis/r6-moai.md` | Moai 전체 (이슈 JSONL 스키마·supervisor→worker·TUI/web·강제 없는 규율) |
| dreaming-analyst | `analysis/r6-openclaw-hermes-dreaming.md` | OpenClaw 드리밍·Hermes 메모리 외부 조사 점별 검증 |
| openmuse-analyst | `analysis/r6-openmuse.md` | OpenMuse 전체 (AG-UI·브라우저/터미널·배경 작업·승인) |
| opengrokbot-analyst | `analysis/r6-opengrokbot.md` | OpenGrokBot 전체 (teammate 모델·BYOK·작업 루프·컴퓨터 제어) |
| lead | `analysis/r6-00-SYNTHESIS.md` | 5+α 저장소 종합 — 영역별 최적 출처, **바로 반영** 개선점 우선순위와 구현 배정 |
