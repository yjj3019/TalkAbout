# 05. Orca 제품·UX·테스트 자산 분석 → TalkAbout 이식 검토

- 분석 대상: `orca-analysis` (stablyai/orca, HEAD `51dcef8`, MIT)
- 기준 프로젝트: TalkAbout (`talkabout/web/` 자체 CSS·SVG, Python 636 / Node 196)
- 작성일: 2026-10-25
- 규칙: 모든 주장에 파일 경로(+라인)를 붙였다. 코드로 확인하지 못한 것은 **미확인**으로 표시했다.
  태그는 BRIEF.md §4 정의(`[채택]/[차용]/[대조]/[제외]`)를 따르고, 비용은 S(~1일)/M(2~4일)/L(1주+)이다.

## 0. 먼저 읽을 것 — 규모 차이와 전제

| 지표 | Orca | TalkAbout | 근거 |
| --- | --- | --- | --- |
| 소스 테스트 파일 | 13,424 | 54 (`tests/test_*.py` 46 + `tests/*.mjs` 8) | `orca-analysis/src/**/*.test.ts*` 파일 수 / `tests/` 목록 |
| E2E 스펙 | 598 (`tests/e2e/*.spec.ts`) | 없음(합성 DOM 하네스) | `orca-analysis/tests/e2e/` |
| GitHub 워크플로 | 68 | 1 (`safe-regression.yml`) | `.github/workflows/` |
| UI 언어 | 7 | 2 (ko/en) | `src/shared/ui-locale.ts:14` / `state.mjs:1-4` |

Orca는 "규모로 품질을 사는" 저장소다. **그 구조를 그대로 옮기면 TalkAbout이 유지보수 불가**가 된다.
이 보고서가 뽑는 것은 (a) 상태 기계·억제 규칙 같은 **규약**, (b) 사용자가 결함을 즉시 알아채는 **UX 결정**,
(c) 적은 비용으로 회귀를 잡는 **테스트 기법**, (d) **문서/린트 이중 강제** 구조다.

Orca 렌더러는 Electron 전용(WebGL 터미널, 트레이, Dock, 자동 업데이터)이므로 컴포넌트·IPC는 옮기지 않는다.
Electron 의존 항목은 §10에서 `[제외]`로 이유와 함께 정리했다.

---

## 1. 알림 — 트리거·억제·포커스·OS/인앱 (질문 1)

### 1.1 파이프라인 구조 (핵심 발견)

Orca 알림은 main이 아니라 **렌더러가 생산**하고, main은 **배달 정책만** 담당한다. 이 분리가 이식 가능한 핵심이다.

```
renderer: 훅/제목/프로세스 종료 관측 → 직렬 생산자 → dispatchTerminalNotification()
            → (1) stale 스냅샷 거부 (2) attention admission (3) unread 마킹
main:     createNotificationDeliveryService().dispatch()
            → tray attention → 모바일 fan-out → 설정 게이트 → 포커스 억제 → burst 쿨다운 → OS 알림
```

- 생산자: `orca-analysis/src/renderer/src/hooks/agent-hook-completion-notifications.ts:201-247` (훅 레인),
  `src/renderer/src/components/terminal-pane/pty-connection/agent-task-complete-notify.ts:38-141` (제목/프로세스 레인)
- 정책 집행: `src/renderer/src/components/terminal-pane/use-notification-dispatch.ts:69-186`
- 배달: `src/main/notifications/notification-delivery-service.ts:42-174`
- 서비스는 `NotificationDeliveryDependencies`(읽기 함수·플랫폼·시계)를 **주입받는 순수 조립체**다
  (`notification-delivery-service.ts:14-34`). Electron 없이 테스트할 수 있는 이유가 여기에 있다.

### 1.2 트리거 조건

| 소스 | 발화 조건 | 근거 |
| --- | --- | --- |
| `agent-task-complete` | 에이전트가 working → idle로 전이 | `docs/site/content/docs/notifications.mdx:9` |
| 〃 (훅 레인) | 훅이 turn end를 증명 → coordinator가 completion 요청 | `agent-hook-completion-notifications.ts:212-232` |
| 〃 (제목 레인) | 제목 idle 엣지 + grace 타이머 경과, 또는 최대 대기 만료 | `agent-task-complete-notify.ts:125-134` |
| 〃 (프로세스 종료) | `agentCompletionSource === 'process-exit'`면 stale active 훅을 무시 | `use-notification-dispatch.ts:94-110` |
| `terminal-bell` | 터미널 BEL. 단, 훅이 소유한 idle은 BEL을 흡수 | `agent-task-complete-notify.ts:24-36` |
| `test` | 설정 화면의 시험 발송. 모든 dedupe 우회 | `notification-delivery-service.ts:144-145` |

- **실패(failure)·에러는 알림 트리거가 아니다.** 실패는 문구/상태 라벨로 처리한다:
  `src/shared/agent-session-failure-copy.ts:145-158` (rate-limited, usage limit, reset 시각).
  `agent-hook-completion-notifications.ts:237-243` 주석이 명시한다 — "Native settings call this task complete,
  but snapshot state must label the alert needs input." 즉 **하나의 알림 소스로 상태(완료/입력대기)를 구분**한다.
- 대기(waiting/needs input)는 별도 트리거가 아니라 같은 완료 이벤트 + `agentState` 페이로드로 표현된다
  (`notification-settings-types.ts:37-70`의 `agentState`, `agentTurnOutcome`).

### 1.3 중복 억제 / 디바운스 (수치까지 확인됨)

| 계층 | 규칙 | 근거 |
| --- | --- | --- |
| 데스크톱 burst 쿨다운 | **5,000ms**, 키는 `attentionKey ?? worktreeId ?? worktreeLabel ?? 'global'`. 맵 상한 50 | `src/shared/notification-burst-cooldown.ts:1-2, 24-37`; 호출부 `notification-delivery-service.ts:144-156` |
| keyed news 예외 | 생산자가 소유한 `attentionKey`가 있으면 worktree 단위 collapse에서 분리 | `notification-delivery-service.ts:48-50` |
| 모바일 fan-out | 별도 맵 + 키에 `desktopAllowed` 포함(데스크톱이 꺼져도 폰은 받음) | `notification-delivery-service.ts:94-125` |
| 재시작 후 중복 | `announced-notification-registry`: paneKey별 최대 256 subject × 20 id, 메모리 전용, journal read가 덮을 때만 retire | `announced-notification-registry.ts:8-9, 21-61` |
| stale 완료 거부 | 10초 초과 스냅샷 무시, superseded 스냅샷 거부 | `use-notification-dispatch.ts:37, 88-116` |
| 렌더러 측 중복 | 동일 알림 id를 `stateStartedAt`으로 만들어 OS/미확인/벨이 같은 identity 공유 | `use-notification-dispatch.ts:148-156` |

이 설계의 요점은 **억제 사유를 결과 타입으로 돌려준다**는 점이다:
`NotificationDispatchResult.reason`이 `cooldown / suppressed-focus / host-muted / blocked-by-system / not-displayed …`
8종으로 열거된다 (`notification-settings-types.ts:72-85`). 억제는 "조용히 아무 일도 안 일어남"이 아니라 **설명 가능한 결과**다.
TalkAbout에 그대로 필요한 사고방식이다.

### 1.4 앱 포커스 시 억제

- `settings.suppressWhenFocused && request.isActiveWorktree && window.isFocused()` → `suppressed-focus`
  (`notification-delivery-service.ts:134-142`).
- 이 억제는 **데스크톱 알림에만** 적용된다. 모바일 fan-out은 데스크톱 early-return보다 앞에서 수행된다
  (`:88-89` 주석: "Desktop focus need not silence phones").
