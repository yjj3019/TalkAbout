# 01-FINDINGS — munder-difflin 정밀 검토 발견 사항

- 대상: `HarnessMD/munder-difflin` @ `d86ab7ac` (2026-11-23 분석, MIT)
- 방법: 4개 영역 병렬 정밀 검토(메인 프로세스·렌더러·CI/테스트/릴리스·저장소 위생) + 원문 라인 검증.
  코드 수정 없음(읽기 전용). 클린룸: 아이디어만 참고.
- 발견: **44건** (High 5 · Medium 20 · Low 19) — 보안 10 · 안정성/성능 8 · 유지보수/설계 11 · CI/테스트/릴리스 7 · 위생 6 · 문서 4 · UX/i18n 2 · 접근성 1 (복수 범주는 주범주로 계수)

## A. 보안 (10건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| CORE-02 | **High** | IPC 핸들러(~230) 전역 sender 무검증 — XSS 1건에 브리지 전권(설정·키·파일·pty·webhook) 노출, floor 창도 전권 공유 | `src/main/index.ts:1246-7733`, `src/preload/index.ts:1923`, `src/main/navigationGuard.ts:84-102` |
| CORE-01 | Medium | 루프백 OTLP 수집기가 무인증 — 로컬 임의 프로세스가 가짜 cost/error 주입 → 서킷브레이커 오폭·대시보드 오염 | `src/main/telemetry.ts:269-301,316-335,372-375,108` |
| CORE-03 | Medium | 웹훅 HTTP 서버가 호스트 미지정 바인드(0.0.0.0) — 비밀 없는 LAN 피어가 공유 버킷 소진 → 터널 경유 정상 호출까지 429 | `src/main/webhook.ts:331,383-407,565-575` |
| CORE-04 | Medium | 사용자 프롬프트가 `command_history`에 평문 저장(토큰/비밀번호 포함 가능) — 로컬 전용·5000행 캡은 있으나 유출 시 평문 회수 | `src/main/db.ts:164-171,107-115`, `src/shared/retention.ts:128-134` |
| CORE-09 | Low | `pty:write` 크기 캡 없음(렌더러 신뢰 경계라 현재 위험 낮음, sender 검증 도입 후 표면 축소) | `src/main/index.ts:4860-4870`, `src/main/pty.ts:995-999` |
| UI-07 | Low | 웹훅 엔드포인트 ID `Math.random()` 생성 — main 재검증 주석 의존, 타입 수준 고정 부재 | `src/renderer/src/components/triggers/api.ts` |
| UI-03 | Medium | 통합 레지스트리 클라이언트가 브리지 부재 시 **조용한 mock 폴백**(“bridge NOT landed yet” 잔재) — 거짓 성공 저장 | `src/renderer/src/integrations/registryClient.ts:126-166` |
| INFRA-08 | Low | `tools/make-ph-thumbnail.cjs:18` 개인 절대경로 하드코딩 — 다른 기계에서 엉뚱한 클론 읽기 | `tools/make-ph-thumbnail.cjs:18` |
| INFRA-11 | Low | `DISCORD_BOT_TOKEN` 누락 시 조용히 성공(exit 0) + timeout/`--max-time` 미지정 | `.github/workflows/contributor-role.yml:48-50,29,76` |
| HYG-07 | Low | 테스트 픽스처 4건이 시크릿 패턴 보유(전부 더미 — 실값 없음 확인됨) — 허용목록/주석으로 명문화 권장 | `test/repro/bug-12.repro.cjs`, `test/repro/bug-17.repro.cjs`, `test/slack-timeout.test.cjs`, `test/voice-messages.test.cjs` |

