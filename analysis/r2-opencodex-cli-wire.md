# r2 — opencodex CLI 와이어/인증/quota 분석

분석 대상: `opencodex/` (lidge-jun/opencodex, HEAD `9f0d40a`, `package.json` v2.92.0, MIT)
비교 기준: TalkAbout `talkabout/agent_runtime.py`(codex app-server·exec-json / claude stream-json / grok ACP), `adapters.py`, `providers.py`, `http_runtime.py`, `processes.py` · 1라운드 `analysis/01-providers.md`(Orca)
작성: providers-analyst · 태그/비용/부착위치/최적 저장소 규칙은 `analysis/BRIEF-R2.md:57-67` 준수 · 표의 "최적 저장소"는 **채택/차용 항목의 1순위 출처**이며, [제외] 행은 `—`로 두고 같은 행에 이유를 적었다(hermes-agent는 이 보고서 범위 밖 — §11.3).

## 0. 결론 요약

1. **opencodex의 정체는 "agent"가 아니라 "wire takeover 프록시"다.** stock CLI의 **설정 파일 한 줄**(Codex `openai_base_url`, Grok `[model_providers]`) 또는 **프로세스 env 두 줄**(Claude Code `HTTPS_PROXY`+`NODE_EXTRA_CA_CERTS`)만 바꿔 트래픽을 자기 루프백으로 끌어온다(`src/codex/inject.ts:94-106`, `src/claude/intercept/settings.ts:20-48`, `src/grok/inject.ts:41-47`). **바이너리를 패치하거나 hook을 설치하지 않는다**(Codex shim은 예외적 보조수단, §1.5).
2. **Claude Code 우회는 MITM이다.** 자체 CA를 `node:crypto`만으로 발급해 `NODE_EXTRA_CA_CERTS`로만 신뢰시키고, CONNECT 프록시에서 `api.anthropic.com:443`만 자기 TLS 리스너로 splice한다. 실제로 가로채는 경로는 **2개**(`POST /v1/messages`, `/v1/messages/count_tokens`)뿐이고 나머지는 원업스트림으로 그대로 relay한다(`src/claude/intercept/connect-proxy.ts:8-22`, `listener.ts:8-20`). 이 "최소 경로만 개입" 원칙이 TalkAbout에 가장 값진 부분이다.
3. **인증은 2값이 아니라 3값(`present/absent/unknown`)이고, unknown은 절대 proxy로 해석하지 않는다.** 읽기 실패(keychain 거부·권한·손상 JSON)를 absent로 취급하면 정상 구독자가 프록시 모드로 전환되는 사고가 나기 때문이다(`src/claude/auth-detect.ts:1-11`, `auth-mode.ts:26-52`). 또한 자기 토큰을 네이티브 인증 근거로 다시 읽는 **피드백 루프 차단**(`PROXY_MARKER`, `ownTokens`)이 있다(`auth-detect.ts:30-35,139-160`).
4. **quota 모델링이 3개 저장소 중 가장 정교하다.** 중립 window 목록(`5h/weekly/monthly/custom:*`), "absence ≠ 0%", **429를 4종(shared-quota / family-quota / transient-rate / request-scoped-unknown)으로 분류**, **rolling window의 deadline creep을 reset으로 오인하지 않는 순수 검출기**, 소금 친 accountTag 기반 idempotence key(`src/quota/reset-detector.ts:40-110,141-282`, `src/oauth/anthropic-rate-limit-policy.ts:5,17-26`). TalkAbout의 `usage_limit_reached{window,resetsAt}`(1라운드 제안)은 이 모델로 **정밀화**해야 한다.
5. **계정 풀은 "자격증명 레코드 + 세대 + 리스/쿨다운 + 유한 회전"으로 구성된다.** Codex는 `codex-accounts.json`에 `{generation, credential{accessToken,refreshToken,expiresAt,chatgptAccountId}, sourceAuthPath, sourceSubject}`를 저장하고 60초 skew/60초 stale 락으로 refresh를 한 번만 수행한다(`src/codex/account-store.ts:30-33,44-46,63-92`). 회전은 요청당 최대 3회·최대 6계정으로 상한되고(`src/oauth/generic-account-failover.ts:52,60`), **잔여 headroom으로 정렬**한다(`src/oauth/account-quota-rank.ts:1-12`). TalkAbout의 서버 권한 모델(계정 scope ∩ membership ∩ credential scope)과는 **층이 다르다** — 그대로 이식 금지, 쿨다운 출처 어휘만 이식.
6. **프로토콜 계약은 "버전 + 닫힌 reason code + feature×hop disposition + 순수 planner + shadow plan"으로 고정**된다(`src/protocols/contract.ts:11-12,55-84`, `features.ts:18-39,74-131`, `plan.ts:1-12,146-199`). 버전 드리프트는 **네이티브 CLI 명령에 위임**하는 방식으로 피한다(`src/codex/features.ts:1-27`: "Every other upstream feature flag is delegated to the native `codex features` command ... must not be hardcoded here").
7. **모순/R1 정정은 §10에 모았다.** 핵심 3개: (a) R1의 "Orca에 `codex exec --json` 파서 없음"은 opencodex도 마찬가지 → **3개 저장소 중 exec JSONL을 다루는 건 TalkAbout뿐**(R1 결론 강화), (b) R1의 "계정 신원은 검증 불가" 전제는 opencodex의 `sourceSubject`/`sourceAuthPath`처럼 **출처 파일에서 읽는** 설계로 반박 가능, (c) R1의 window 어휘(`session/weekly/weeklyModel/credits`)와 opencodex의 `5h/weekly/monthly/custom`은 **불일치** → 통합 어휘 필요.

### 라이선스 (직접 확인)

`opencodex/LICENSE:1-3` — **MIT License, Copyright (c) 2026 opencodex contributors**. 복사·수정·배포·재라이선스 가능하나 저작권 고지와 허가문을 포함해야 한다(`LICENSE:9-13`). 참고: `CREDITS.md` 38KB가 별도 존재하며, `AGENTS.md`는 보안 연구물을 tracked 디렉터리에 남기지 말라고 규정한다(비밀유출 관리 규율). 본 보고서는 **읽기만** 했고 clone은 수정하지 않았다.

### 규모/표본 판정 (devlog 5,305개 전수 금지)

- 12,883파일 중 `.md` 5,305개는 대부분 `devlog/`. 나는 **목차 표본만** 읽었다: `devlog/_plan/` 최근 단위 12개(예: `261013_codex_context_muse_wonder`, `261014_antigravity_direct_protocols`, `261021_fallback_off`, `261021_link_apply_parity`, `261023_sweep`)와 `structure/INDEX.md:1-40`의 Tier1~2 문서 목록. 판정: devlog는 **설계 근거가 코드 주석에 이미 요약**되어 있어(예: `inject.ts:94-104`가 devlog 없이 이유를 말함) TalkAbout 이식에는 코드+structure 문서로 충분하다. 추가 정독 가치는 [제외].
- 실제 분석한 소스: `src/{codex,grok,claude,oauth,quota,quota-pool,protocols,adapters/anthropic*,remote-control,bridge}` 중 **약 40파일**(전수 아님). `src/server/`(94K줄)·`src/adapters/`(66K줄)는 계약 헤드만 봤다.

---

## 1. 주입(inject) — 각 CLI의 정확한 개입 지점

### 1.1 한눈 표