- 트레이 attention은 게이트보다 **먼저** 설정된다(`:69-74`). 포커스 억제가 트레이 점까지 죽이지 않게 하려는 의도.
- 알림을 받을 자격 자체는 `resolveAgentAttention`이 판정한다: 음소거 워크스페이스 → unread·배너·소리·푸시 전부 없음,
  live 세션 증거 없음 → 거부 (`src/renderer/src/attention/agent-attention-policy.ts:47-93`).

### 1.5 OS 알림 vs 인앱

| 채널 | 구현 | 근거 |
| --- | --- | --- |
| OS 알림 | Electron Notification, 소스별 on/off, 커스텀 사운드 10종+파일 | `notification-settings-types.ts:6-29` |
| 트레이 점 | amber `#f59e0b` (렌더러 attention 색과 동일) 비트맵 합성 | `src/main/tray/tray-attention-icon.ts:3-6, 34-69` |
| macOS Dock 배지 | unread count, 99+ 상한, darwin 전용 | `src/main/dock/unread-badge.ts:5-11` |
| 모바일 푸시 | `dispatchMobileNotification` fan-out, away-only 정책 | `notification-delivery-service.ts:88-125` |
| **인앱 unread** | 배달 **전에** unread를 먼저 쓴다 — 배너가 억제돼도 마커는 남는다 | `agent-attention-policy.ts:95-107` |
| 데스크톱 away 판정 | system idle/lock. **모르면 away로 취급**해 폰을 막지 않음 | `src/main/notifications/desktop-away-state.ts:8-19`; `desktop-presence-reporter.ts:41-42` |

- **Windows taskbar attention(flashFrame)·오버레이 아이콘은 없다** — `flashFrame|setOverlayIcon|requestUserAttention`
  저장소 전체 grep 결과 0건. 크로스플랫폼 알림은 "트레이 점 + OS 알림"으로만 커버된다(문서 `notifications.mdx:9`는
  "system notification, sound, and a chip on the worktree"라고만 적는다 — Windows 관점의 공백은 문서에도 드러나지 않는다).

### 1.6 TalkAbout 현실과 제안

TalkAbout은 **완료 통지가 0**이다. `talkabout/web/app.mjs:671`의 10초 폴링이 유일한 갱신 경로이고,
`document.hidden`이면 폴링을 건너뛴다(`if(!user||document.hidden||view.busy.size||communityRead)return;`).
즉 **사용자가 다른 탭에 있으면 에이전트 종료를 영원히 모른다.** 화면 피드백은 `notice()` 단일 슬롯뿐이다(`app.mjs:12`).

- `[채택]` **완료 통지 최소 세트** (비용 M)
  - 서버 worker가 task 완료 시 `document.title`에 미확인 수를 반영(`(1) TalkAbout — 토론` 형태).
  - 브라우저 Notification API는 권한이 있을 때만, `document.hidden`일 때만 발화.
  - 억제/중복 규칙은 Orca의 **결과 사유 열거**를 그대로 차용: `{delivered, reason}` 형태로 함수에서 반환하고
    테스트에서 reason을 단언한다. 부착 위치: `talkabout/web/app.mjs`(polling 지점), `talkabout/web/index.html`(title/author 메타).
- `[차용]` **in-app unread를 배달보다 먼저** 쓰는 순서 (`agent-attention-policy.ts:95-107`).
  TalkAbout은 SSE/폴링이 있으므로 "배지 먼저, 배너 나중"이 자연스럽다. 비용 S.
- `[대조]` Orca는 unread를 **pane(터미널 서피스)** 단위로 관리하고, 렌더러 재구성으로 ID가 바뀌는 문제를
  journal 위치로 보정한다(`announced-notification-registry.ts:1-2`). TalkAbout은 서버 `event.seq`가 진실 원천이므로
  이 보정 계층이 **필요 없다**. Orca가 문제를 푸는 방식(휴리스틱 ID 레지스트리)을 따라 하면 안 된다.

---

## 2. usage / rate-limit 상태 표시 UX (질문 2)

### 2.1 무엇을 표현하는가 — 데이터 모델이 곧 UX 계약

`src/shared/rate-limit-types.ts`:

- 창(window): `usedPercent 0~100`, `windowMinutes`(300=5h, 10080=7d), `resetsAt`, `resetDescription` (`:1-10`)
- 제공자 집계: `session / weekly / monthly / extraUsage / buckets[] / planType / updatedAt / error / status` (`:88-133`)
- 수집 상태 5단계: `idle | fetching | ok | error | unavailable` (`:12`)
- 실패 원인 14종: `missing-credentials / stale-token / keychain-unavailable / no-subscription / rate-limited / usage-unavailable …` (`:49-65`)

**"실패"를 하나로 뭉개지 않는 것**이 핵심 UX 결정이다. TalkAbout도 runtime 실패를 한 문구로 처리하지 말아야 한다.

### 2.2 언제 보여주는가

- 상태바 세그먼트로 **상시**. 단, 자격증명/세션 쿠키/키 존재 플래그가 있을 때만 세그먼트가 보인다
  (`rate-limit-types.ts:184-195`의 `minimaxCookieConfigured`, `cursorAuthConfigured` 등).
- 폭이 부족하면 density level을 낮추고(`src/renderer/src/components/status-bar/status-bar-density.ts`,
  테스트 `status-bar-density.test.ts:22-27`가 0~3 레벨 단언) usage는 collapse한다
  (`status-bar-usage-collapse.ts` — 본문 미확인, 파일 존재만 확인).
- 상세는 hover card/popover: `status-bar/tooltip.tsx`, `UsageRosterPanel.tsx`, `ProviderDetailsMenu.tsx`.

### 2.3 소진 임박 경고 방식 (수치 확인)

| 임계 | 표현 | 근거 |
| --- | --- | --- |
| ≥ 60% | warning 톤(노랑) | `provider-extra-usage-section.tsx:9, 16-18`; `usage-roster-formatting.ts:17-23` |
| ≥ 80% | urgent 톤(빨강) | `provider-extra-usage-section.tsx:10, 18-19`; `usage-roster-formatting.ts:19-21` |
| tone 속성 | `urgent / warning / normal`, 색은 CSS 토큰(`destructive`, `status-warning`) | `StatusBarProviderSegment.tsx:109, 118-160` |
| 정렬 | 소진된 provider를 로스터 **앞으로** | `cursor-usage-headline.test.tsx:77, 109` ("keeps exhausted providers first") |
| stale 표시 | 마지막 성공 후 오래되면 경고 아이콘 | `StatusBarProviderSegment.tsx:305, 349` |

- **모달/토스트로 "곧 소진" 경고를 하는 코드는 확인하지 못했다**(미확인). 실제 경고는 색·정렬·stale 아이콘뿐이다.
- 표시 단위는 `used`/`remaining` 선택 가능, 기본 `used` (`usage-percentage-display.ts:4-7`).
  반올림은 **used를 먼저 반올림한 뒤 보수**해서 raw/선반올림 입력이 동일하게 보이도록 한다(`:27-29`).
  NaN은 100% 잔여로 보이지 않도록 0 처리(`:22-25`).
- 기본값이 바뀌면 1회성 안내를 띄우되, 5개 조건(영속 UI 준비·미해제·상태바 표시·미터 표시·모달 없음)을 모두 만족할 때만
  1.8초 뒤에 보여준다 (`usage-percentage-display-change-notice.ts:20-34`, `StatusBarUsageChangeNoticeCard.tsx:7`).

### 2.4 수집 비용을 UX로 회피하는 결정 (인상적)

