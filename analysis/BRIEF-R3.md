# 공유 브리프 (3라운드) — handy · t3code · claude-mem 3개 저장소 **성능** 정밀 분석

## 0. 목표 (이번 라운드는 "기능"이 아니라 "성능"이다)

1라운드·2라운드는 기능·계약·보안을 봤다. 3라운드는 **성능**만 본다.
산출물은 두 층이다. **둘 다 반드시 쓴다.**

- **(A) 각 저장소 자체의 성능 개선안** — 그 저장소 메인테이너에게 보낼 수 있는 수준의 병목 진단과 패치 제안.
  "성능"의 정의: 지연(latency, p50/p95), 처리량(throughput), 메모리, 시작 시간, 번들 크기, 토큰/비용,
  DB·IPC 왕복 수, 락 경합, GC 압력, 리렌더/페인트 비용, 캐시 적중률, 스케줄링·동시성.
- **(B) TalkAbout 적용안** — TalkAbout(Python/FastAPI/PostgreSQL + 자체 vanilla JS 웹)에서 같은 문제를 푸는 구체적 방법.
  TalkAbout 모듈/파일을 실제로 지목하고, 지금 코드가 어떤가를 함께 적는다.

## 1. 분석 대상 (2026-11-02 스냅샷, `main` 브랜치 zip 다운로드 — **git 이력 없음**)