## B. 안정성·성능 (8건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| CORE-05 | Medium | 서킷브레이커 `hardStop` 기본 OFF + 1단계 에스컬레이션 — 비용 폭주(키 만료·무한 재시도) 시 사용자 무관 지속 | `src/main/breaker.ts:23,88,330` |
| CORE-10 | Low | 웹훅 재생창 바깥 재전송은 재처리(중복 방지는 창 한정) — 고의 트레이드오프, 문서화만으로 충분 | `src/main/webhook.ts:363-380` |
| CORE-11 | Low | 메인 스레드 동기 SQL + 주기 동기 스캔 + 상시 하트비트 — 부하 증가 시 메인 스레드/PTY I/O 경합 | `src/main/db.ts`, `src/main/handoffJob.ts`, `src/main/index.ts:4252` |
| CORE-12 | Low | 명시적 페일-오픈 저하 경로 3종(floorGate/프록시 직결/트랜스크립트 폴백) — 고의·문서화, 저하율 집계만 추가 권장 | `src/main/floorGate.ts:68-73`, `src/main/hive.ts:1824-1852`, `src/main/telemetry.ts:439-457` |
| UI-01 | Medium | `feeds` 상태가 무한 성장하는 **write-only** 버퍼(소비자 0건, ANSI 원문 보관) — 장시간 세션 메모리 누적 | `src/renderer/src/store/store.ts:969-970`, `usePtyParser.ts:134` |
| UI-02 | Medium | 터미널 청크마다 `set()` 2회 — 선택자 재실행 진폭(에이전트수×청크수), 정규식 스니핑 3종도 매 청크 | `src/renderer/src/hooks/usePtyParser.ts:182` |
| UI-08 | Low | 수면 감지 2초 풀링의 전체 `hiveRegistry()` 호출 — 에이전트 증가 시 선형 비용, 이벤트 푸시로 대체 가능 | `src/renderer/src/hooks/useHive.ts:490` |
| UI-09 | Low | 부팅 경로 `sendSync` 동기 차단(roster/harnessHome) — 의도 설계, 비용 비가시 | `src/preload/index.ts` |

## C. 유지보수·설계 (11건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| CORE-06 | **High** | `src/main/index.ts` 484KB/8415줄 모놀리스(핸들러 ~230 인라인, 수제 typeof 검증 제각각) + preload 래퍼 280여 개 수제 1:1 — 신규 채널마다 4곳 수동 동기화 | `src/main/index.ts`, `src/preload/index.ts`(113KB/1925줄), `src/main/hive.ts`(312KB/5594줄) |
| CORE-07 | Medium | 훅 브리지 shim이 hive.ts 임베디드 템플릿으로 7벌 중복(AGENT_ID·GEMINI_HOOK_SHIM) — 단위 테스트 불가·회귀 상존 | `src/main/hive.ts:4956,5071,5141,5201,5255,5470,5530` |
| UI-04 | Low | `clearLocalState()`가 `cth.*` 접두어 전부 삭제 — 언어 등 기기 선호도까지 휘발, “fresh” 흐름과 혼재 | `src/renderer/src/components/SettingsModal.tsx:51-61` |
| UI-05 | Low | 레거시 터미널 뷰가 테마 팔레트를 수제 중복(색상 이원화) | `src/renderer/src/components/TerminalView.tsx` |
| UI-10 | Low | 실시간 보이스 모듈 전역 타이머의 수명 관리 분산 — 마운트/언마운트·HMR 시 정리 추적 어려움 | `src/renderer/src/realtime/session.ts:421` |
| INFRA-09 | Medium | `verify-*.mjs` 미러 스크립트가 생산 로직을 복제해 검증 — 원본 수정 시 미러 낡음 → 가짜 확신 + CI 미편입 | `scripts/verify-worker-gc.mjs:36-45`, `scripts/verify-keepalive-catchup.mjs:44-60` |
| INFRA-10 | Low | 소스/YAML 패턴 고정 테스트 3건(brittle) + `test/repro/bug-*.repro.cjs`가 스위트 스코프 밖(회귀 보호 부재) | `test/pr-evidence-gate.test.cjs:20-38`, `test/release-drop-reach.test.cjs:51-66` |
| INFRA-12 | Low | electron-builder `afterSign`/notarize 정책이 소스 전용 릴리스 전환 후 죽은 경로 잔존 | `electron-builder.yml:9` |
| HYG-09-A | Low | 잔존 쓰레기: 1바이트 빈 파일(`.claude/skills/sample.md`), `docs/r/*.json` 2건, legacy 포스터 다수 | 커밋 트리 확인 |

## D. CI·테스트·릴리스 (7건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| INFRA-01 | **High** | 테스트 115개가 CI에 전혀 편입 안 됨(typecheck·build만) — 회귀가 머지 후에만 발견 | `.github/workflows/ci.yml:9-42` |
| INFRA-02 | **High** | Build 작업 `continue-on-error: true`가 필수 “Build” 체크로 쓰임 — 게이트 실질 무력화 | `.github/workflows/ci.yml:34`, `blog.yml:40` |
| INFRA-03 | Medium | macOS(Node 20) 단일 러너 — Windows 전용 테스트 5종·Linux 경로 CI 미검증, Node 버전 3중 분산(.nvmrc 22/engines ≥20 <25/blog 22) | `ci.yml:12,17`, `.nvmrc` |
| INFRA-04 | Medium | 태그 푸시 시 태그명↔package.json 버전↔RELEASE.md 제목 일치 검증 부재 — 불일치 릴리스 발행 가능 | `release.yml:6-9`, `ci.yml:5` |
| INFRA-06 | Medium | `test` 스크립트 부재, `test:focused`가 사실상 전체 실행(명칭 불일치), 셸 글롭 의존 — Windows cmd에서 실패 가능 | `package.json:22` |
| INFRA-05-A | Medium | RELEASE-CHECKLIST.md·TELEMETRY.md(:101-103)가 소스 전용 릴리스 전환 후 구식(POSTHOG_KEY 주입 주장도 이제 비사실) | `RELEASE-CHECKLIST.md`, `TELEMETRY.md:101-103` |
| HYG-08 | Low | `seo/SEO_METADATA.md` URL이 구 계정 이름 | `seo/SEO_METADATA.md` |