Claude 사용량은 **usage endpoint 폴링을 하지 않는다**. Claude ≥2.1.80이 statusLine으로 `rate_limits`를 밀어주므로
그 피드를 쓰고, 폴링이 429를 유발하던 문제를 제거했다. 클라이언트 하한 15초(`claude-statusline-rate-limits.ts:1-6`).
파싱은 스키마 드리프트에 **부분 열화**(`used_percentage` 없으면 `utilization`, ISO 문자열 reset 허용)로 대응한다(`:27-50`).

### 2.5 TalkAbout에 붙일 것

| 항목 | 판정 | 비용 | 부착 위치 |
| --- | --- | --- | --- |
| 실패 원인 열거 + 사유별 문구 | `[채택]` | S | `talkabout/web/app.mjs`의 `textFileError` 패턴(`:706`)을 runtime/task 실패로 확장 |
| "예산/lease 잔여" 상시 표시 | `[채택]` | M | `app.mjs:566`(정의)·`:568`(사용)의 `roomCallBudget`은 onboarding 명령 문자열에만 쓰인다 → 방 화면 상단 상태 라인으로 승격 |
| 60/80식 임계 톤 | `[차용]` | S | `style.css`에 `--warn-soft`/`--danger-soft` 토큰 추가 후 예산 잔여에 적용 |
| provider quota 폴링 | `[제외]` | — | TalkAbout은 모델·구독을 보유하지 않는다(BRIEF §2). 이 데이터는 참가자 PC 소유 |

---

## 3. 키보드 · 접근성 — "실제로 강제되는 것" (질문 3)

핵심 답: Orca의 접근성/디자인 규칙은 **문서 + 린트 + 컴포넌트 위임** 3중으로 강제된다.

### 3.1 강제 계층

| 계층 | 내용 | 근거 |
| --- | --- | --- |
| 린트(디자인) | `shadcn/no-restyle`, `shadcn/no-raw-colors`, `shadcn/require-static-classes` — 렌더러 `*.tsx`에 **error** | `config/oxlint-design-system.json:26-43` |
| 린트(기타) | ast-grep, react-doctor, anti-slop, type-aware | `package.json:19, 22-30` |
| 문서(정본) | 토큰은 코드, 문서는 역할만 | `docs/STYLEGUIDE.md:3`; 딥다이브 분리 `:11-19`; 침묵 시 해결 순서 `:89-96` |
| 포커스 트랩 | 자체 구현 없음 — Radix 위임. Esc는 IME 오버레이 예외 처리 후 전달 | `src/renderer/src/components/ui/dialog.tsx:7, 113-131`; `radix-ui` 의존성 `package.json:326` |
| ARIA | `aria-*` 사용 파일 1,279개(`*.tsx`), `aria-live` 84개 파일 | 렌더러 grep |
| reduced motion | CSS `prefers-reduced-motion` 12블록 + `motion-reduce:` 클래스 54곳 | `src/renderer/src/assets/main.css` grep; 규칙 `STYLEGUIDE.md:77` |
| 키보드 규칙 | 하드코딩 `e.metaKey` 금지, 라벨은 실제 바인딩과 일치, 미구현 단축키 칩 금지 | `STYLEGUIDE.md:64, 84-86`; `docs/styleguide/ux-review-rubric.md:18-20` |
| 스크린리더 | 아이콘 전용 버튼은 `sr-only` 라벨 | `ui/dialog.tsx:74-76` |

**주의**: 위 규칙 중 "포커스 트랩"은 Orca 자체 코드가 아니라 Radix의 동작이다. 문서에서 "우리는 트랩을 강제한다"고
쓰면 거짓이 된다. Orca 문서는 실제로 그렇게 주장하지 않고, `ui/` 프리미티브 사용을 강제한다(`STYLEGUIDE.md:57`).

### 3.2 키 바인딩 구조

사용자 편집 파일(`~/.config/.../keybindings.json` 계열) 기반이며, main이 파일을 읽어 스냅샷을 캐시한다
(`src/main/keybindings/keybinding-service.ts:32-84`). 레거시 설정에서 마이그레이션하고(`:36`),
파싱은 별도 모듈(`keybinding-file-parser.ts`)로 분리되어 있다. 액션 ID는 공유 타입(`src/shared/keybindings.ts`).

### 3.3 TalkAbout의 현재 상태와 실제 결함

이미 잘 되어 있는 것:
- 포커스 링 토큰: `talkabout/web/style.css:159-163` (`:focus-visible` 2px accent, offset 2px)
- 탭 시맨틱: `role="tab"`/`aria-selected`/`tabIndex` + Arrow/Home/End (`app.mjs:548, 690`), 테스트로 고정됨
  (`tests/ui-review.test.mjs:143-153`)
- 알림 영역: `#notice`가 `role="status" aria-live="polite"` (`index.html:4`), `#invite-form-error`가 `role="alert"`
- 정적 마크업 다국어 치환(`data-i18n`, `app.mjs:15`)

`[채택]` **결함 3건 — 파일:라인 수준**

1. **reduced-motion 블록에 무관한 규칙이 섞여 들어갔다.**
   `style.css:1692`에서 `@media (prefers-reduced-motion: reduce)`가 열리는데, 바로 다음 `:1693`의 선택자가
   `body::before, .stage-caption`이고 `:1694-1706`이 `.stage-caption`의 위치/배경 속성이다. 이 값은 `:897-910`과 **완전히 동일**하다.
   결과: (a) reduced-motion 사용자에게 `body::before`(전체 배경 aurora, `:118-131`)가 **우하단 작은 pill로 재배치**되고,
   (b) 의도한 `animation: none`이 `body::before`에 적용되지 않아 `:130`의 `aurora 24s infinite`가 계속 돈다.
   → 중복 블록 삭제 + `body::before { animation: none !important; }`를 미디어 쿼리 안에 명시.
2. **reduced-motion 커버리지가 불완전하다.** `:1707`은 `.room-stage .ch, .room-stage .ch *`만 정지시킨다.
   `.motes circle`(`:981`), `.prop-lights circle`(`:989`), `.live-dot`(`:1087`), `body::before`(`:130`)는 계속 움직인다.
   `*,*::before,*::after { transition: none }`(`:1708`)는 transition만 죽이고 animation은 죽이지 않는다.
   → 정지 대상 목록을 명시적으로 나열하고, 아래 (3)의 검사로 회귀를 막는다.
3. **CSS를 검사하는 테스트가 없다.** Node 테스트는 DOM/문구는 보지만(`tests/ui-review.test.mjs`) CSS 파싱·규칙 검사는
   하지 않는다. Orca는 `shadcn/no-raw-colors` 같은 린트로 이 부류를 잡는다(`config/oxlint-design-system.json:35-40`).
   TalkAbout에는 CSS 린터가 없으므로 **`node:test` 1개로 대체**하는 것이 현실적이다:
   `style.css`를 읽어 (a) 중괄호 균형, (b) `@media (prefers-reduced-motion)` 안에 `animation`/`transition` 외 선언이
   섞였는지, (c) `animation:` 선언이 모두 정지 규칙에 매칭되는지 검사. 비용 S.

추가 경미 항목: `<html lang="ko">` 고정(`index.html:2`)과 런타임 갱신(`app.mjs:15`)의 초기 불일치.
영어 사용자가 첫 페인트에서 스크린리더로 한국어 문서를 만난다. `language()` 호출 전에 `lang`을 정하는 것으로 해결(S).

---

## 4. i18n 구조 — 키 관리·누락 감지·언어 추가 비용 (질문 4)

### 4.1 Orca 구조