| CLI | 건드리는 것 | 메커니즘 | 복원/소유권 | 위험 |
| --- | --- | --- | --- | --- |
| **Claude Code** | `~/.claude/settings.json`의 `env` 3키 | `HTTPS_PROXY=http://opencodex:<token>@127.0.0.1:<port>`, `NODE_EXTRA_CA_CERTS=<own>/ca.pem`, `NO_PROXY=<기존>,127.0.0.1,localhost,::1,0.0.0.0` | **마커 키 없음. 값으로 소유 판정**: `NODE_EXTRA_CA_CERTS`가 자기 CA 경로면 ours (`settings.ts:20-48`). `NO_PROXY`는 별도 receipt로 원복(`settings-no-proxy.ts`) | 로컬 CA 신뢰 = TLS 개입. 루프백 MCP까지 프록시로 보내면 405(이슈 #6936) → NO_PROXY 필수 |
| **Claude Desktop** | 3p config library + Claude Desktop remote store/profile | `desktop-3p.ts`가 프로필 렌더·원격 스토어 적용(`applyRemoteDesktopStore`), gateway/1M capability를 스키마에 맞춰 기록(`desktop-3p.ts:1-20`, `:38-44`, `:66-83`) | 원격 스토어 inspect/cleanup/restore 경로 분리(`desktop-remote-store.ts`) | Desktop 앱 버전별 스키마 드리프트, 1M 컨텍스트 오표기 금지(`long-context.ts` 경유) |
| **Codex CLI/App** | `$CODEX_HOME/config.toml` **root 한 줄** `openai_base_url` | Codex 내장 provider override(codex-rs `config_toml.rs`)로 **provider 이름을 바꾸지 않고** base URL만 루프백으로 변경(Design B). thread는 `model_provider="openai"` 유지 → history 재매핑 불필요(`inject.ts:94-106`) | **journal**(주입 전 baseline·base_url·realtime ws url 기록) + write lock + pre-image restore(`inject.ts:37-45,192`; `inject/plan.ts:1-9`) | 비루프백 bind에서는 내장 provider가 커스텀 헤더를 못 실어 `[model_providers.opencodex]` 표 주입으로 폴백(`inject.ts:98-104`) |
| **Grok Build** | `~/.grok/config.toml`(또는 `GROK_HOME`)의 **fenced managed block** | `[model_providers.opencodex]`(base_url/api_backend/api_key/extra_headers) + 각 `[model.<alias>]`가 `model_provider`로 참조. Grok 0.2.109+ 상속 동작에 의존(`inject.ts:43-47`, `:356-357`) | BEGIN/END 마커, orphan 감지, 소유 predicate(우리 api_key literal + 루프백 base_url + `x-opencodex-grok`) (`inject.ts:41,483-497`). 상태 조회는 **읽기 전용**(`grok/status.ts:1-11`) | 상속 의미가 Grok 버전에 종속. 비루프백이면 주입 거부(`skippedReason:"non-loopback"`, `inject.ts:30-37`) |

### 1.2 Claude Code — "최소 경로만 MITM"

- 진입: Claude Code는 `HTTPS_PROXY`를 존중해 모든 업스트림에 `CONNECT host:443`을 연다. 프록시는 **`api.anthropic.com`만** 자기 TLS 리스너로 splice하고 나머지는 blind relay한다(`connect-proxy.ts:8-22`). 인증은 HTTP Basic 프록시 자격증명(`opencodex:<token>`)이며 토큰은 owner-only 파일(43바이트 base64url, symlink/FIFO 거부, dev/ino 재검증)(`proxy-auth.ts:1-60`).
- TLS: CA와 leaf를 `node:crypto`(ECDSA P-256) + 수제 DER로 발급, **OS 신뢰 저장소에는 절대 넣지 않고** `NODE_EXTRA_CA_CERTS`로 그 프로세스만 신뢰(`local-ca.ts:6-19`).
- 개입 범위: `INTERCEPTED_PATHS = {"/v1/messages", "/v1/messages/count_tokens"}`의 POST만 로컬 라우터로 재작성하고, 그 외(usage/feedback/model 목록)는 **그대로 업스트림에 relay**해 "1st-party 설치처럼" 동작 유지(`listener.ts:8-20,35`).
- Chromium(Desktop 앱)이 보내는 CONNECT UA(`Mozilla/`)를 Desktop용 릴레이(claude.ai)로 보내고, UA 없는 Claude Code는 api.anthropic.com intercept로 보낸다(`connect-proxy.ts:60-70`). 주석이 스스로 말한다: "This is a routing hint, not a trust boundary".

### 1.3 Codex — config 한 줄 + journal

핵심 코드 그대로:

```ts
// src/codex/inject.ts:94-100
 * Design B (2026-07-06): loopback installs no longer re-tag the provider. Instead of
 * `model_provider = "opencodex"` + a `[model_providers.opencodex]` table, we set the official
 * built-in override `openai_base_url` (codex-rs config_toml.rs) so codex's own `openai`
 * provider points at the proxy. Threads keep `model_provider = "openai"`, so history never
 * needs remapping or restore.
```

주입은 **순수 변환 계획 → 락 → 원자 쓰기 → 저널** 순서다. 계획은 "파일시스템 변경 없이" 파생되고, 락 안에서 다시 파생되어 커밋 바이트가 reconcile 결과와 어긋나지 않게 한다(`src/codex/inject/plan.ts:1-9`). restore는 journal의 pre-image로 되돌린다(`inject.ts:37-45`). 실시간 ws도 별도 키(`experimental_realtime_ws_base_url`)로 같은 방식(`injected-marker.ts:44-53`).

### 1.4 Grok — fenced block + 소유 predicate

```ts
// src/grok/inject.ts:43-47
// Grok 0.2.109 (2026-07-21) shipped working [model_providers.<id>] inheritance: base_url,
// api_backend, api_key, and extra_headers declared on the provider are applied to inference
// routing for inheriting models (verified in grok-build's with_provider_defaults →
// resolve_model_list → sampling_config_for_model → SamplingClient chain). We emit one shared
// [model_providers.opencodex] table and each [model.*] references it.
```

소유 판정이 인상적이다: api_key literal만으로는 부족하고 **루프백 base_url + durable marker(`x-opencodex-grok`) 또는 재직렬화된 child**까지 요구한다. "루프백 base_url만으로는 부족하다 — 자기 model을 로컬 프록시로 겨누는 것은 정당한 사용"이라는 주석(`inject.ts:483-497`)이 오탐 경계를 설명한다.

### 1.5 codex shim — 예외적 보조수단

Codex 런처 자체를 교체하는 **autostart shim**도 있다. shim 마커는 `"opencodex codex autostart shim"`이고 revision 마커로 구버전을 판별한다(`src/codex/shim-templates.ts:7-15`). 설치 시 원본 런처를 `.opencodex-real`로 rename해 보존하며(`app-server-processes.ts:44-56`), POSIX overlay·Windows PowerShell shim·fingerprint 검증·restore lock까지 별도 모듈로 분리되어 있다(`src/codex/shim.ts:1-30` 임포트 목록). 목적은 Codex 실행 시 프록시 서비스를 자동 기동하는 것이며(마커 문자열 자체가 근거), **wire 우회의 주 수단이 아니다**. TalkAbout에는 [제외](바이너리 변조는 무결성·업데이트 리스크).

### 1.6 TalkAbout 대조 및 적용 판단

TalkAbout은 주입하지 않는다 — 매 호출 `subprocess` + argv로 직접 실행하고, `--ignore-user-config`/`--strict-config`/`--tools ''`로 **설정을 오히려 차단**한다(`agent_runtime.py:550-572`). 즉 두 프로젝트는 정반대 철학이다.

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| "최소 경로만 개입" 원칙(2개 엔드포인트만 변조, 나머지 verbatim relay) | **[차용]** | S | `agent_runtime.py`의 파서 정책 문서화 + `docs/PROVIDERS.md` | **opencodex** (실증된 규율) |
| 설정 소유권 "마커가 아니라 값으로 판정"(CA 경로/루프백 URL) | **[채용]** | S | `codex_sessions.py`의 profile/scope digest 설계 | opencodex |
| 주입→journal→restore 3단(쓰기 전 baseline, 원자 쓰기, 복원) | **[차용]** | M | 이미 TalkAbout 방향과 반대(차단)이므로 도입 안 함 | — |
| Claude Code `HTTPS_PROXY`+자체 CA MITM | **[제외]** | L | — | 이유: TalkAbout은 자식 프로세스 env allowlist만으로 도구·설정을 차단하는 설계이고, MITM은 자식 격리 원칙과 충돌. 또한 POSIX 차단(`processes.py:1`) 상태에서 TLS 프록시 추가는 공격면만 늘림 |
| Codex `openai_base_url` 리다이렉트 | **[제외]** | M | — | 이유: TalkAbout은 이미 `exec`/`app-server` wire를 직접 파싱해 관측 가능. 프록시를 끼우면 "누가 말했는가" 증거 사슬에 중간자를 하나 더 넣게 됨(BRIEF 31행 위배 위험) |
| Grok fenced block 주입 | **[제외]** | M | — | 이유: TalkAbout Grok은 ACP 클라이언트 경로이며 config 주입이 아니라 **ACP 응답 ID 규율**이 관문(§10 모순) |

**핵심 판단: opencodex의 inject는 TalkAbout에 이식 대상이 아니다. 이식 대상은 inject를 가능하게 만든 "관측 계층"(인증 3값·quota·429 분류)이다.**

---

## 2. 인증 — 감지·전환과 credential domain

### 2.1 Claude: 3값 presence + 4 소스 + 피드백 루프 차단

```ts
// src/claude/auth-detect.ts:1-11 (요약)
 * THREE values, not two. `unknown` means a source could not be READ (denied keychain,
 * permission error, corrupt JSON) — treating that as `absent` would silently flip a
 * subscriber into proxy mode ... The resolver maps unknown to the historical default
 * (subscription), never to proxy.
```

- 소스 4개: `claude-json-oauth`(`.claude.json`의 `oauthAccount.emailAddress` 존재), `claude-credentials-file`, `macos-keychain`(**메타데이터만: `security find-generic-password -s`에 `-g`/`-w` 미사용, exit code 44=absent**), `exported-env`(`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`)(`auth-detect.ts:18-28,101-160,184-220`).
- **자기 출력을 사용자 인증으로 재독하지 않는다**: `PROXY_MARKER="opencodex-proxy"`와 `ownTokens`(opencodex이 export한 admission key)를 제외(`auth-detect.ts:35,56-70,139-160`). 주석: "counting it would make a proxy-mode launch look authenticated on the NEXT launch"(149-152).
- 홈 해석: `os.homedir()`가 아니라 **호출자가 준 env의 HOME/USERPROFILE 우선**, 그 다음 OS 값(`auth-detect.ts:91-99`). 이유: 격리 프로필 probe가 실제 사용자 파일을 읽는 사고 방지.
- 모드 해석: `auto|proxy|subscription` 3-state. manual은 detector를 영구 우회, `unknown → subscription`(fail-safe)(`auth-mode.ts:26-59`).

### 2.2 credential domain — "어디로 보내면 그 자격증명이 유효한가"

```ts
// src/protocols/opaque-state.ts:27-38
/** Anthropic's own API host. Nothing else is first-party, including look-alike subdomains. */
export const FIRST_PARTY_ANTHROPIC_HOST = "api.anthropic.com";
export type CredentialAuthClass = "key" | "oauth" | "forward" | "local" | "unknown";
export interface CredentialDomain {
  readonly host: string;
  readonly authClass: CredentialAuthClass;
  readonly firstPartyAnthropic: boolean;
}
```

first-party 판정은 **https + host 정확 일치 + 기본 포트 + userinfo 없음**까지 요구한다(64-69행). 이 domain이 다르면 opaque reasoning(signature/redacted_thinking)을 **본문에서 제거**한다(110-131행). 이유: signature는 발급한 배포판+자격증명만 검증 가능. TalkAbout `agent_runtime.py`는 `thinking_delta`/`signature_delta`를 무시만 하고(938-939) 재전송하지 않으므로 동일 문제가 없지만, **"자격증명 등급×호스트"를 계약으로 두는 사고방식**은 계정 전환 설계에 유용하다.

### 2.3 TalkAbout 대조 — `--auth-mode native|api_key`와 Grok gating

| 축 | TalkAbout 현재 | opencodex | Orca(1라운드) |
| --- | --- | --- | --- |
| 감지 시점 | `probe()`가 **env key 존재**만 봄(`agent_runtime.py:429-437`) | 실행 전 파일/키체인/env 4소스 + 3값 | 실행 직전 CLI 명령(`codex login status`, `claude auth status`) 출력 파싱 |
| 모드 전환 | argv/`CODEX_API_KEY` 분기(`agent_runtime.py:595-606`) | `auto/proxy/subscription` + manual 고정 | 정책 없음(사용자 설정) |
| 실패 시 기본값 | fail-closed(`runtime_auth_required`) | **unknown→subscription**(fail-safe) | n/a |
| Grok native | `cached_token`만 수용, `grok.com`만 있으면 차단(`agent_runtime.py:962-971`) | Grok은 proxy 쪽 자격증명 소유 + config 주입(§1.4) | n/a |

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| auth presence 3값(`present/absent/unknown`) + "unknown을 proxy로 승격 금지" | **[채택]** | S | `agent_runtime.py:425-445` `probe()`에 `auth_presence` 필드 추가, `providers.py`에 `auth_sources` 선언 | **opencodex** |
| 감지 소스 다중화(파일·키체인·env) + `ownTokens`/marker 제외 | **[채택]** | M | `agent_runtime.py`에 `_auth_sources()` 신설(Claude: `.credentials.json`/`~/.claude.json`, Codex: `CODEX_HOME/auth.json`, Grok: `GROK_HOME/auth.json`) | opencodex |
| "os.homedir() 대신 호출자 env HOME 우선" | **[채택]** | S | `agent_runtime.py:392`/`runtime_environment` | opencodex |
| credential domain(host×authClass) 계약 | **[차용]** | S | `providers.py` capabilities에 `credential_domain` | opencodex |
| MITM·프록시 모드 전환 | **[제외]** | L | — | 이유: §1.6과 동일(자식 격리·증거 사슬) |

---

## 3. quota — 창·reset 모델링 (TalkAbout `usage_limit_reached{window,resetsAt}` 직결)

### 3.1 중립 window 목록과 "absence ≠ 0%"

```ts
// src/quota/window-mapping.ts:35-41 + 107
const NOMINAL_WINDOW_SECONDS = { "5h": 5*3600, weekly: 7*24*3600, monthly: 30*24*3600 };
/** Windows absent from the snapshot are omitted, never zero-filled: absence is not 0%. */
```

- Codex형 입력(`shortPercent/shortResetAt/shortWindowSeconds`, `weekly*`, `monthly*`, `customWindows`)과 provider형(`fiveHour*`…)을 하나의 `QuotaWindowObservation{window, percent?, resetAt?, windowSeconds?, observedAt?}`로 정규화(`window-mapping.ts:14-38,84-120`).
- **exact-model row(`modelId`)는 제외**한다: 같은 풀의 family window가 이미 보고하므로 모델 수만큼 중복 알림이 생긴다(61-66행).
- Anthropic 실측 소스 2개:
  - **body**: `five_hour.utilization`, `seven_day`, `seven_day_fable/opus/sonnet`, `limits[kind=weekly_scoped].percent`(`providers/quota/anthropic-decision-windows.ts:13-34`).
  - **headers**: `anthropic-ratelimit-unified-{5h,7d,7d_oi}-utilization`(0~1 fraction→%), `-reset`(초→ms) (`같은 파일 36-47`). `account-cache.ts:260-265`도 동일 헤더를 읽는다.
- Codex: 주간 최소 24시간 규칙으로 burst(5h)와 weekly를 구분한다. 이걸 틀리면 5h 바를 weekly로 표시하고 **실제로 계정을 막는 한도를 라우팅이 못 본다**(`src/codex/quota.ts:47-60`).

### 3.2 429를 4종으로 분류 — "한도 소진 vs 일시 장애"

```ts
// src/oauth/anthropic-rate-limit-policy.ts:5
export type AnthropicRateLimitKind = "shared-quota" | "family-quota" | "transient-rate" | "request-scoped-unknown";
// :17-26
const rejected = (window) => headers.get(`anthropic-ratelimit-unified-${window}-status`)?.trim() === "rejected";
if (rejected("5h") || rejected("7d")) return "shared-quota";
if (rejected("7d_oi")) return "family-quota";
if (headers.get("anthropic-ratelimit-unified-status")?.trim() === "rejected") return "shared-quota";
// Retry-After is an account-local admission pause, not proof of spent subscription quota.
if (anthropicRetryAfterMs(headers.get("retry-after"), now) !== undefined
  || headers.get("anthropic-ratelimit-unified-status")?.trim() === "allowed") return "transient-rate";
return "request-scoped-unknown";
```

이 4분류가 곧 **재시도 정책**이다. 쿨다운 출처도 3종으로 구분한다: `retry-after`(이번 거절에 대한 응답) / `reset-derived`(소진된 창이 언제 열리는지) / `default`(우리 추정). 주석: "collapsing it into `retry-after` would report a drained five-hour window as request-rate throttling"(`src/oauth/anthropic-routing.ts:96-110`).

### 3.3 reset 검출기 — 순수 함수 + 7개 오탐 함정

`src/quota/reset-detector.ts`는 시계·디스크·설정을 읽지 않는다(1-11행). 이벤트는 `scheduled|surprise` 2종(40행), 반환 필드는 `scope/accountTag/window/percentBefore/percentAfter/previousResetAt/resetAt/detectedAt/key`(42-64행). 규칙:

| 상황 | 판정 | 근거 |
| --- | --- | --- |
| baseline 없음 / window 불일치 | null | 150-154행 |
| `percent` 사라짐 | null ("absence는 0%가 아니다") | 180-182행 |
| deadline 경과 + usage 감소 **또는** deadline 전진 | `scheduled` | 184-202행 |
| deadline 경과했지만 값이 byte-identical(부분 쓰기) | null | 186-201행 주석 |
| deadline 미경과 + 5%p 이상 하락 + creep 아님 | `surprise` | 217-229행 |
| rolling window의 deadline creep(경과시간×2 이내 전진) | null | 260-282행 |

`quotaAccountTag`는 **설치 salt + hash**로 외부 webhook에 계정 식별을 노출하지 않으면서 계정 구분은 유지한다(73,91-93행). idempotence key는 `scope|tag|window|resetAt(new 우선, 없으면 previous)`(95-110행).

### 3.4 reset grant·쿠폰 — 실제 Anthropic/Codex API

- Anthropic: `cedar_ember` 프로그램. read `GET /api/oauth/usage?cedar_ember=1&skip_spend=1`, org `GET /api/oauth/profile`, redeem `POST /api/organizations/{org}/reset_rate_limits` `{program,grant_id,request_id}`. 결과 6종(`reset/already_used/not_limited/cooldown/ineligible/unavailable`) + HTTP 거절 2종(`rate_limited/auth_error`), 파싱은 fail-closed(형식 오류를 "0 grants"로 읽지 않음)(`src/providers/anthropic-reset-grants.ts:1-39`).
- Grok: `grok.com/prod_mc_billing.ConsumerUiSvc/{GetRemainingResets,RedeemReset}` — **gRPC-Web**(`src/grok/reset-coupons.ts:1-19`).
- Codex: reset credit ledger/recovery가 별도 대형 모듈(`src/codex/reset-credit-operation-ledger.ts` 56KB, `reset-credit-recovery.ts` 38KB — 헤드만 확인).

### 3.5 쿼터 폴링 cadence와 "패시브 vs 프로브"

- 기본 15분, **최소 10분**(provider cache TTL 5분·account TTL 10분 위). 이유: 쿼터 엔드포인트가 429를 낸다(관측 근거 포함)(`src/quota/reset-poller.ts:14-20`).
- 같은 파일이 "왜 폴러가 load-bearing인가"를 설명한다: `fetchProviderQuotaReports`의 유일한 호출자가 대시보드 GET이라, 대시보드가 닫혀 있으면 overnight reset을 아무도 못 본다(1-12행).
- passive 관측(응답 헤더에서 얻은 quota)은 TTL이 없으므로 **라우팅에 쓰기 전 1시간 상한**을 둔다(`src/oauth/account-quota-rank.ts:90-104`).
- Codex 쿼터는 디스크 캐시 6시간 + 250ms debounce(`src/codex/quota.ts:24-27`).

### 3.6 quota pool wire contract — 그대로 쓸 수 있는 스키마

`src/quota-pool/contract.ts:1-8`은 **다른 프로세스(Goat)가 디코드하는 공개 계약**이다: `ocx.quota-pool/1`.

```ts
// :18-31
export type QuotaPoolWindowKind = "burst_5h" | "daily" | "weekly" | "monthly" | "credits" | "custom";
export type QuotaPoolSource = "measured" | "header" | "estimated";
export interface QuotaPoolWindow {
  id: string; kind: QuotaPoolWindowKind; label: string;
  usedPercent?: number; used?: number; limit?: number; unit?: QuotaPoolUnit;
  resetsAt?: string; windowSeconds?: number; scopeModel?: string;
  source: QuotaPoolSource; observedAt: string; exhausted?: boolean;
}
// :34-41 — accountRef는 opaque, 이메일/raw id 금지
```

**이 파일은 TalkAbout의 Event payload 후보로 가장 값지다.** 이유: (a) optional field는 "모름"으로 생략되고 null을 쓰지 않음, (b) `source`(measured/header/estimated)로 증거 등급을 구분 — BRIEF 31행의 "합성↔실제 증거 분리"와 정확히 같은 발상, (c) `accountRef` opaque로 계정 식별 비노출.

### 3.7 TalkAbout 매핑

| TalkAbout(1라운드 제안) | opencodex 어휘 | 매핑 |
| --- | --- | --- |
| `window: session` | `5h` / `burst_5h` | 동일 |
| `window: weekly` | `weekly` | 동일 |
| `window: weeklyModel` | `family`(Anthropic 7d_oi/opus/sonnet) / `scopeModel` | **충돌**: R1은 창 종류로, opencodex는 scope로 표현 → §10 참조 |
| `window: credits` | `credits` | 동일 |
| — | `daily`, `custom:*`, `monthly` | TalkAbout에 없음(추가 검토) |
| `resetsAt` (epoch ms) | `resetsAt` (ISO string) | 형식 통일 필요 |

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| 429 4분류(shared/family/transient/unknown) + 쿨다운 출처 3종 | **[채택]** | M | `agent_runtime.py` 오류 코드 → `domain.py` Event의 `failure.usageLimit.reason` | **opencodex** (Orca엔 이 분류가 없음 — R1 §8은 "실패 종류"만) |
| rolling-window creep 배제 + scheduled/surprise 2종 검출 | **[채택]** | S | 판정은 서버: `domain.py`/`participation.py`(에이전트가 아니라 서버가 계산) | opencodex |
| window 관측값 `{percent,resetAt,windowSeconds,observedAt,source}` | **[채택]** | S | `domain.py` Event 신규 필드, `agent_runtime.py`는 raw 관측만 보고 | opencodex |
| `ocx.quota-pool/1` 스키마 채용(opaque accountRef, source enum) | **[채택]** | S | `domain.py`/`docs/PERMISSIONS.md`(scope) + Event 스키마 | **opencodex** (3개 저장소 중 유일하게 공개 wire 계약으로 존재) |
| `absence ≠ 0%`, null 금지 | **[채택]** | S | Event 스키마 검증(`bounds.py`) | opencodex |
| reset grant redeem(cedar_ember) 자동 실행 | **[제외]** | L | — | 이유: 사용자 구독 자원을 플랫폼이 대신 소모. "사람 소유자 통제"(BRIEF-R2 §2) 위반 |
| 15분 상시 폴러 | **[제외]** | M | — | 이유: TalkAbout은 provider 폴링이 아니라 **runtime 1회 호출의 부산물**로 관측해야 함(BRIEF 27-29: 서버가 heartbeat/cursor 관리 안 함) |

---

## 4. account pool — 격리·선택·자동전환

### 4.1 저장 구조 (Codex)

- 파일: `<configDir>/codex-accounts.json`(`src/codex/account-store.ts:44-46`). 레거시 형태는 `Record<id, credentials>`이고 새 형태는 `Record<id, credentialRecord>`(34-36행).
- 레코드: `{generation, credential?, deletedAt?, replacedAt?, lastCodexValidatedAt?, lastCodexValidationStatus?, codexValidationPending?, ...}`(`:80-92`).
- credential: `accessToken`, `refreshToken`, `expiresAt`, `chatgptAccountId` + **`sourceAuthPath`/`sourceSubject`가 둘 다 있거나 둘 다 없어야** 한다(`:63-78`). 즉 **계정 신원을 "출처 인증 파일 경로 + subject"로 기록**한다 — 추측이 아니라 증거다.
- refresh 안전장치: skew 60초, 락 stale 60초, 대기 65초, 50ms 폴링(30-33행). 세대(`generation`) 충돌·락 타임아웃·stale을 **별도 오류 클래스**로 구분한다(`auth-context.ts:8-17`, `CodexCredentialGenerationConflictError` 등).

### 4.2 선택·전환

- Anthropic OAuth pool(기본 OFF): sticky session affinity(24h idle TTL, 2000개 상한), 신규 세션 전략 `quota`(기본: 최저 5h 사용률)/`round-robin`/`fill-first`, 429·분류된 403에서만 failover, transient throttle은 affinity 유지(`src/oauth/anthropic-routing.ts:1-18`). 요청당 최대 3회 회전(68행). 소진 상태는 캐스팅된 오류에 `usageLimit{resetsAt}`로 실어 보낸다(85행 부근 `AnthropicAccountCooldownError`).
- generic OAuth failover: 요청당 **3회**, 계정 **6개** 상한(52,60행). 상한 이유가 명시적이다: "each funded account gets the normal transient ladder, so this fixes the default ingress ceiling at 6 × TRANSIENT_RETRY_MAX_ATTEMPTS physical sends: enrolling more accounts cannot turn one request into a pool-wide 429 storm"(54-59행).
- 정렬: `healthy(0) < unknown(1) < exhausted(2)`, healthy 내부는 **모든 window 중 최소 headroom** 내림차순(잔여가 가장 적은 창이 지배)(`src/oauth/account-quota-rank.ts:70-84`).
- 소유 경계: `openai`(Codex)·`anthropic`은 이 모듈에서 **제외**하고 각자 라우팅이 소유한다(`generic-account-failover.ts:80-90`).

### 4.3 TalkAbout 대조

TalkAbout에서 "계정"은 **서버 권한 scope**(`docs/PERMISSIONS.md`, 계정 scope ∩ membership ∩ credential scope)이고, opencodex의 계정은 **로컬 자격증명 풀**이다. 층이 다르므로 계정 풀 자체는 [제외]. 다만:

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| 쿨다운 출처 3종(`retry-after`/`reset-derived`/`default`)을 failure fact에 포함 | **[채택]** | S | `agent_runtime.py` 오류 매핑, `domain.py` | **opencodex** |
| 요청당 failover/계정 수 상한(3회/6계정)을 서버 attempt 예산에 반영 | **[차용]** | S | `domain.py:117-125` attempt 예산, `worker.py` | opencodex |
| headroom 최소값 기준 정렬(라우팅) | **[제외]** | — | — | 이유: TalkAbout은 서버가 round-robin 슬롯을 강제(BRIEF 38행)하며 provider 계정 선택은 참가자 소유 |
| `accountRef` opaque(이메일/raw id 금지) | **[채택]** | S | `identity.py`/`domain.py` 계정 표시 | opencodex |
| `sourceAuthPath`+`sourceSubject`로 계정 신원 증거화 | **[차용]** | S | `codex_sessions.py`의 profile digest(§10 정정 대상) | opencodex |

---

## 5. protocols — 계약·버전 드리프트 대응

### 5.1 무엇을 계약으로 고정하나

| 계약 | 내용 | 근거 |
| --- | --- | --- |
| 버전 | `PROTOCOL_CONTRACT_VERSION = "2026-09-25.2"` — reason code/hop/mode/feature disposition의 **의미가 바뀌면 bump** | `contract.ts:11-12` |
| 3 public protocol / 4 upstream wire / 6 hop | `responses/chat/messages`, `other`, `ir`, `responses-internal` | 14-34행 |
| delivery mode 4종 | `native / translated / legacy-bridge / blocked` — blocked는 "never implied" | 36-46,179-185행 |
| fidelity 3종 | `preserved / degraded / unknown` | 48-49행 |
| reason code **닫힌 목록** | 23종 고정. "Never conversation-derived, never free text" | 55-84행 |
| feature 19종 × hop disposition | `passthrough/translated/degraded/unsupported` 행렬 | `features.ts:18-110` |
| **unknown은 다섯 번째 disposition이 아니다** | `other` 또는 미선언 hop은 `undefined`로 표현 | `features.ts:1-12,119-131` |

### 5.2 Pure planner + shadow plan

- `planProtocol()`은 부수효과 없는 순수 함수: 후보별 경로·모드·feature 효과·차단 여부를 예측하고, 요청의 보장/부분 feature를 계산(`plan.ts:1-12,146-199`).
- `plan-snapshot.ts`는 "프리뷰가 아무것도 보내지 않도록" combo/policy를 부수효과 없이 전개한다(1-19행). caller credential이 필요한 native passthrough는 **`caller-credential-required`로 보고하고 가정하지 않는다**.
- `shadow-plan.ts`: 실제 dispatch 직후 같은 snapshot을 기록해 두고 finalize에서 순수 planner 결과와 비교. 실패는 삼킨다("a shadow plan is never fails or changes the request it describes")(1-14행).

### 5.3 버전 드리프트 대응 (가장 실용적 기법)

`src/codex/features.ts:1-27`이 원칙을 말한다: opencodex이 직접 읽어야 하는 플래그만 미러링하고 **나머지는 네이티브 `codex features` 명령에 위임**한다. 이유로 upstream churn을 든다: "in the 1f0566d3f..5a1097ed2 range alone, `code_mode_host` changed from a boolean to a table, `enable_fanout` and `item_ids` were retired to `Stage::Removed`". 대조: Orca는 플래그별 `firstVersion` + 실패 진단 캐시로 자체 판정(R1 §2), TalkAbout은 help 텍스트의 플래그 실재만 검사(`agent_runtime.py:514-532`).

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| 닫힌 reason code enum + 버전 상수(계약 변경 시 bump) | **[채택]** | S | `domain.py` Event reason enum, `docs/INTERNAL_FOUNDATION_CONTRACT.md` | **opencodex** |
| `delivery mode`/`fidelity`(native/translated/degraded/unknown) | **[차용]** | S | Event에 "텍스트가 어떤 변환을 거쳤는가" 표기 — TalkAbout은 text-only라 축소 적용 | opencodex |
| feature×hop disposition 행렬 | **[제외]** | M | — | 이유: TalkAbout은 단일 wire(text)만 쓰고 hop이 없음. 다만 도구 차단 판정에는 "supports×policy" 행렬 개념만 차용 |
| shadow plan(진단이 요청을 바꾸지 않음, 실패 삼킴) | **[채택]** | S | `trial_*.py` 관측 훅, `worker.py` | **opencodex** |
| 네이티브 명령에 위임(자체 미러 최소화) | **[채택]** | S | `agent_runtime.py` preflight — 이미 유사(help 검사), 원칙만 명문화 | opencodex |

---

## 6. anthropic 어댑터(84KB) — TalkAbout HTTP transport에 필요한가

방향: **Anthropic Messages ↔ 내부 IR**이며, Claude Code inbound는 Anthropic 요청을 내부 `/v1/responses`로 번역해 **routing/OAuth/pool/failover를 그대로 상속**한다(`src/claude/inbound.ts:1-17`). outbound는 IR → Anthropic SSE로, 프레임 순서 계약(`message_start → (content_block_start→deltas→stop)* → message_delta → message_stop`, ping은 어디든, 200 이후 mid-stream error 가능)을 고정한다(`src/claude/outbound.ts:1-15`, 상태→오류 taxonomy `:58-70`).

어댑터 계약면(전수 아님): tool catalog(`anthropic-tool-catalog.ts`), image codec/normalize/guard(`anthropic-image-*.ts`), replay capture(`anthropic-replay.ts`), progress(`anthropic-progress.ts`), cache retention(`anthropic/cache-retention.ts`), model-specific contract(`anthropic-model-contract.ts` — combined sampling 거부·forced tool choice 거부·adaptive thinking 등), beta allowlist(`anthropic/beta-allowlist.ts`), SSE 디코더(`lib/sse-decoder`). 진입점 `createAnthropicAdapter`(`anthropic.ts:1010`), base URL/헤더(`:404,545-560`).

beta allowlist가 특히 안전하다: caller의 `anthropic-beta`를 **그대로 전달하지 않고** 고정 목록과 비교해 자기 표기로 재방출하며, 드롭된 값은 **로그에 이름을 남기지 않는다**(닫힌 공개 목록만 예외)(`beta-allowlist.ts:1-18,48-70`).

TalkAbout `http_runtime.py`의 실태: OpenAI-compatible `/chat/completions` 전용, text-only, 도구 정의 미전송, 고정 오류 코드(`http_runtime.py:1-16,30-38,103-120`), `auth_mode`는 항상 `"api_key"`(59행).

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| Anthropic Messages 어댑터 전체(도구·이미지·캐시·replay) | **[제외]** | L | — | 이유: TalkAbout http transport는 OpenAI 호환 단일 형태(`http_runtime.py:1-16`)만 지원. Messages 지원은 별도 제품 결정이며 현재 범위 밖 |
| **요청 헤더 allowlist**(caller beta/header를 그대로 전달하지 않음 + 드롭 값 비노출) | **[채택]** | S | `http_runtime.py`의 헤더 구성부, `mcp.py`/`tool_broker.py`의 외부 호출 | **opencodex** |
| 모델별 파라미터 계약 표("이 모델은 combined sampling 거부" 등) | **[차용]** | M | `providers.py` capabilities 확장 + `http_runtime.py` 요청 빌더 | opencodex |
| opaque reasoning strip(발급자만 검증 가능한 상태 제거) | **[차용]** | S | 향후 reasoning 재전송 도입 시 `agent_runtime.py` | opencodex (Orca R1보다 명시적) |
| SSE 프레임 순서 계약 + mid-stream error | **[대조]** | — | TalkAbout은 SSE를 쓰지 않음(`agent_runtime.py`는 line-delimited JSONL) | — |

---

## 7. codex/grok 프로토콜 — Orca·TalkAbout과의 3자 비교

### 7.1 사실관계

- **Codex 주 경로는 HTTP 리다이렉트다.** opencodex은 Codex의 app-server JSON-RPC를 프록시하지 않는다. `openai_base_url`로 Responses API 트래픽만 받는다(§1.3). app-server JSON-RPC 클라이언트가 존재하는 곳은 **remote-control(원격 작업공간)** 이다: `initialize → config/read → thread/start|thread/resume → turn/start`(`workspace-codex-runtime.ts:455-490`), `item/agentMessage/delta` 누적 후 `item/completed`에서 대체, `turn/completed`에서 정산(`:414-445`), 한 줄 2MB·stderr 64KB·델타 32개/64KB·**early completion 16개** 상한(`:27-33`), 초기 완료는 `completedBeforeWait`로 보관 후 `turn/start` 응답과 대조(`:386-392,502-508`), `turn/interrupt` 3초 후 close·bridge stop·격리 제거(`:510-530`).
- **Grok은 두 층이다.** 추론은 config 주입으로 HTTP/OpenAI 호환이지만, 리셋 쿠폰은 **gRPC-Web**(`grok.com/prod_mc_billing.ConsumerUiSvc/*`)이다. `grpc-web.ts`는 5바이트 프레임 헤더(0x00 data / 0x80 trailer)만 처리하는 **무의존 코덱**이고(1-5,89행), 변조된 trailer·미완료 프레임을 `framingComplete=false`로 정직하게 보고한다(100-140행).
- **Grok 세션 ID 승격**: `x-grok-conv-id`(+body `prompt_cache_key`)밖에 없는 Grok 요청에 `session_id`를 **합성 주입**해 프롬프트 캐시·thread affinity를 살린다. 단 `x-opencodex-grok: 1`(우리가 쓴 surface)일 때만, explicit session header가 있으면 양보(`grok/session-identity.ts:1-17,17-46`).

### 7.2 3자 비교

| 축 | Orca (R1) | opencodex | TalkAbout 현재 |
| --- | --- | --- | --- |
| Codex 취급 | **app-server JSON-RPC가 정본** + exec JSONL 파서 없음 | HTTP Responses 리다이렉트 + (remote-control에서만) app-server 클라이언트 | **exec JSONL이 기본**, app-server는 opt-in(`agent_runtime.py:5-9`) |
| Claude 취급 | SDK `query()` stream-json 장기 연결 | **MITM 프록시**로 Messages 인터셉트 후 IR 번역 | `claude --print --input-format stream-json` 1회 호출 |
| Grok 취급 | ACP(구조화 세션) | config 주입 + gRPC-Web(쿠폰) | ACP `agent stdio`(`agent_runtime.py:573-574`) |
| 세션 재개 | provider ID + account home pin (R1 §3) | Codex thread resume/start, mismatch 즉시 실패(`workspace-codex-runtime.ts:477-486`) | 메모리 session_id만(§10) |
| 바이너리 패치 | 없음 | Codex launcher shim(보조) | 없음 |
| hook 의존 | 강함(agent-hooks) | 없음(설정/env만) | 없음 |

**모순 아님, 강화**: R1에서 "Orca에는 `codex exec --json` 파서가 없다"고 적었다. opencodex도 없다. 따라서 **3개 저장소 중 `exec --json` 이벤트를 파싱하는 구현은 TalkAbout뿐**이다. 이는 TalkAbout의 자산이며, 배울 것은 파서가 아니라 **이벤트 분류표·blocking 응답표·quota 분류**다(R1 §4 결론 유지).

**모순**: opencodex의 Codex 처리는 Orca와 정반대다. Orca는 app-server 의미론(thread/turn/approval)을 직접 다루고, opencodex은 HTTP 몸통만 갈아끼운다. TalkAbout은 **둘 다** 갖고 있으면서 exec-json을 기본으로 둔다(`agent_runtime.py:8-9`). 따라서 "어느 쪽이 옳다"가 아니라 **경로별 책임 분리**가 필요하다(§10 2항).

| 항목 | 판정 | 비용 | 부착 위치 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| app-server 초기 완료 유실 방지(early completion 보관) + 경계 버퍼 | **[채택]** | S | `agent_runtime.py:749-862`(`_codex_exec`/`_codex`) | **opencodex** (Orca R1에는 이 구체 패턴이 없음) |
| `thread/resume` 후 ID 불일치 즉시 실패 | **[대조]** | — | 이미 있음(`agent_runtime.py:811-821`) | — |
| gRPC-Web 무의존 코덱 | **[차용]** | S | TalkAbout Grok은 ACP라 불필요. 단 Grok quota/쿠폰을 붙일 때 재사용 가능 | opencodex |
| session_id 합성 주입(x-grok-conv-id) | **[제외]** | — | — | 이유: TalkAbout은 ACP가 세션을 명시하므로 불필요. 프록시 특유의 문제 |
| Codex HTTP base_url 리다이렉트 | **[제외]** | M | — | 이유: §1.6과 동일(증거 사슬) |

---

## 8. TalkAbout 구체 개선점 (파일:라인)

| # | 근거(현재) | 문제 | 개선 | 태그/비용 | 최적 저장소 |
| --- | --- | --- | --- | --- | --- |
| T1 | `agent_runtime.py:949` | `rate_limit_event`를 수신하고 버림. `system:api_retry`도 무시 | 관측 전용 collector 추가: `{window, percent?, resetAt?, source:'live-session'}` → 서버 판정 | **[채택]** S | opencodex |
| T2 | `agent_runtime.py:792-793` | `turn.failed`/`error` payload를 버려 "한도"와 "장애"가 같은 코드 | 오류 타입 allowlist(`usageLimitExceeded|rateLimitExceeded|serverOverloaded…`) + 4분류 매핑 | **[채택]** S | opencodex |
| T3 | `agent_runtime.py:425-445` | auth 판정이 `env key exists` 단일. keychain/파일 로그인 미감지 | 3값 presence + 다중 소스(단, "unknown→프록시 전환" 같은 전환은 도입하지 않음) | **[채택]** M | opencodex |
| T4 | `agent_runtime.py:102,437` / `processes.py:1` | POSIX에서 CLI 실행 자체가 차단 | 유지. 단 `runtime_containment_unsupported`를 실패 종류로 노출해 "왜 안 되는지"가 Event에 남게 | **[대조]** — | — |
| T5 | `providers.py:56-57` | Grok `gated` 이유가 note 문자열뿐 | `capabilities.auth_sources`, `capabilities.quota_windows`, `evidence_ref`로 구조화(§3.7 어휘 사용) | **[채택]** S | **opencodex**(quota 어휘) — capability 구조는 R1 orca |
| T6 | `http_runtime.py:59` | `auth_mode = "api_key"` 고정, 헤더 구성이 자유로움 | 헤더 allowlist(외부 beta/헤더 미전달) + `auth_mode`를 provider capability에서 | **[채택]** S | opencodex |
| T7 | `agent_runtime.py:106-115`(R1 A1 재확인) | `adapters.py:21-27`은 여전히 교차 자격증명 | R1 권고 유지(단일 `provider_environment()`로 통합) | **[채택]** S | **orca**(R1 근거) — opencodex `CredentialDomain`이 보강 |
| T8 | `agent_runtime.py:416,572,947`(R1 R1) | Claude 세션 ID가 메모리 uuid4 | R1 권고 유지(영속) + opencodex `sourceSubject`처럼 **출처 증거**로 계정 pin | **[채택]** M | opencodex(증거 방식) |
| T9 | `domain.py:117-125` | 실행 전 실패도 attempt 소모(BRIEF-R2 §3 결함 5) | opencodex의 "reason code로 실패를 분류해 재시도 예산과 분리" 개념 차용 | **[차용]** S | opencodex |

---

## 9. Top 5 실행 제안

| 순위 | 제안 | 근거 | 작업 위치 | 비용 | 최적 저장소 | 검증 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **quota 관측·분류 계층**: `{window,percent,resetAt,windowSeconds,observedAt,source}` + 429 4분류 + 쿨다운 출처 3종 | `window-mapping.ts:35-45,84-120`, `anthropic-rate-limit-policy.ts:17-26`, `reset-detector.ts:141-282`, `quota-pool/contract.ts:18-41` | `agent_runtime.py:792-793,949`, `domain.py` Event, `participation.py` 표시 | M | **opencodex** | 합성: 429 헤더 3케이스(shared/family/transient), rolling creep 미검출, absence≠0% |
| 2 | **auth presence 3값 + 다중 소스**(전환은 하지 않음) | `auth-detect.ts:1-11,101-160,184-220`, `auth-mode.ts:26-52` | `agent_runtime.py:425-445`, `providers.py` capabilities | M | **opencodex** | 합성: 권한 오류 파일 → `unknown`(≠absent), own token 미계상 |
| 3 | **failure fact에 reason code·mode·fidelity 닫힌 enum**(shadow 진단은 실패 삼킴) | `contract.ts:11-12,55-84`, `shadow-plan.ts:1-14` | `domain.py`, `worker.py`, `trial_*.py` | S | **opencodex** | 합성: 미지 reason code 거부, 진단 실패가 요청 실패로 승격되지 않음 |
| 4 | **exec/app-server 경로별 책임 분리 + early completion 보관** | `workspace-codex-runtime.ts:27-33,386-392,414-445,502-508` | `agent_runtime.py:749-862` | S | **opencodex** | 합성: `turn.completed`가 `turn/start` 응답보다 먼저 와도 결과 유지 |
| 5 | **헤더/beta allowlist + opaque 상태 제거**(외부 입력을 그대로 전달하지 않음) | `beta-allowlist.ts:1-18,48-70`, `opaque-state.ts:104-131` | `http_runtime.py` 헤더부, `tool_broker.py`/`mcp.py` 외부 호출 | S | **opencodex** | 합성: 미허용 헤더 미전달·로그 미노출, signature는 first-party 외 제거 |

의존: 1→3(같은 Event 스키마), 2→1(auth 실패와 한도 실패를 구분해야 분류가 맞음). 4·5는 독립.

---

## 10. 1라운드(Orca) 결론과의 모순·정정

1. **모순 아님·강화**: R1 §4.3 "Orca는 `codex exec --json`을 소비하지 않는다". opencodex도 소비하지 않는다(HTTP Responses 리다이렉트, `remote-control`만 app-server). → **3개 저장소 중 exec JSONL 구현은 TalkAbout 단독 자산**. R1의 "분류표를 가져오라"는 결론은 유지되고, 여기에 opencodex의 **early completion/경계 버퍼** 패턴을 더한다.
2. **정정**: R1 §3의 "세션 재개 정본은 provider ID + account home"은 여전히 유효하지만, TalkAbout이 "계정 신원은 검증 불가"라며 resume을 영구 차단한 전제(`codex_sessions.py:69-70,113-116` + `agent_runtime.py:399`)는 **opencodex의 `sourceAuthPath`+`sourceSubject`**(`account-store.ts:63-78`)처럼 **출처 파일에서 읽는 증거**로 대체 가능하다. 즉 "이메일/홈 추정 금지"는 맞지만, "출처 인증 파일 경로+subject"는 추정이 아니라 증거다. → R1 §10.3 C2 권고를 "게이트 제거"가 아니라 **"증거 출처를 provider 인증 파일로 명시"**로 수정.
3. **불일치(모순)**: quota window 어휘가 3자 충돌한다. R1에서 나는 `session/weekly/weeklyModel/credits`(Orca식)를 제안했고, opencodex는 `5h/weekly/monthly/custom` + `family` scope를 쓴다(§3.7 표). 이대로 두면 서버 Event 스키마가 갈라진다. **통합안**: `{kind: 'burst'|'daily'|'weekly'|'monthly'|'credits'|'custom', scope?: {modelFamily?: string, modelId?: string}, ...}` — `weeklyModel`은 kind가 아니라 **scope**로 표현한다(opencodex 방식이 더 확장적). → R1 §8.3 표를 이 형태로 정정.
4. **정정(BRIEF-R2 §8의 4번 반영)**: R1에서 "교차 자격증명은 활성 누출 아님"으로 정정되었지만, TalkAbout `adapters.py:21-27`은 **현재도** Codex 프로세스에 `ANTHROPIC_API_KEY`, Claude에 `OPENAI_API_KEY`를 넘긴다. opencodex는 `CredentialDomain{host,authClass}`로 이 경계를 코드화한다(`opaque-state.ts:27-75`). → R1 A1은 유효하며 opencodex가 근거를 보강한다.
5. **모순처럼 보이나 계층 차이**: opencodex의 account pool(자동 전환·affinity·headroom 정렬)과 TalkAbout의 계정 scope은 **다른 문제**다. BRIEF-R2 §3의 "Orca는 데스크톱 로컬 신뢰 경계" 판단은 opencodex에도 그대로 적용된다(로컬 프록시). → 계정 풀은 [제외], **쿨다운 출처·opaque accountRef·유한 상한**만 이식.

---

## 11. 미확인 / 주의

1. **미확인**: `src/claude/desktop-3p.ts`(46KB)의 Desktop 원격 스토어 스키마 세부는 헤드·타입만 확인했다. "Claude Desktop이 이 3p 프로필을 어떻게 신뢰하는가"의 정확한 앱 버전 조건은 검증하지 않았다.
2. **미확인**: Codex `reset-credit-*`(56+38KB)의 원장·복구 알고리즘은 파일 존재와 헤드만 확인. 본 보고서는 "reset grant가 별도 원장으로 관리된다"까지만 주장한다.
3. **미확인(범위 밖)**: hermes-agent는 이 보고서에서 분석하지 않았다. "어느 저장소가 최적인가"는 orca/opencodex 비교이며, ACP 서버 구현(`hermes-agent/acp_adapter/`)이 더 나은 항목은 `r2-` 다른 보고서와 교차 확인이 필요하다.
4. **주의(라이선스)**: MIT(§0). 코드 인용은 분석 목적이며, 이식 시 `LICENSE` 고지 필요. 특히 `beta-allowlist.ts`·`reset-detector.ts`·`quota-pool/contract.ts`는 **개념·스키마 이식**을 권고하고 코드 복사는 최소화한다.
5. **주의(성격)**: opencodex은 사용자 자신의 구독 자격증명을 **자기 프록시가 대신 사용**하는 도구다. TalkAbout은 "참가자 자기 환경·자기 구독"(BRIEF-R2 §2)이므로, opencodex의 credential 소유·전환 로직을 플랫폼으로 옮기면 설계 원칙 위반이 된다. 이 보고서의 [채택] 항목은 모두 **관측·분류**이지 **대행**이 아니다.
6. **주의(POSIX)**: opencodex은 Bun/Node 크로스플랫폼이지만 TalkAbout은 Windows Job Object 전용(`processes.py:1`, `agent_runtime.py:102`). opencodex의 프록시 패턴을 POSIX 근거로 인용하면 안 된다.

---

## 부록 — 인용한 opencodex 파일

```
LICENSE, package.json, README.md, AGENTS.md(참고), structure/INDEX.md(표본)
src/codex/inject.ts, src/codex/inject/plan.ts, src/codex/inject/config-toml.ts
src/codex/injected-marker.ts, src/codex/features.ts, src/codex/shim.ts
src/codex/app-server-processes.ts, src/codex/account-store.ts, src/codex/quota.ts
src/codex/auth-context.ts
src/grok/inject.ts, src/grok/status.ts, src/grok/models.ts, src/grok/grpc-web.ts
src/grok/reset-coupons.ts, src/grok/session-identity.ts
src/claude/auth-detect.ts, src/claude/auth-mode.ts
src/claude/intercept/{settings.ts,connect-proxy.ts,local-ca.ts,listener.ts,proxy-auth.ts}
src/claude/desktop-3p.ts, src/claude/inbound.ts, src/claude/outbound.ts
src/oauth/anthropic-rate-limit-policy.ts, src/oauth/anthropic-routing.ts
src/oauth/generic-account-failover.ts, src/oauth/account-quota-rank.ts
src/quota/reset-detector.ts, src/quota/window-mapping.ts, src/quota/reset-poller.ts
src/quota-pool/contract.ts
src/providers/quota/anthropic-decision-windows.ts, src/providers/quota/account-cache.ts
src/providers/anthropic-reset-grants.ts
src/protocols/{contract.ts,features.ts,plan.ts,plan-snapshot.ts,shadow-plan.ts,opaque-state.ts,envelope.ts}
src/adapters/anthropic.ts, src/adapters/anthropic/beta-allowlist.ts
src/remote-control/workspace-codex-runtime.ts
src/bridge/internal.ts(usage 형태), src/server/anthropic-rate-limit-headers.ts
```