## E. 저장소 위생 (6건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| HYG-01 | **High** | 동일 바이너리 이중 커밋 — 동일 블롭 그룹 1,742개 · 낭비 **133.5MB** (`blog/src/assets/media/**` ↔ `docs/blog/assets/media/**`) | `git ls-tree` 블롭 해시 그룹핑 |
| HYG-02 | **High** | 원격 브랜치 **371개** 방치(evidence 128 · seo-engine 104 · blog 38), 스쿼시 머지 관행으로 `--merged` 정리 불가(머지 86개만 집계), 최장 5개월 잔존 | `git branch -r`(371), `--merged/--no-merged`(86/285) |
| HYG-03 | **High** | 빌드 산출물 `docs/blog/`(2,272파일 · **145MB**) 커밋 + LFS 부재 — mp4/png 2,700여 개 과거 버전이 이력에 영구 보관(팩 467MB) | `docs/blog/`, `.gitattributes` 없음, `git count-objects` |
| HYG-04 | Medium | `docs/pr-evidence/` 83파일 15.2MB 영속 누적 — 보존·만료 정책 부재 | `docs/pr-evidence/` |
| HYG-05-M | Medium | SPEC.md(314줄)가 “macOS-first”·“메시지 버스 NOT”·“웹 접근 NOT”으로 규정 — 0.6.0 현실(멀티엔진·hive 메시징·Slack·웹훅)과 직접 모순, MEMORY_GRAPH_SPEC “awaiting sign-off” 스탬프 잔존 | `SPEC.md`, `MEMORY_GRAPH_SPEC.md` |
| HYG-06 | Medium | CHANGELOG 0.6.0이 태그됐는데 `## [Unreleased]` 아래 수록, 0.5.x 스킵 사유 미기재 | `CHANGELOG.md`, `git log -1 v0.6.0` |

## F. 문서 (4건) · UX/i18n (2건) · 접근성 (1건)

| ID | 우선순위 | 제목 | 증거 |
|---|---|---|---|
| INFRA-07 | Medium | 저장소 URL 이원화 — 원격은 `HarnessMD/`인데 package.json:11·SECURITY.md:24·CONTRIBUTING.md:40,136·pr-evidence.yml:142은 `chaitanyagiri/` | grep 대조 |
| HYG-09-B | Low | 문서 잔존 구문/위치 정리 | `hygiene` 검토 |
| UI-06 | Low | 에이전트 카드 `div role="button"` — 키보드 활성화·포커스 표시 보장 미검증 | `AgentCard.tsx` |
| UI-11 | Medium | 컨텍스트 읽기 실패 시 저장이 조용히 차단 — 실패 사유가 UI에 미노출 | `src/renderer/src/components/triggers/api.ts` |

## G. 긍정 확인 (견고한 지점, 유지 권장)

- **보안 기반**: sandbox+contextIsolation+contextBridge 단일 브리지, CSP `default-src 'self'` + `img-src 'self' data: blob:`, rehype-raw 미사용 마크다운, localPath 스킴/확장자/`..` 검증, 외부 링크 `openExternal` 전용, 웹훅 사전 인증·상수시간 비교·decoy·재생창, PID 재사용 가드, 페일클로즈 원칙, `LICENSE`/`LICENSE-ASSETS`/ATTRIBUTION 정리, 시크릿 실값 0건, `.gitignore` 정밀(추적-무시 0건).
- **UI**: `agentSlices` 객체 정체성 캐시 + `useShallow` 고주파 재렌더 차단, addAgent 멱등 처리, 설정 저장 single-flight, i18n 로케일 파생 안전장치(RTL/미번역 구조적 차단), 음성 주입 텍스트 세척(`voiceText.ts`) + main redact.
- **테스트 양**: 115개 `node:test` 스위트 + Electron E2E(quit-sweep) — 단, CI 미편입(INFRA-01)이 유일한 아쉬움.