| 층 | 내용 | 근거 |
| --- | --- | --- |
| 로케일 축 | 7개(`en zh ko ja es fr pt`), `system` 의사 언어, zh는 Hans/Hant 분기 | `src/shared/ui-locale.ts:14-17, 49-58`; `ui-language.ts:1-21, 34-39` |
| 시스템 감지 | Electron `getPreferredSystemLanguages()`, 실패 시 브라우저 fallback, 결과는 1회 스냅샷 | `src/main/i18n/system-language.ts:4-23` |
| main 프로세스 | i18next 인스턴스 + **lazy backend**: 비영어 카탈로그만 동적 import | `src/main/i18n/main-i18n.ts:21-49, 55-75` |
| 영어 정본 | `translateMain(key, fallback)` — 카탈로그 없으면 **호출부 fallback 문자열** | `main-i18n.ts:121-126` |
| 플러그인 언어 | `plugin:<id>/<lang>` 네임스페이스, 런타임 add/remove | `main-i18n.ts:97-119`; `ui-language.ts:20, 34-39` |
| pseudo locale | `en-XA`, 모든 문자열을 `[대괄호]`로 감싸 잘림/미번역을 눈에 보이게 | `src/shared/pseudo-localization.ts:1-15` |
| 카탈로그 규모 | `locales/*.json` 1.10~1.41MB × 7, `en-runtime-required.json` 159KB | `src/renderer/src/i18n/` |
| 키 형식 | `translate('auto.<파일경로>.<hash>', 'English fallback')` | 예: `ui/dialog.tsx:67-68` |

### 4.2 누락 감지 — 3중 게이트

`package.json:119-126`:

| 스크립트 | 역할 |
| --- | --- |
| `verify:localization-catalog` | 소스의 번역 호출 ↔ 카탈로그 키 대조, `sync:localization-catalog`로 en.json만 자동 추가 |
| `verify:localization-extraction` | 추출기가 호출 패턴을 놓치지 않는지(`localization-extraction-call-syntax.test.mjs`) |
| `verify:localization-coverage` | **JSX 텍스트·aria/alt/placeholder/title·toast·alert/confirm/prompt**까지 스캔해 미번역 후보 목록화 |

- 스캔 대상 속성/함수 목록이 코드에 열거되어 있다: `config/scripts/audit-localization-coverage.mjs:16-66`.
- 범위 정의 문서: `config/localization-audit.md:12-30` — "터미널 출력·에러 원문·경로·브랜드명은 미번역이어도 위반 아님".
- **타깃 카탈로그는 자동 수정하지 않는다**(누락은 런타임 영어 fallback으로 버틴다): `localization-audit.md:58-60`.
- `pnpm lint`에 3개 게이트가 모두 포함된다(`package.json:19`).

### 4.3 언어 추가 비용(Orca 기준)

① 1.1MB 카탈로그 ② 정책 스크립트 약 25종(`config/scripts/locale-*-overrides.mjs`, `locale-translation-policy.*.test.mjs`)
③ CI 게이트 3종 통과. 실질 **L**. 게다가 번역 정책 테스트가 언어별로 존재한다
(`locale-translation-policy.ja-round5.test.mjs`, `.zh-status-bar-usage.test.mjs` 등).

### 4.4 TalkAbout 현실 — 그리고 가장 값싼 개선

현재 구조: `talkabout/web/state.mjs:1-4`의 `labels.ko`/`labels.en` 두 객체 + **20개의 `Object.assign(labels.ko,...)` 블록**과
`labels.ko.*` 참조 39회(`:17-19, 79-…`, 파일 끝까지 반복). 조회는 `app.mjs:11`의 `t=key=>labels[view.language][key]`.

- 문제 1: **키 누락 시 `"undefined"`가 화면에 렌더된다.** fallback이 없다.
- 문제 2: 언어 추가 = 20여 블록을 전부 손으로 확장. 3번째 언어 비용이 선형이 아니라 **블록 수 × 키 수**로 폭발한다.
- 이미 있는 자산: `tests/ui-state.test.mjs:8`이 `Object.keys(labels.ko)`와 `labels.en`을 `deepEqual`로 비교한다.
  → **parity 테스트는 이미 존재**한다. 이건 TalkAbout이 Orca보다 앞선 지점이다(Orca는 스크립트 3종으로 같은 일을 한다).

`[채택]` 개선 3단계 (비용 M, 부착 `talkabout/web/state.mjs` + `tests/ui-state.test.mjs`):

1. `labels`를 `{[key]: [ko, en]}` **단일 표**로 통합(20개 블록 → 1개). `Object.assign` 사슬 제거.
2. `t()`를 `labels[lang][key] ?? labels.ko[key] ?? '__MISSING__:'+key`로 바꿔 누락을 **가시화**한다
   (`app.mjs:11`). 테스트에서 `Object.entries(labels)`의 각 값이 2개 언어를 갖는지 단언.
3. `index.html`의 `data-i18n` 키 집합 ⊆ `labels` 키 집합 검사 추가(현재 테스트 없음).
4. (`[차용]` S) pseudo-locale: `view.language='xx'`일 때 모든 사본을 `⟦…⟧`로 감싸 잘림을 QA에서 노출.
   `pseudo-localization.ts:6-11`의 3줄 구현을 그대로 개념 차용(코드 복사 아님).

`[제외]` i18next·1MB 카탈로그·7언어·번역 정책 테스트 인프라. TalkAbout은 사용자 2개 언어 UI로 충분하다.

---

## 5. 테스트 전략 — "테스트가 실제로 잡는 것"의 설계 (질문 5)

### 5.1 배치와 규모

| 층 | 위치 | 규모 | 근거 |
| --- | --- | --- | --- |
| 단위(colocated) | `src/**/*.test.ts(x)`, `*.spec.ts` | 13,424 | grep |
| E2E | `tests/e2e/*.spec.ts` | 598 spec + 1,037 파일(헬퍼/픽스처 포함) | `tests/e2e/` |
| 툴 자체 테스트 | `config/scripts/*.test.mjs` | 458 | `config/scripts/` |
| 모바일 | `mobile/vitest.config.ts` + `check:tests-typecheck` ratchet | — | `mobile/package.json` scripts |
| 픽스처 | `__fixtures__` 디렉터리 21개, `__mocks__` 7개 | — | 파일시스템 |
| 스냅샷 | `.snap` 3개, `toMatchSnapshot` 3곳 | — | grep |

**스냅샷을 거의 쓰지 않는다**는 점이 중요하다. Orca가 스냅샷 대신 쓰는 것은 (a) 순수 함수 단언, (b) 결과 타입 단언,
(c) 스크린샷 fixture(사람이 읽는 이미지)다.

### 5.2 IPC/Electron 경계 분리 — 3가지 기법

1. **정책을 순수 함수로 분리**한다.
   `notification-burst-cooldown.ts:24-37`(시간·맵만 받음), `agent-attention-policy.ts:48-93`(surface 인터페이스 주입),
   `usage-percentage-display.ts:18-30`. Electron import가 없다.
2. **의존성 주입 + 가짜 시계/플랫폼**으로 서비스를 테스트한다.
   `notification-delivery-service.ts:14-34`가 `now`, `platform`, `readAuthorizationStatus`를 주입받는다.
   테스트는 `makeRequest(...)` 하나로 12KB 분량의 게이트 조합을 검증한다
   (`src/main/notifications/notification-delivery-service.test.ts` 12,271 bytes).
3. **거부 사유를 반환값으로** 만들어 그 자체를 단언한다.
   `{delivered:false, reason:'cooldown'|'suppressed-focus'|…}` (`notification-settings-types.ts:72-85`).

### 5.3 E2E 범위와 물리적 제약