| 저장소 | 로컬 경로 | 규모(실측) | 스택 | 정체 |
| --- | --- | --- | --- | --- |
| `cjpais/handy` | `.analysis-repos/handy/Handy-main/` | 484파일 8.1MB · `.rs` 82/1.35MB · `.tsx` 152/609KB | Rust + Tauri 2 + React/TS | 로컬 음성→텍스트 데스크톱 앱(whisper 계열 + Silero VAD, 단축키 누르고 말하면 타이핑) |
| `pingdotgg/t3code` | `.analysis-repos/t3code/t3code-main/` | 29,137파일 321.6MB(단 `.repos/`·`Vendor/`·`node_modules` 제외 시 ~)` · `.ts` 22,962/163MB · `.tsx` 1,668/12MB | TypeScript 모노레포(Effect, pnpm workspace) + 모바일 네이티브 | 에이전트 코딩 오케스트레이터/IDE 계열 |
| `thedotmack/claude-mem` | `.analysis-repos/claude-mem/claude-mem-main/` | 2,873파일 53.7MB · `.ts` 1,872/17.9MB · `.py` 202/2MB | TypeScript(Bun 빌드 산출물 `.cjs` 포함) | Claude Code 등 에이전트용 장기 기억(memory) 플러그인 — 훅·압축·검색·주입 |

**주의(실측)**: `t3code`의 `.repos/`, `apps/mobile/modules/*/Vendor/`, `node_modules`는 서드파티 벤더·서브모듈 사본이다.
**분석 대상에서 제외**한다(포함하면 결론이 오염된다). `claude-mem`의 `plugin/scripts/*.cjs`, `*.map`은 빌드 산출물이므로
소스(`src/`)를 기준으로 분석하고, 번들 크기 논의에만 산출물을 쓴다. `handy`의 `.gguf`/이미지/아이콘은 바이너리다.

## 2. 기준 프로젝트 (TalkAbout)

`D:\AI-Codding\Claude\TalkAbout` — 초대 기반 사적 공간에서 각자 자기 컴퓨터의 외부 AI 에이전트(Codex CLI·Claude Code·Grok)가
사람 소유자 통제 아래 토론하는 **서버**. Python 3.12 + FastAPI + PostgreSQL. 정본 `docs/ARCHITECTURE.md`, 상태 `docs/STATUS.md`.

성능상 이미 정해진 사실(추측 아님):
- 이벤트가 진실 원천, per-room `last_event_seq` + room row lock. revision 다층. 외부 pull worker가 SQLite journal 사용.
- 서버 `TurnScheduler`가 round-robin `speak` task 생성 → claim(lease) → 런타임 1회 호출 → complete.
- 웹은 프레임워크 없음: `talkabout/web/app.mjs` 126KB · `state.mjs` 91KB · `style.css` 57KB · `index.html` 25KB.
- 테스트: pytest 1,244 수집(0.77초), Node 테스트 별도. 도구: Python 3.12(.venv), Node 24, pytest. **Rust/cargo/bun/pnpm/tsc 없음**.
- 성능 회귀 하네스가 이미 있는지 `tools/`·`.ci-local/`에서 확인하고, 있으면 그것을 기준선으로 인용한다.

## 3. 분석 방법 (이 순서를 지킬 것)

1. **핫패스 선정**: 엔트리포인트(예: Tauri command, HTTP route, CLI 진입, React root, 훅 엔트리)에서 실제 실행 경로를 따라가
   "사용자가 체감하는 지연/비용이 발생하는 지점"을 먼저 나열한다. 근거 없이 파일 크기만 보고 고르지 않는다.
2. **원인 규명**: 코드 레벨 원인(알고리즘 복잡도, 불필요한 복사/직렬화, 락 범위, 왕복 수, N+1, 폴링 주기, 매 프레임 할당,
   캐시 없음, 과도한 리렌더, 워커/스레드 배치, 블로킹 I/O, 동기화 지점)을 **파일:라인**으로 지목한다.
3. **개선안**: before/after를 짧은 의사코드 또는 실제 diff 형태로. 가능하면 구체 수치(예: "10ms 폴링 → 이벤트 구동").
4. **효과·측정**: 예상 효과(정성이라도 좋다, 가능하면 추정치와 산출 근거), 그리고 **어떻게 측정해 증명할지** 명령/도구를 적는다.
   저장소에 벤치마크가 있으면 그것을 쓰고 경로를 인용한다.
5. **비용**: S(<반나절) / M(1~3일) / L(1주+). 위험(동작 변경·호환성)도 적는다.

## 4. 보고 규칙 (반드시)

1. **근거 우선**: 모든 주장에 저장소 루트 기준 상대 경로 + 라인(`src-tauri/src/managers/model.rs:412`). 코드 인용은 원문 그대로, 짧게.
2. **추측 금지**: 확인 못 한 것은 "미확인". 존재를 추정한 파일을 근거로 쓰지 않는다. 부재 주장은 "찾지 못했다"로 한정.
   실행해 보지 않은 것은 "정적 분석 추정"이라고 명시한다.
3. **선별**: 개선안은 **효과 대비 비용 순**으로 정렬하고, 상위 5~8건에 집중한다. 잡다한 스타일 지적은 금지.
   각 항목에 `[즉시]`/`[단기]`/`[장기]`/`[제외]` 태그와 근거를 붙인다.
4. **한국어**. 코드 식별자·경로는 원어 유지.
5. 분량: 250~600줄. 표·짧은 코드블록 적극 사용.
6. **자기 보고서 파일만 쓴다.** 다른 팀원 파일·제품 코드·`docs/`는 수정 금지. 저장소 클론도 수정 금지(읽기 전용).
7. 외부 코드를 복사하지 않는다(구조·수치 인용만). 라이선스: handy MIT, t3code·claude-mem 라이선스는 각 `LICENSE` 확인 후 명시.
8. 시간을 쓰지 말 것: 빌드·실행 시도(Rust/Bun 툴체인 없음), `npm install`, 대용량 벤더 디렉터리 전수 열람.

## 5. 산출물 배정

| 담당 | 파일 | 범위 |
| --- | --- | --- |
| handy-analyst | `analysis/r3-handy.md` | Handy 전체 (Rust 오디오·모델·IPC + React UI) |
| t3code-analyst | `analysis/r3-t3code.md` | t3code `apps/web`·`apps/server`(또는 `packages/` 핵심) 성능 |
| claude-mem-analyst | `analysis/r3-claude-mem.md` | claude-mem 훅·워커·검색·압축·주입 경로 |
| talkabout-analyst | `analysis/r3-talkabout-baseline.md` | TalkAbout 현재 성능 기준선·병목 실측/정적 진단 |
| lead | `analysis/r3-00-SYNTHESIS.md` | 3저장소 + TalkAbout 종합, 채택 우선순위 |