- 기본 프로젝트는 `electron-headless`(`package.json:155`), headful은 실제 픽셀이 필요한 경우만(`:177`).
- **창을 띄우거나 포커스를 훔치지 않는다**: `ORCA_BACKGROUND_LAUNCH=1` 강제, `show()/bringToFront()/app.focus()` 금지,
  예외는 Xvfb 호스팅 CI의 terminal-perf·IBus 두 픽스처뿐 (`tests/AGENTS.md:3-16`, `AGENTS.md:14-20`).
- golden spec 세트를 별도 스크립트로 고정(`package.json:156-176`): 세션 복원, 파일 링크, 워크트리 전환, 커밋/디프 등.

### 5.4 "잡는 것"을 설계하는 장치 — ratchet

- 기준선을 `.txt`로 커밋하고 **악화만 실패**시킨다: `max-lines`, `ts-nocheck`, `flaky-test-pattern`, `e2e-typecheck`,
  `focus-read`, `layout-writer`, `path-literal`, `host-list-render`, `main-sync-spawn` 등 20여 종
  (`package.json:48-62`; 기준선 파일 예: `config/flaky-test-pattern-baseline.txt` 135KB, `config/ts-nocheck-baseline.txt`).
- 즉 Orca의 품질은 "규칙 준수"가 아니라 **"나빠지지 않음"을 기계적으로 증명**하는 방식으로 유지된다.
- 테스트 안티패턴이 문서화되어 있고 AGENTS.md가 링크한다: 목(mock) 호출만 단언, 프로덕션 로직 재구현, 자체 mock이
  공급한 값 단언, 실제 시간/네트워크 대기 금지 (`docs/reference/test-antipatterns.md:3-25`).

### 5.5 TalkAbout에 옮길 것

| 항목 | 판정 | 비용 | 부착 위치 |
| --- | --- | --- | --- |
| 거부 사유 열거 반환 + 테스트 | `[채택]` | S | `talkabout/web/app.mjs`의 API 오류 처리(`:23`)와 `textFileError`(`:706`) |
| 의존성 주입 서비스(시계/플랫폼) | `[채택]` | S | `talkabout/web/state.mjs` `ViewState`가 이미 이 역할. `now`/`hidden` 주입만 추가 |
| ratchet(기준선 고정) | `[채택]` | S | `tools/run_safe_regression.py`에 "테스트 수 < 기준선이면 실패" 1줄 추가. 기준선은 `docs/STATUS.md:13` 수치 |
| fixture 스크린샷 루프 | `[차용]` | M | `docs/ui/` 스크린샷이 수동(`docs/UI.md:89-98`). 헤드리스 1장 캡처 스크립트로 대체 가능 |
| 598 E2E / 13k 단위 테스트 | `[제외]` | — | 규모 과잉. TalkAbout 60 테스트 예산에 맞지 않음 |

---

## 6. 문서 체계 + AGENTS.md 같은 에이전트용 규약 (질문 6)

### 6.1 4층 분업

| 층 | 위치 | 독자 | 근거 |
| --- | --- | --- | --- |
| 제품 문서 | `docs/site/content/docs/**.mdx` (Next.js + fumadocs, 58 페이지) | 최종 사용자 | `docs/site/README.md:56-62` |
| 디자인 정본 | `docs/STYLEGUIDE.md`(96줄) + `docs/styleguide/*.md` 딥다이브 5개 | UI 작업자/에이전트 | `STYLEGUIDE.md:11-19` |
| 엔지니어링 레퍼런스 | `docs/reference/*.md` 9개 (크로스플랫폼, git, 테스트 안티패턴, fixture 등) | 기능 담당자 | `docs/reference/` |
| 설계 노트 | `docs/*.md` (automated-setup-tab-design.md 등) | 작성자/리뷰어 | `docs/` 루트 |

- `docs/site`는 **배포 경계**다: "publication boundary"로 취급하고, 원본 소스에서 복사할 때 내부 참조·자격증명·
  제3자 미디어 권리를 리뷰한 뒤 승인된 페이지만 옮긴다(`docs/site/README.md:63-69`). 루트 워크스페이스에 넣지 않아
  앱 의존성 설치가 docs 빌드를 끌고 가지 않는다(`:8, 153-157`).
- `STYLEGUIDE.md`의 결정적 문장: **"토큰 값은 `main.css`가 정본, 이 문서는 역할과 규칙만"**(`:3`).
  문서와 코드가 같은 값을 두 번 갖지 않게 하는 선언이다. TalkAbout은 `style.css` 단일 파일이므로 그대로 적용 가능.
- 가이드가 침묵할 때의 **해결 순서**를 문서에 명시한다(`:89-96`): ① 인접 컴포넌트 ② 기존 프리미티브 ③ 토큰 정본
  ④ 그래도 안 되면 사용자에게 묻는다. 에이전트가 임의 발명을 하지 않게 만드는 장치다.

### 6.2 AGENTS.md 실제 내용과 가치 (69줄)

| 절 | 내용 | 왜 값어치가 있나 |
| --- | --- | --- |
| Useful Commands (`:1-6`) | `pnpm check:changed` 하나로 format+lint+typecheck+영향 테스트+ratchet | 에이전트가 **명령을 고민하지 않는다**. TalkAbout의 `python -B tools/run_safe_regression.py`에 대응 |
| Design System (`:8-10`) | STYLEGUIDE 준수, 토큰 정본 위치, 침묵 시 해결 순서 | "예쁘게" 같은 모호한 지시를 **참조 가능한 규칙**으로 바꾼다 |
| Electron UI Validation (`:12-20`) | 백그라운드 실행, 포커스 탈취 금지, CDP 스크린샷, fixture 우선 | 물리적 부작용(모니터 점유)을 문서가 통제. 브라우저 자동화에도 그대로 필요한 규칙 |
| Code Style (`:22-39`) | 재사용 우선, 주석은 why 1줄, `helpers/utils` 금지, `as` 캐스팅 금지 | 파일명 규칙이 **검색 가능성**을 지킨다. TalkAbout의 `app.mjs`(785줄)에 정면으로 필요한 규칙 |
| First Class Citizens (`:41-59`) | 크로스플랫폼·원격·모바일·폴더 워크스페이스 체크리스트 | 리뷰 누락 방지용 **강제 질문 목록** |
| Git/GitHub (`:61-65`) | 실패한 run은 `--failed`로 재실행, 성공 잡 보존 | CI 비용·시간 절약 규칙 |
| Tests (`:67-69`) | 저가치 테스트 추가 금지 + 안티패턴 링크 | 테스트 수 인플레이션을 막는 유일한 장치 |

추가로 디렉터리별 규칙을 분리했다(`tests/AGENTS.md:1-16`) — 전역 AGENTS.md가 200줄이 되지 않게 하는 방법.
에이전트 도구 설정도 저장소에 커밋한다: `.claude/settings.json:1-11`(11줄, 플러그인 마켓플레이스만).

### 6.3 TalkAbout 갭 — 확인된 사실

- 저장소 루트에 `AGENTS.md` **없음**, `CLAUDE.md` 없음, `.claude/` 없음, husky/lint-staged 없음(루트 파일 목록 확인).
- `docs/AGENT_GUIDE.md:1-3`은 **제품 안내**(참가 에이전트에게 주는 절차)이며 저장소 규약 문서가 아니다. 이름이 비슷해
  혼동하기 쉽다 — 이 문서에 저장소 규약을 섞으면 안 된다.
- TalkAbout 문서 체계는 이미 정본 선언이 있다: `docs/STATUS.md:1-4`가 "현재 상태의 단일 정본, 다른 상태 문서는
  스냅샷"을 선언한다. 이건 Orca보다 **명시적**이다(`[대조]`).

`[채택]` (비용 S) 루트 `AGENTS.md` 60줄 이내 + `tests/AGENTS.md` 15줄 이내. 반드시 포함할 것:
1. 검증 단일 명령: `python -B tools/run_safe_regression.py`(STATUS.md:24) — 그리고 "수치를 손으로 고치지 말고
   `.ci-results/result.json`을 인용" 규칙.
2. UI 변경 시 `docs/UI.md`의 어느 절을 갱신해야 하는지.
3. 금지: 확인하지 않은 것을 검증됨으로 쓰지 않는다(BRIEF §2와 동일 규칙 — 이미 코드베이스 문화로 존재).
4. 테스트 추가 기준(저가치 금지) — `test-antipatterns.md` 개념 차용, 파일 복사는 하지 않는다.

---

## 7. `artifacts` / `memory`가 실제로 저장하는 것 (질문 7)

### 7.1 `artifacts` — 로컬 산출물 저장소가 아니라 **클라우드 공유 publish 파이프라인**

| 저장물 | 포맷/위치 | 근거 |
| --- | --- | --- |
| 공유 레코드 | `{profileDir}/artifact-shares.json`, `version:2`, `lifecycleGeneration/Nonce`, `shares: {sourceKey: {slug, editToken, shareUrl, expiresAt, savedAt, scope}}`, 최대 **10,000** | `artifact-share-record-store.ts:23-41` |
| 생성 의도(intent) | `{profileDir}/artifact-create-intents/<sha256(sourceKey+scope)>.json`, 최대 **32 pending**, 디렉터리 mode 0700, durable write + fsync | `artifact-create-intent-store.ts:15-16, 26-34, 172-191` |
| 본문 | `contentType`은 `text/html` 또는 `text/markdown`만, content 최대 10MiB | `artifact-create-intent-store.ts:70-82, 162-164` |

동작:
- `publish()`: pending intent가 있으면 **재생(replay)**. 재생 결과 body가 현재 요청과 일치하면 그대로 성공,
  다르면 기존 slug에 `PUT`으로 갱신 (`artifact-publisher.ts:108-161`).
- 실패 분류: 4xx 중 `408/409/425/429`를 제외하면 intent를 **폐기**한다(재시도 무의미). 그 외는 보존
  (`artifact-publisher.ts:43-50, 232-244`).
- 수명: `expiresAt`을 서버 응답으로 갱신(`:194-201`), 404면 로컬 레코드 삭제 후 새로 생성(`:146-156`).
- 동시성: `sourceKey` 단위 / `slug` 단위 **직렬화 큐**(`runForSource`/`runForSlug`, `:58-72, 163-177`).
- 검색: 로컬 검색 인덱스 없음 — scope+sourceKey 직접 조회. (UI 검색은 **미확인**)

### 7.2 TalkAbout text artifact와 비교

| 축 | TalkAbout | Orca | 판정 |
| --- | --- | --- | --- |
| 버전 | `expected_revision` 낙관적 동시성 + **불변 version** + 채택(adopt) | 단일 원격 객체 `PUT` 갱신(버전 개념 없음) | `[대조]` TalkAbout 우위 |
| 중복 방지 | revision 충돌 → 409 → 최신 비교 | idempotency key + intent replay | 둘 다 존재, 목적이 다름 |
| 요청 유실 복구 | reference pull worker **journal transaction**이 cursor·queue 원자 저장(BRIEF §2) | create intent 파일 영속화 | `[대조]` TalkAbout 더 강함(서버 측) |
| 실패 폐기 규칙 | — | 4xx/5xx 구분 + retryable 코드 예외 | `[채택]` |
| 직렬화 | room row lock / commit 순서 | sourceKey·slug 단위 큐 | `[대조]` TalkAbout 더 강함 |

`[차용]` 가져올 것은 **데이터 모델이 아니라 절차**다(비용 S):
① 재시도 가능/불가 오류 코드 구분(408/409/425/429 예외 목록), ② 같은 대상에 대한 쓰기 직렬화,
③ "의도 저장 → 서버 확정 → 의도 삭제" 3단계와 그 사이 crash 복구.
부착 위치: `talkabout/attachments.py`, `talkabout/text_files.py`(현재 init→content→complete 3단계가 이미 있음,
`app.mjs:723`에서 확인됨)에 **intent 만료·정리 정책**만 추가하면 된다.

### 7.3 `memory` — 이름과 내용이 다르다 (중요)

`src/main/memory/`는 **에이전트 기억이 아니라 프로세스 RAM/CPU 통계**다.
`collector.ts:1-37`은 `exec`로 프로세스 목록을 긁어 host/worktree/app/session 버킷으로 집계하고,
`memory-snapshot-buckets.ts`로 히스토리를 쌓고 sweep한다. Windows 전용 수집기(`windows-process-resource-collector.ts`)도 있다.

에이전트 메모리는 Orca가 **소유하지 않는다**: "Claude의 `CLAUDE.md`와 Codex의 `AGENTS.md`는 그대로 둔다 — 에이전트의
것이다. Orca는 파일 탐색기에 보여줄 뿐이다" (`docs/site/content/docs/agents/hooks-memory.mdx:17-19`).
훅 설정만 Orca가 관리하고 on/off를 런타임에 반영한다(`:21-27`).

- `[제외]` `src/main/memory/*` — TalkAbout은 웹 서버이며 데스크톱 프로세스 관리자가 없다. RAM 대시보드는 무의미.
- `[차용]` 히스토리 버킷 sweep(`memory-snapshot-buckets.ts`)의 **상한+오래된 것 제거** 사상은
  `talkabout/retention.py`(43줄)와 `docs/RETENTION_BOUNDS.md`의 상한 설계 검토에만 참고. 비용 S.

### 7.4 `notebook` / `text-generation`

- `src/main/notebook/notebook-kernel.ts:1-25`: Jupyter kernel을 Python bridge로 띄우고 NDJSON 프레임으로 통신,
  stderr tail 4,000자, 종료 유예 5초. `python-environments.ts`가 환경 탐색. → `[제외]` TalkAbout 범위 밖(코드 실행 없음).
- `src/main/text-generation/`: 커밋 메시지·PR 설명·Jira 요약을 **agent CLI로 생성**하는 레이어. 파일이 30+개로
  분해되어 있다: 요청 조립(`commit-message-generation-request`), 모델 탐색 정책(`-model-discovery-policy`),
  취소(`-cancellation.test` 19KB), 실패 문구 정규화(`agent-failure-output.ts`, `source-control-agent-failure.ts`).
  → `[차용]` **생성 실패 문구를 별도 모듈에서 정규화**하는 분리. TalkAbout은 runtime 실패를 사용자에게 보여줄 때
  같은 분리가 필요하다. 부착 `talkabout/adapters.py` + `app.mjs:706` 패턴. 비용 S.

---

## 8. CI와 pre-commit (질문 8)

### 8.1 Orca

| 계층 | 내용 | 근거 |
| --- | --- | --- |
| 워크플로 수 | 68개 (release 110KB, pr 75KB 포함) | `.github/workflows/` |
| PR 경로 선택 | `code_paths` 잡이 변경 경로로 `static_analysis/typecheck/test/mobile_web_app/...` 불리언 출력 → 비싼 잡 스킵 | `pr.yml:22, 1497-1576` |
| 최종 게이트 | `verify` 잡이 **success와 skipped를 구분**해 집계. 안 돌았어야 하는 잡이 돌면 실패 | `pr.yml:1554-1576` |
| 단위 테스트 | `unit-plan.yml` → shard 매트릭스, warm/cold module cache, phase diagnostics | `unit-tests.yml:1-183` |
| E2E | 별도 `e2e.yml`(35KB), PR에서는 **advisory**(차단 안 함) | `pr.yml:1521` 주석 |
| 린트 | `lint` = oxlint(다중 프로필) + ast-grep + anti-slop + type-aware + **ratchet 20여 종** | `package.json:19` |
| pre-commit | `.husky/pre-commit` 2줄: `pnpm exec lint-staged` | `.husky/pre-commit:1-2` |
| lint-staged | js/ts에 oxlint×3 + ast-grep + oxfmt, json/css/md에 oxfmt | `package.json:370-381` |
| handoff 검증 | `check:changed`가 typecheck+관련 테스트를 **먼저** 스케줄하고 format/lint를 채움. warm vitest record 95% 조건 | `config/scripts/check-changed.mjs:21, 103-121, 125-221` |

핵심 통찰: **커밋 훅에는 타입체크/테스트가 없다**. 느린 검사는 `check:changed`(handoff 시점)로 미루고, 커밋은
린트+포맷만(빠름). "Before handoff"라는 AGENTS.md 문장이 이 분업의 계약이다(`AGENTS.md:3`).

### 8.2 TalkAbout

- 단일 워크플로 `safe-regression.yml`: `windows-latest`, cron 아님, `timeout-minutes: 30`, `cancel-in-progress: true`,
  모든 액션 **SHA 고정**, `persist-credentials: false`, pip은 `.github/ci-constraints.txt`로 고정.
  PostgreSQL 18.6 바이너리를 내려받아 `ci_postgres_fixture.ps1`로 create → 테스트 → `if: always()` stop.
- 실행 본체: `python -B tools/run_safe_regression.py --postgres-config ...`
  이 러너는 (a) 자식 Python 프로세스에서 pytest 실행(`run_safe_regression.py:25-45`),
  (b) `node --test tests/*.mjs`(`:72`), (c) **환경변수에서 비밀류 키 제거**(`:66-68`),
  (d) 프로세스 소유/정지 확인(`:77-82`), (e) `.ci-results/{cases.jsonl,result.json}`에 증거 저장(`:84-88`).

`[채택]` 개선 (모두 S, 부착 `.github/workflows/safe-regression.yml`, `tools/run_safe_regression.py`):
1. **린트/문법 게이트 부재**: `node --check`를 `tests/*.mjs` 및 `talkabout/web/*.mjs`에 적용, Python은
   `python -m compileall -q talkabout` 정도라도 1스텝 추가. (Orca의 preflight 대응 최소선)
2. **job 분리**: 현재 python과 node가 한 잡 안에서 순차 실행(`run_safe_regression.py:70-73`)된다. 두 job으로 나누면
   30분 예산의 실패 반경이 절반이 된다. 단, `ManagedProcess` 종료 검증은 유지.
3. **ratchet 연결**: 이미 `result.json`에 counts가 있다(`:86`). 기준선(636/196)을 파일로 커밋하고
   "기준선보다 적으면 실패"를 추가 — `docs/STATUS.md:13`의 수치를 문서 산문에서 기계 검사로 옮기는 것.
4. **경로 필터**: `docs/**`만 바뀐 PR에서 전체 회귀를 돌리는 건 낭비다. Orca `pr.yml:22`의 path selection 개념만 차용.

`[대조]` TalkAbout이 이미 더 잘하는 것: (a) 비밀 환경변수 제거(`:66-68`), (b) 픽스처를 `always()`로 정리,
(c) 러너가 만든 JSON 증거를 `docs/validation/` 문화와 연결. Orca에는 이런 "증거 파일" 규율이 상대적으로 약하다
(텔레메트리·대시보드 중심).

---

## 9. TalkAbout 구체 개선점 (질문 9) — 파일:라인

| # | 위치 | 문제(확인된 사실) | 개선 | 비용 | Orca 근거 |
| --- | --- | --- | --- | --- | --- |
| 1 | `talkabout/web/style.css:1693-1706` | reduced-motion 미디어 쿼리 안에 `.stage-caption` 규칙이 통째로 중복 삽입됨(`:897-910`과 동일). `body::before`가 pill로 재배치됨 | 중복 블록 삭제, `body::before{animation:none!important}` 명시 | S | `STYLEGUIDE.md:77` |
| 2 | `style.css:130` | `aurora 24s infinite`가 reduced-motion에서 정지하지 않음 | 위 1과 함께 수정 | S | `ux-review-rubric.md:30` |
| 3 | `style.css:981, 989, 1087` | `.motes`, `.prop-lights`, `.live-dot` 애니메이션이 `:1707` 규칙 범위 밖 | 정지 대상 명시 + 검사 스크립트 | S | 〃 |
| 4 | `state.mjs:1-4, 17-19, 79-…` | `labels.ko/en`이 20개 `Object.assign` 블록 + 막판 단일 키 대입으로 흩어짐. 3번째 언어 비용 폭발 | `{key:[ko,en]}` 단일 표 | M | `ui-locale.ts:14` |
| 5 | `app.mjs:11` | `labels[lang][key]`가 `undefined`를 그대로 렌더 | fallback + `__MISSING__` 표식 | S | `main-i18n.ts:121-126` |
| 6 | `app.mjs:12` | `notice()` 단일 슬롯. 성공·거부·일시오류가 같은 자리, 자동 소멸 없음, 이전 메시지 덮어쓰기 | severity별 영역 + auto-dismiss(3s/지속) 분리 | S | `STYLEGUIDE.md:78` |
| 7 | `app.mjs:671` | `document.hidden`이면 폴링 정지 → **백그라운드 탭에서 완료를 영원히 모름** | 완료 시 title badge + Notification API(hidden일 때만) | M | `notification-delivery-service.ts:134-142` |
| 8 | `app.mjs:566, 568` | `roomCallBudget` 결과가 onboarding 명령 문자열에만 쓰임. 방 화면 상시 예산 표시 없음 | 방 헤더 상태 라인에 "예산 사용/잔여" | M | `rate-limit-types.ts:88-133` |
| 9 | `app.mjs:706` | 오류 코드→문구 매핑이 **텍스트 파일 API에만** 존재(좋은 패턴인데 국소적) | runtime/task/lease 실패에 동일 매핑 확장 | S | `usage-error-copy.ts:46` |
| 10 | `app.mjs:733` | 한 줄에 `action`×상태 6개 OR 조건. 상태 전이 표가 코드에 없음 | `ViewState`(또는 `workflowState` `:550`)로 추출 후 테스트 | S | `agent-attention-policy.ts:30-39` |
| 11 | `index.html:2` | `lang="ko"` 고정, 런타임에만 갱신(`app.mjs:15`) | 첫 페인트 전 언어 결정 | S | `ui-locale.ts:60-72` |
| 12 | `docs/UI.md:83-87` | 접근성 절이 "목표" 서술. 검증 명령·테스트 링크 없음 | "린트/테스트로 강제되는 것"과 "문서뿐인 것" 열 분리 | S | `oxlint-design-system.json:26-43` |
| 13 | `docs/UI.md:3` | 확인일만 있고 변경 시 갱신 트리거가 없음 | `tests/AGENTS.md`에 "UI 변경 시 UI.md 갱신" 명시 | S | `AGENTS.md:8-10` |
| 14 | `tests/ui-review.test.mjs` 전반 | synthetic DOM 하네스가 훌륭하나 **CSS 미검사** | `style.css` 파싱 검사 1개 추가(§3.3) | S | `oxlint-design-system.json` |
| 15 | `docs/STATUS.md:13` | 테스트 수치가 산문. 실제 실행 결과(`.ci-results/result.json`)와 자동 연결 없음 | 러너가 STATUS 블록을 갱신하거나 ratchet로 대체 | S | `config/*-baseline.txt` |
| 16 | `.github/workflows/safe-regression.yml` | SHA 고정·constraints는 훌륭. **lint/typecheck/format 게이트 없음** | `node --check` + `compileall` + ratchet 1스텝 | S | `package.json:19` |

TalkAbout이 **이미 Orca보다 낫거나 동등한 것** (베끼지 말 것):
- 이벤트 순서·revision 다층: `expected_revision`/`generation`/`membership_revision`을 UI가 직접 검증(`app.mjs:733`의
  `data-action` disabled 조건, `:547`의 `expected_revision`). Orca에는 대응물이 없다(`[대조]`).
- 합성 픽스처를 실제 공급자 검증과 **명시적으로 구분**(`app.mjs:544`의 `startSyntheticReady`,
  `state.mjs:124-125`의 `syntheticTestOnly` 라벨) — Orca 문서도 "합성과 실증을 구분"하지만 UI 라벨 수준 강제는 TalkAbout이 더 명확.
- 보안 민감 값의 60초 자동 소거(`app.mjs:672`)와 "원문 재조회 불가" 문구(`state.mjs:18-19`). Orca에서 대응 확인 못함.

---

## 10. 이식 판정 요약 (질문 10)

| Orca 자산 | 판정 | 이유 |
| --- | --- | --- |
| `src/main/notifications/notification-delivery-service.ts` | `[제외]` | `BrowserWindow`, `tray`, `dock`, macOS 권한 API 전제 (§1.1) |
| `src/main/tray/*`, `src/main/dock/*` | `[제외]` | 데스크톱 전용(비트맵 합성, `app.dock.setBadge`) |
| `src/main/updater/*`, `src/main/menu/*` | `[제외]` | Electron 앱 수명주기 전용 |
| `src/main/notebook/*` | `[제외]` | Jupyter 커널·코드 실행. TalkAbout 범위 밖 |
| `src/main/memory/*` | `[제외]` | 프로세스 RAM 통계. 웹 서버에 무의미 (§7.3) |
| `src/renderer/**` 컴포넌트 | `[제외]` | React+shadcn+Radix+xterm. TalkAbout은 무프레임워크 DOM+SVG |
| `src/main/artifacts/*` | `[차용]` | 클라우드 publish 계약은 다름. **intent 재생·오류 폐기·직렬화**만 (§7.1) |
| `src/main/i18n/*` + 7언어 카탈로그 | `[제외]` | i18next + 1.1MB×7 + 정책 스크립트 25종 과잉 |
| `config/oxlint-design-system.json` | `[차용]` | shadcn 플러그인은 못 씀. "토큰 외 색 금지"를 CSS grep 테스트로 대체 |
| `docs/STYLEGUIDE.md` 구조(역할 문서 + 토큰 정본 분리) | `[채택]` | 프레임워크 무관. TalkAbout `style.css`가 이미 정본 |
| `AGENTS.md` + `tests/AGENTS.md` 패턴 | `[채택]` | 도구 무관. TalkAbout에 부재 (§6.3) |
| ratchet(기준선 파일) | `[채택]` | 언어 무관. `result.json` counts와 결합 가능 |
| fixture 스크린샷 루프 | `[차용]` | Playwright/Chromium 의존. TalkAbout은 헤드리스 1장 캡처로 축소 |
| 알림 억제 **규칙**(5초 쿨다운·포커스 억제·결과 사유 열거) | `[채택]` | 순수 규칙. 부착 `app.mjs` |
| unread 선기록 → 배너 후행 | `[채택]` | 순서 규약. 부착 `app.mjs` + `index.html` |

---

## 11. 모순과 한계 (숨기지 않음)

1. **문서 vs 구현**: `docs/site/content/docs/notifications.mdx:9`는 "system notification, sound, and a chip"이라 하지만
   Windows taskbar attention은 저장소에 없다(§1.5). 문서는 플랫폼별 차이를 밝히지 않는다.
2. **AGENTS.md "Avoid Comments"(`:28-31`) vs 실제 코드**: 코드는 `// Why: …` 한 줄 주석을 광범위하게 쓴다
   (`notification-delivery-service.ts:69, 88, 144`, `announced-notification-registry.ts:1-2`).
   규칙은 "how 금지, why 1줄"로 운용 중이다. 문자 그대로 읽으면 오독한다.
3. **영어 fallback 정본 vs 한국어 우선**: Orca는 영어 문자열이 코드에 있고 카탈로그는 번역본(`main-i18n.ts:121-126`).
   TalkAbout은 `labels.ko`가 사실상 정본이다(문구가 한국어로 더 길고 상세함, `state.mjs:2`). 두 방향을 섞으면
   "어느 쪽이 진실인가"가 붕괴한다. TalkAbout은 **ko 정본 + en fallback**을 명시해야 한다.
4. **미확인 항목**: `src/shared/window-shortcut-policy.ts`(10KB), `status-bar-usage-collapse.ts`,
   `src/main/telemetry/*` 본문, `mobile/` 앱 코드, Orca artifacts UI 검색, `docs.yml` 배포 게이트 본문.
   이 보고서는 위 항목에 대해 어떤 주장도 하지 않았다.
5. **라이선스**: Orca는 MIT(`LICENSE`). 이 보고서는 **아이디어·규약만** 차용했고 코드 복사는 없다.
   향후 코드를 복사할 경우 저작자 표시 + 라이선스 고지 + `THIRD_PARTY_NOTICES.md` 갱신이 필요하다
   (TalkAbout은 이미 해당 파일을 운영 중).

---

## 12. Top 5 실행 제안

| 순위 | 제안 | 태그 | 비용 | 부착 위치 | 즉시 확인 가능한 결과 |
| --- | --- | --- | --- | --- | --- |
| 1 | **완료 통지 최소 세트**: task 완료/실패 시 title badge + (hidden일 때) Notification API. 억제는 `{delivered,reason}` 반환 + 5초 쿨다운 | `[채택]` | M | `talkabout/web/app.mjs`(폴링 지점 `:671`), `index.html` | 백그라운드 탭에서 완료를 인지. 억제 사유가 테스트로 고정됨 |
| 2 | **reduced-motion 결함 수리 + CSS 회귀 검사**: `style.css:1692-1709` 오염 제거, 미차단 animation 4곳 정지, `tests/`에 CSS 검사 1개 | `[채택]` | S | `talkabout/web/style.css`, `tests/` | 접근성 설정 사용자에게 배경이 깨지지 않음. 같은 부류 재발 차단 |
| 3 | **에이전트용 저장소 규약 도입**: 루트 `AGENTS.md`(60줄) + `tests/AGENTS.md`(15줄). 검증 명령·정본 포인터·금지사항만 | `[채택]` | S | 저장소 루트, `tests/` | 새 세션/새 에이전트가 같은 명령·같은 규칙으로 시작 |
| 4 | **i18n 표 통합 + 누락 가시화**: 20개 `Object.assign` → 단일 표, `t()` fallback, `data-i18n` 키 ⊆ labels 검사, pseudo-locale | `[채택]` | M | `state.mjs`, `app.mjs:11`, `tests/ui-state.test.mjs` | 3번째 언어 추가 비용이 선형이 됨. `undefined` 렌더 제거 |
| 5 | **상태 표시 계층**: 방 예산/lease/연결을 상시 상태 라인으로, 실패 문구를 코드→문구 매핑으로 통일 (60/80 임계 톤 차용) | `[차용]` | M | `app.mjs:568, 706`, `style.css` 토큰 | "왜 안 되는지"가 화면에 상시 존재. 문서(`UI.md`)와 코드가 같은 언어 사용 |

각 항목은 서로 독립적이므로 1·2·3(모두 S~M)을 먼저 끝내고 4·5를 이어가는 순서를 권한다.
1번과 4번은 `app.mjs`를 동시에 만지므로 **같은 사람이 순차 수행**해야 충돌이 없다.
