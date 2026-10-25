# R2 대립 검증 — 1라운드(Orca) 판정 재판정

- 역할: reviewer(대립 검증). 새 저장소 2개로 **1라운드 결론을 깨는 것**이 목적.
- 입력: `analysis/00-SYNTHESIS.md`(228줄) + `analysis/BRIEF-R2.md` + `opencodex/`(HEAD `9f0d40a`) + `hermes-agent/`(HEAD `930e5ed`). 다른 팀원 보고서 미사용(지시대로 독립 판정).
- 경로 규칙: 신규 저장소는 `opencodex/...`, `hermes-agent/...`(저장소 루트 기준). TalkAbout은 `talkabout/...`. 1라운드 항목 번호는 `00-SYNTHESIS.md`의 Tier/번호를 그대로 인용한다.
- 표기: `[채택]`/`[차용]`/`[대조]`/`[제외]`, 비용 S/M/L, **최적 출처**(orca/opencodex/hermes).
- 원칙: 판정한 항목만 근거 라인을 붙였다. 확인하지 못한 것은 **"판정 불가" 또는 "찾지 못했다"**로 남겼다. 새 저장소를 얕게 읽고 "더 낫다"고 쓴 항목은 없다.

## 라이선스

| 저장소 | 라이선스 | 확인 방법 |
|---|---|---|
| orca | MIT | `orca-analysis/LICENSE` (1라운드 확인) |
| opencodex | **MIT** | `opencodex/LICENSE:1-3` 직접 확인("MIT License / Copyright (c) 2026 opencodex contributors") |
| hermes-agent | MIT (브리프 기재) | `BRIEF-R2.md:14` 기재. 본 라운드에서 파일 원문은 열지 않음 |

코드 복사 없음(프로토콜·구조 참조만). 실제 복사 시 세 저장소 모두 저작자 표시·라이선스 전문 고지 필요.

---

## 0. 결론 — 바뀐 판정 수

| 구분 | 전체 | 변경 | 유지 | 판정 불가 |
|---|---|---|---|---|
| Tier 1 | 13 | **7** | 6 | 0 |
| Tier 2 | 12 | **5** | 6 | 1 |
| Tier 3 | 3 | **2** | 1 | 0 |
| **합계** | **28** | **14** | **13** | **1** |
| "TalkAbout 우위" 9 | 9 | **3**(격하·정정) | 6 | 0 |
| "배울 게 없는 것" 8 | 8 | **1**(부분 뒤집힘) | 7 | 0 |

**가장 중요한 변경 3개**
1. **Tier 1 #3(SQLite 락 3분류)의 최선 참조는 Orca가 아니라 hermes-agent다.** hermes는 락 오류를 결과코드로 분류하고(`hermes_state_errors.py:40-70`), 11개 버킷으로 나누며(`:98-102`), **락 보유자를 `/proc/locks`에서 지목**한다(`hermes_state_lockowners.py:1-8`). Orca의 `sqlite-read-failure.ts` 3분류는 이에 비해 입문서 수준이다.
2. **Tier 1 #11(capability 선언↔구현)과 Tier 2 #10(정본 서빙)의 최선 참조는 opencodex다.** opencodex는 호환성 주장을 `disposition + evidence(assertion id)`로 강제하는 **검증 가능한 매니페스트**를 갖고 있고(`src/compatibility/manifest.ts:20-50,140-167`), 스킬 표면을 생성물로 만들고 CI에서 드리프트를 막는다(`tests/ci-workflows/skill-ocx-generated.test.ts`).
3. **"TalkAbout이 이미 우위" 9개 중 `tool fail-closed`는 성립하지 않는다.** `tool_broker.py:1`이 "Memory-only fake-tool foundation; intentionally absent from app/runtime wiring"이라고 스스로 밝히고, 다른 모듈에서 import되지 않는다(grep 결과 `tool_broker.py` 자신 외 참조 0). 즉 우위가 아니라 **미배선**이다.

---

## 1. 판정 방법과 한계

- 두 저장소 모두 `AGENTS.md`·`structure/`(opencodex)·`website/docs/developer-guide/`(hermes)의 **설계 의도 문서**를 먼저 읽고, 코드 라인으로 확인했다. 문서 주장만으로 판정한 항목은 없다.
- `opencodex/devlog/`(5,305 md)와 `hermes-agent/`의 대형 파일(`hermes_state_*.py` 60모듈, `gateway/run_notifications.py` 113KB)은 **전수 읽지 않았다**. 판정에 쓴 부분만 읽었고, 읽지 않은 영역은 "찾지 못했다"로 한정했다.
- 두 저장소의 **문제 정의가 TalkAbout과 다른 지점**을 매 항목에서 확인했다. 예: hermes `acp_adapter`는 ACP **서버**이고 TalkAbout은 **클라이언트**다. 서버측 구현이 클라이언트 프레임 규율의 대체물은 아니다(Tier 1 #1 유지 이유).
- "최적 출처"는 **그 항목을 구현할 때 가장 직접적으로 참조할 저장소**를 뜻한다. 여러 저장소를 조합해야 하는 항목은 "주/부"로 표기했다.

---

### 1.1 "더 낫다"고 판정할 수 있는 기준 3원칙

이번 라운드에서 "최적 출처 변경"은 아래 3개 중 **하나 이상을 코드 라인으로 증명했을 때만** 인정했다.

| 기준 | 의미 | 이 기준으로 바뀐 항목 | 이 기준으로 안 바뀐 항목(반례) |
|---|---|---|---|
| **C1. 상위 집합** | 1라운드 참조가 하던 일을 포함하면서, 그 뒤 단계(판정·억제·통지/정산)까지 구현 | T1-2(리셋 판정·억제·통지), T2-1(stdio까지 redaction), T3-1(손자 프로세스 스윕) | T1-1: hermes ACP는 프레임 처리를 SDK에 위임 → 상위 집합 아님 |
| **C2. 강제력** | 문서·선언이 아니라 코드/CI가 어겨지면 실패하게 만든다 | T1-10(structure SSOT CI), T1-11(evidence 없는 claim 거부), T1-12(선언된 passthrough만 통과), T2-10(생성물 드리프트 CI) | T2-6: hermes 권한 매핑표는 작은 표이고 카탈로그 검증이 아님 |
| **C3. 문제 정의 일치** | TalkAbout의 실제 실행 형태(Python, 클라이언트, 1회 호출)와 같다 | T1-3(Python SQLite), T3-1(Python 프로세스 트리) | T3-2(hermes 상주 데몬은 대화 유지 전제), T2-2(hermes 자체 세션 DB) |

세 기준을 모두 만족하지 못하면 1라운드 판정을 유지했다. 예: T1-4(shutdown 상한)는 hermes가 TERM→KILL 래더를 갖지만 **C3 불일치**(프로세스 트리 vs 어댑터 호출 상한)로 Orca 유지.

## 2. Tier 1 재판정 (13항목)

| # | 항목 | 1라운드 | 새 판정 | 최적 출처 | 근거 | 변경 이유 |
|---|---|---|---|---|---|---|
| 1 | ACP 프레임 규율(숫자 id, 불일치 무시+유한 타임아웃, 초과 라인 개별 정산) | `[채택]` Orca | **유지** | orca | hermes `acp_adapter/*.py`는 `acp` SDK를 92곳에서 import하고 수동 JSON-RPC 엔벨로프/타임아웃 처리가 없다(`acp_adapter/events.py:15`, `permissions.py:11` 등). 프레임 규율은 SDK 소관이라 in-repo 참조가 아니다 | 서버측 SDK 위임 구조라 "프레임 직접 구현" 참조가 될 수 없다 |
| 2 | 실패·한도 어휘 타입화(`usage_limit_reached{window,resetsAt}`) | `[채택]` Orca | **변경** | **opencodex** | `src/quota/window-mapping.ts:11-49`(window/percent/resetAt/windowSeconds 중립화, "absence is not 0%", 모델별 중복행 제외 `:74-78`), `reset-detector.ts:141-237`(scheduled/surprise 2종 판정), `:260-282`(롤링 윈도우 creep 배제), `src/quota/reset-seen-store.ts`·`reset-notify-config.ts`·`reset-sinks.ts`(중복 통지 억제 파이프라인) | Orca는 **감지 1단계**(usage-limit 파서)만. opencodex는 어휘→리셋 판정→중복 억제→통지까지 있다 |
| 3 | journal 락 오류를 stop으로 승격 금지 + busy_timeout | `[채택]` Orca | **변경** | **hermes** | `hermes_state_errors.py:40-70`(SQLITE_BUSY/LOCKED를 **결과코드**로 판정, transient/malformed/disk-full 분리), `:98-102`(11버킷 `PERSISTENCE_ERROR_CAUSES`), `hermes_state_lockowners.py:1-8`("피해자만 말하고 보유자를 못 말한다"→`/proc/locks`로 보유자 지목), `hermes_cli/sqlite_util.py:33-55`(busy_timeout 단일 노브 + transient만 재시도) | Orca의 3분류는 파일 1개. hermes는 **분류·버킷·소유자 지목·재시도 정책**이 한 체계다 |
| 4 | stop 경로 shutdown 5초 상한 | `[채택]` Orca | **유지** | orca | hermes는 프로세스 트리 종료용 TERM→10초→KILL 래더(`agent/verify/runner.py:127-147`)를 갖지만, 이는 "런타임 어댑터 shutdown 호출 상한"이 아니다 | 문제 단위가 다르다. TalkAbout `runtime.py:51-59` 수리에는 Orca의 상한 패턴이 직접적 |
| 5 | ack 증거 사슬(서버 delivered_seq, ack ≤ delivered) | `[채택]` Orca Delivery | **변경(출처)** | **hermes** | `gateway/delivery_ledger.py:1-11`(배달 의무 원장: pending→attempting→delivered/failed, **crash 의미론 명시**), `:31-35`(attempt 상한·stale 만료·retention), `:38-50`("중복 가능"을 숨기지 않는 recovered 마커) | 1라운드 제안(ack≤delivered)은 유효하나, "전달 의무를 원장으로 남기고 모호한 재전송을 표시"하는 설계는 hermes가 더 완전 |
| 6 | stall 감지(연속 N tick eligible 0 → 이벤트) | `[채택]` 독자(Orca에도 없음) | **유지** | 독자 | hermes `hermes_startup_watchdog.py`는 **startup hang** watchdog이고(`:17` respawn-storm backoff 300s, `:46-48` exit code), 스케줄러 무진행 감지가 아니다. opencodex는 서버 스케줄러가 없다 | 두 저장소 모두 등가물을 찾지 못했다. hermes watchdog은 T2-4 참조로만 |
| 7 | MCP 비파괴 설치(dry-run digest, 못 읽으면 중단) | `[채택]` Orca | **유지** | orca | hermes `optional-mcps/`는 서버 **카탈로그**이고, hermes에서 MCP 설정 병합의 dry-run/digest 게이트를 찾지 못했다(grep: `merge/dry_run/backup`는 git 복구·oauth 백업에만) | 등가 구현 미확인. "찾지 못했다"로 한정 |
| 8 | 완료 통지(5초 쿨다운·포커스 억제·unread 선기록) | `[채택]` Orca | **변경** | **hermes** | `gateway/run_notifications.py:35`("Notice-dedupe key for one SERVED profile's home channel"), `:987`("`started_at` are delivered undeduplicated rather than risk suppressing a real completion" — **완료는 중복 억제보다 누락 방지 우선**), `gateway/run_notifications_ownership.py`(소유권 분리), `evals/delivery_flood_wire.py`(플러드 와이어 시나리오) | Orca의 5초 쿨다운은 "억제"만 있다. hermes는 **억제하지 말아야 하는 것**을 규칙으로 고정했다 |
| 9 | reduced-motion CSS 버그 수리 + CSS 검사 테스트 | `[채택]` Orca | **유지** | orca | opencodex GUI는 React/Vite이고 이 버그 클래스의 참조 사례를 찾지 못했다 | 신규 저장소 무관. 1라운드 판정 그대로 |
| 10 | `AGENTS.md` 규약 + test-antipatterns 차용 | `[채택]` Orca | **변경** | **opencodex** | `opencodex/AGENTS.md`(규약 허브) + `structure/INDEX.md`(소유권 SSOT, AGENTS가 "Ownership is not advisory"로 강제) + `tests/ci-workflows/structure-ssot.test.ts`(28KB, doc↔path·invariant 바인딩 검사) + `file-size-ratchet.test.ts` + `tests/test-layout.test.ts`(테스트 배치) | Orca의 69줄 AGENTS.md보다 **문서-코드 소유권을 CI 게이트로 강제**하는 opencodex가 상위. hermes의 라우팅 표(`AGENTS.md` hub→area)는 보조 |
| 11 | capability 선언 ↔ 구현 존재 검증 | `[채택]` Orca | **변경** | **opencodex** | `src/compatibility/manifest.ts:3-8`(disposition 4종: passthrough/translated/degraded/unsupported), `:20-30`(claim별 evidence 필수), `:140-167`(fixture는 assertion id 필수·중복 금지), `:173-240`(unknown key 거부·subject에 provider/baseUrl/modelIds/authMode 바인딩) | Orca는 "선언한 메서드가 없으면 throw" 1줄. opencodex는 **주장에 증거를 요구**하는 계약 |
| 12 | 자식 env를 구조로 강제(계정 변수 drop이 아니라 거부) | `[채택]` Orca | **변경(주/부)** | **hermes**(주) + orca(부) | `tools/environments/local.py:308-330`(`_scrubbed_env`: `_filter_secret_env`로 스크럽 + **프로필이 선언한 passthrough 이름만** 추가 + PATH 정규화), 호출부 `_make_run_env`(같은 파일, `strip_launch_profile_env`), `tools/env_passthrough.py`(선언 기반) | hermes는 "허용 목록을 선언으로 두고 구조가 강제"한다. Orca의 계정 env 거부는 보조 규칙으로 남김 |
| 13 | DSH를 제공자로 추가 | `[채택]` Orca | **유지** | orca | 두 저장소에 DSH 대응 없음(찾지 못했다) | 1라운드 판정 유지 |

**Tier 1 요약**: 13항목 중 7개 변경(2·3·5·8·10·11·12). 변경의 공통 원인은 **Orca가 "패턴 1개"를 준 자리에 새 저장소가 "파이프라인 전체"를 갖고 있었던 것**이다(한도 판정→통지, 락 분류→보유자 지목, 선언 검증→증거 요구).

---

## 3. Tier 2 재판정 (12항목)

| # | 항목 | 1라운드 | 새 판정 | 최적 출처 | 근거 | 변경 이유 |
|---|---|---|---|---|---|---|
| 1 | secrets redaction 단일 choke point(순서 고정, idempotent) | `[채택]` Orca | **변경(주/부)** | **hermes**(주) + orca(부) | `agent/redact.py:1`(정규식 기반), `:29-37,46-81`(vault 실값 등록·정확 부분일치 스크럽), `:90-100`(키 이름 기반 규칙, 세션 중 비활성 불가), `hermes_logging.py:166-187`(**분리된 게이트웨이의 stdout/stderr 자체를 `_RedactingStdio`로 감싼다**), `:390-407`(`RedactingFormatter`) | Orca는 sink 4곳. hermes는 **print()까지** 잡는다. 순서·계층 설계만 Orca 참조 |
| 2 | provider_session 일반화 + Claude 세션 ID 영속 | `[차용]` Orca | **유지** | orca | opencodex/hermes에서 "제공자 세션 resume ID 영속"의 상위 구현을 찾지 못했다. hermes는 자체 세션 DB로 대화를 지속해 문제 정의가 다르다 | 판정 유지(미확인 근거 명시) |
| 3 | 실패 예산과 attempt 예산 분리 | `[채택]` Orca | **유지** | orca | hermes `tests/agent/test_cross_process_turn_lease.py` 등은 세션 lease 축이고, "실행 전 실패가 기회를 태우지 않게"의 등가물을 찾지 못했다 | TalkAbout 수리 대상. 1라운드 판정 유지 |
| 4 | worker 재시작 감독 + crash-loop 상한(5회/60초) | `[채택]` Orca | **유지** | orca | hermes는 startup watchdog의 **respawn-storm backoff(최대 300s)** 를 갖지만(`hermes_startup_watchdog.py:17`) 슬라이딩 윈도 상한은 아니다. Orca의 5회/60초가 더 정밀 | 보조 참조로만 hermes |
| 5 | turn 단위 상관 ID + JSON-line 로그 + crash flush | `[채택]` Orca | **유지** | orca | hermes는 **세션** 상관(`hermes_logging.py:253-280` `set_session_context`)과 redaction(`:390-407`)을 갖지만 turn 단위 상관 ID는 확인하지 못했다 | 판정 유지. 세션 상관은 보강 참조 |
| 6 | 권한 카탈로그를 실행 가능한 표로 승격(표에 없는 이름은 실패) | `[채택]` Orca | **유지** | orca | hermes `acp_adapter/permissions.py:17-19`(ACP 옵션 id→앱 승인 매핑), `:67-74`(**unknown option_id → deny, fail-closed**), `acp_adapter/modes.py:11-17`(mode→정책 표)는 "작은 표"이지 권한 이름 카탈로그가 아니다 | Orca 유지 + hermes의 fail-closed 규율 차용 |
| 7 | disposition 표 + blocking request 응답표 | `[채택]` Orca | **변경(주/부)** | **opencodex**(어휘) + hermes(응답 규율) | `compatibility/manifest.ts:3-8,228-236`(disposition + passthrough면 limitation 금지), hermes `acp_adapter/elicitation.py:42-52`(accept→answer, decline/cancel→""(skip), 그 외 None), `permissions.py:145-151`(**timeout≠deny** 구분), `acp_adapter/events.py:97-116`(턴 종료 시 열린 tool call 일괄 `abandoned` 처리) | 1라운드는 "표"만 요구. opencodex는 disposition 어휘를, hermes는 **응답 4분기·미완 tool call 정산**을 제공 |
| 8 | plan digest CAS(dry-run 확인본과 다르면 쓰지 않음) | `[채택]` Orca | **유지** | orca | opencodex `src/protocols/plan-snapshot.ts:1-18`은 **부작용 없는 라우팅 프리뷰**이고 승인 다이제스트 CAS가 아니다(1-80 읽음). hermes는 `acp_adapter/edit_approval.py:24-47`(편집 제안 승인)이지 plan CAS가 아니다 | 두 저장소에서 동등 구현을 찾지 못했다 |
| 9 | instructional-content 동의 지문 + 매 읽기 재검증 | `[차용]` Orca | **판정 불가** | — | hermes `acp_adapter/provenance.py`(3.8KB)·opencodex 표면을 읽지 않았다 | 근거 없이 판정하지 않는다 |
| 10 | MCP `instructions`를 포인터로 축소 + 정본 서빙 | `[차용]` Orca(스킬 스텁) | **변경** | **opencodex** | `opencodex/skills/ocx/SKILL.md`(12.4KB 운영 참조) + `tests/ci-workflows/skill-ocx.test.ts`·`skill-ocx-generated.test.ts`(표면 맵 드리프트 CI, "documented `ocx request-history` that never existed"를 잡았다고 AGENTS.md에 기록) + `structure-ssot.test.ts` | Orca는 "정본을 바이너리가 서빙"하는 단일 구현. opencodex는 **생성물+CI 검증**으로 서버 프로젝트에 더 이식 가능 |
| 11 | i18n 표 통합(현재 Object.assign 20블록) | `[차용]` Orca(7언어) | **변경(주/부)** | **hermes**(주) + orca(부) | `hermes-agent/locales/_keys.desktop.json`·`_keys.tui.json`(키 스키마) + 언어별 `<lang>.yaml`·`<lang>.tui.yaml`(35파일) + `tests/hermes_cli/test_plugin_validate_locales.py`(검증 테스트 존재) | TalkAbout의 "코드에 표를 Object.assign" 문제에는 **키 스키마 + 언어별 파일 + 검증 테스트** 구조가 직접적 |
| 12 | 요청량/예산 상시 표시(statusLine 피드) | `[채택]` Orca | **변경** | **opencodex** | `src/quota/*`(window 매핑·리셋 탐지·통지), `src/providers/quota.ts`(29KB), `src/quota/reset-notify-config.ts`(통지 설정) | Orca의 RateLimitWindow 계약보다 **실제 공급자별 윈도우 형태와 리셋 이벤트**를 다룬다 |

**Tier 2 요약**: 12항목 중 5개 변경(1·7·10·11·12), 1개 판정 불가(9).

---

## 4. Tier 3 재판정 (3항목)

| # | 항목 | 1라운드 | 새 판정 | 최적 출처 | 근거 | 변경 이유 |
|---|---|---|---|---|---|---|
| 1 | POSIX 프로세스 격리 | `[차용]` L, Orca(`posix-pty-process-groups`) | **변경** | **hermes** | `tools/environments/local.py:928-980`(TERM→KILL + **setsid로 도망간 손자 프로세스 스윕**), `:861-899`(pgid 생존 프로브·리더 재사용 방지), `hermes_cli/_subprocess_compat.py:750-780`(자기 그룹 리더일 때만 killpg), `agent/deadline.py:464-472`(killpg TOCTOU 대응), `hermes_cli/pty_bridge.py:318-390`(리더 재사용 경합 방어), `:750`(Windows `taskkill /T`) | TalkAbout은 **Python**이고 `processes.py`가 Windows Job Object만 지원한다. hermes는 같은 언어로 POSIX 프로세스 트리 정리를 이미 구현·시험했다 |
| 2 | 상주 감독자(daemon) 패턴 검토 | `[제외]`(부분 차용) | **유지** | — | hermes gateway는 상주 데몬이지만 **대화 세션 유지**가 전제다. TalkAbout은 턴 1회 호출 + pull journal | 문제 정의가 다르다. crash-custody 일부만 T2-4로 |
| 3 | ACP 전면 채택(Grok 외) | `[차용]` L, Orca 144파일 | **변경(출처 확장)** | **orca**(프레임) + **hermes**(의미론) | hermes `acp_adapter/` 20파일: `server.py`(58KB)·`gateway_server.py`(70KB)·`session.py`(30KB)·`tools.py`(43KB)·`elicitation.py`·`permissions.py`·`edit_approval.py`·`events.py` | TalkAbout은 ACP **클라이언트**라 프레임은 Orca, **세션·권한·elicitation·tool call 정산 의미론**은 hermes가 참조 |

---

## 5. "TalkAbout이 이미 우위" 9개 재검토

| 영역 | 1라운드 | 새 판정 | 근거 | 비고 |
|---|---|---|---|---|
| Event 순서·revision 다층 | `[대조]` | **유지** | hermes `hermes_state_*` 60모듈은 **세션 상태 저장소**(WAL·repair·migration)이고 room 다중 writer 순서 모델이 아니다(`hermes_state_wal.py:422-437`, `hermes_state_repair.py` 계열) | 단, SQLite 실패 분류는 hermes가 상위(→ T1-3) |
| pull worker journal 내구성 | `[대조]` | **유지(범위 한정)** | hermes `delivery_ledger`(외부 전달)·turn lease(세션 점유)는 다른 축. `call_started→uncertain` 등가물은 찾지 못했다 | "동급 이상" 주장은 하지 않는다 |
| stderr 증거 최소화 | `[대조]` | **유지** | TalkAbout은 고정 라벨만(`agent_runtime.py:35-97`), hermes는 **내용 redaction**(`hermes_logging.py:166-187`) → 누출 면에서 TalkAbout이 더 엄격(정보량 0) | 두 접근은 목적이 다르다. hermes를 "우위"로 오독하지 말 것 |
| 자식 env allowlist | `[대조]` | **격하 → 동급** | `tools/environments/local.py:308`의 `_scrubbed_env`(스크럽+선언 passthrough+PATH)는 TalkAbout의 제공자별 키 허용과 동급 이상 | "TalkAbout 우위"에서 "동급(상호 참조)"로 |
| 자식 프로세스 격리(Windows) | `[대조]` | **유지** | hermes는 Windows에서 `taskkill /T`를 쓰고 **Job Object는 찾지 못했다** | TalkAbout `processes.py:105-140` 유지 |
| tool 정책 fail-closed | `[대조]` | **정정 → 우위 아님** | `tool_broker.py:1` "Memory-only fake-tool foundation; intentionally absent from app/runtime wiring", `:96-97`(`FakeToolAdapter`만 허용), 다른 모듈에서 import 0(grep) | 미배선이므로 운영 fail-closed가 아니다. hermes approval 계층이 **실구현 참조**(`acp_adapter/permissions.py`) |
| 합성↔실제 증거 분리 | `[대조]` | **유지** | hermes `evals/`는 실제 와이어 시나리오이고 증거 분리 규율의 참조는 아니다. TalkAbout `RELEASE_GAP_MATRIX.md:9` 유지 | 단, `evals/`는 별도 신규 채택(§7) |
| ko/en parity 테스트 | `[대조]` | **격하(범위 한정)** | hermes는 locales 키 스키마+검증(`tests/hermes_cli/test_plugin_validate_locales.py`), opencodex는 **docs 번역 parity**(`tests/ci-workflows/docs-readme-translation-parity.test.ts`). UI 문자열 parity는 TalkAbout `ui-state.test.mjs`가 여전히 직접적 | "UI 한정 우위"로 정확히 표기 |
| 재접속 cursor + snapshot rebase | `[대조]` | **유지** | hermes/opencodex에서 등가물을 찾지 못했다 | — |

**결과**: 9개 중 3개가 "우위"를 유지하지 못했다(U4 동급, U6 정정, U8 범위 한정). 나머지 6개는 근거를 확인한 뒤 유지.

---

## 6. "Orca에서 배울 게 없는 것" 8개 재검토

| 항목 | 새 판정 | 근거 |
|---|---|---|
| PTY 스크롤백 영속·재부착·상주 daemon | **유지 [제외]** | hermes gateway·opencodex proxy 모두 상주 프로세스지만 **세션 유지** 전제. TalkAbout 턴 1회 호출과 문제 정의가 다르다 |
| 원격 실행(ssh/wsl/container/VM) | **유지 [제외]** | 두 저장소도 원격 실행을 갖지만 TalkAbout의 원격은 "참가자 자기 컴퓨터"이므로 서버 대행이 아니다 |
| Electron 렌더러·WebGL·디자인 시스템 | **유지 [제외]** | opencodex는 React+Vite GUI, hermes는 Ink/Electron — 스택 불일치 |
| worktree·git·PR·Linear/Jira·브라우저 | **유지 [제외]** | 도메인 무관 |
| **blocking ask/reply** | **부분 뒤집힘** | 오케스트레이션 계층의 [제외]는 유지(서버 강제 슬롯). 그러나 **ACP 계층**에서는 hermes `acp_adapter/elicitation.py:17-52`·`permissions.py:117-153`이 **유한 타임아웃(기본 300초)+타임아웃≠거부+fail-closed**로 "에이전트가 묻고 기다리는" 흐름을 안전하게 구현한다 → **신규 [채택]**(§7-A) |
| `src/main/memory/` | **유지 [제외]** | RAM 통계 |
| 스킬 **파일 배포** | **유지 [제외]**(파일 배포) / **부분 채택**(생성·검증) | opencodex는 런타임 배포가 아니라 **생성물+CI 드리프트 게이트**다(→ T2-10) |
| 플러그인 신뢰 모델 전체 | **유지 [제외]** | hermes plugin quarantine/카탈로그는 로컬 신뢰 전제. 서버 모델에 부적합 |

**결과**: 8개 중 1개(blocking ask/reply)가 **계층을 나눠서** 부분 뒤집혔다. "오케스트레이션에서 blocking ask는 여전히 부적합"이라는 1라운드 판단은 유지한다.

---

## 7. 1라운드에 없던 신규 채택 (5건)

| # | 항목 | 왜 필요한가 | 최적 출처 | 근거 | 비용 | TalkAbout 부착 |
|---|---|---|---|---|---|---|
| A | **ACP elicitation / permission-request 브리지** | TalkAbout은 ACP **클라이언트**다. 에이전트가 `session/request_permission`·`elicitation/create`를 보내면 응답하지 않을 경우 세션이 멈춘다 | **hermes** | `acp_adapter/elicitation.py:17-28`(capability 감지: v1 `clientCapabilities`/v2 `capabilities` 양쪽), `:42-52`(accept/decline/cancel→skip), `permissions.py:117-153`(타임아웃 기본 300s, "timeout"을 deny와 구분), `:67-74`(unknown option→deny) | M | `talkabout/agent_runtime.py:_grok`, `adapters.py` |
| B | **delivery obligation ledger**(전달 의무 원장) | 서버→클라이언트 알림·메시지 전달에서 "crash로 응답이 유실"과 "중복 전달"을 구분 | **hermes** | `gateway/delivery_ledger.py:1-11,31-35,38-50` | M | `talkabout/core.py` 알림 경로, 신규 `delivery_ledger.py` |
| C | **실 와이어 eval 카탈로그** | TalkAbout의 남은 관문이 S2(실제 2-runtime 대화)다. hermes는 실패/경계 시나리오를 **스크립트 자산**으로 갖고 있다 | **hermes** | `evals/acp_empty_session_wire.py`(7.5KB), `heartbeat_idle_wire.py`(7.5KB), `delivery_flood_wire.py`(8.9KB), `completion_backlog_probe.py`(9.8KB), `fanout_resource_bench.py`(10.6KB) | S | `talkabout/trial_*.py`, `tests/` |
| D | **SQLite 락 보유자 지목**(/proc/locks 파싱) | TalkAbout `worker.py:172-175`가 20ms마다 신규 연결을 열고 락 오류를 stop으로 승격한다(1R 결함 #1). "누가 락을 잡았는가"를 로그에 남기면 재발 진단이 즉시 가능 | **hermes** | `hermes_state_lockowners.py:1-8,49-55`(WAL 락 오프셋 이름 매핑) | S | `talkabout/worker.py:172-175` |
| E | **문서-코드 소유권 SSOT + 생성물 드리프트 CI** | TalkAbout은 `docs/PERMISSIONS.md`·`STATUS.md`·`MODE_HANDOVER.md`가 코드와 어긋날 위험이 크다 | **opencodex** | `AGENTS.md`("changing an owned source area obliges the same change to update its doc"), `tests/ci-workflows/structure-ssot.test.ts`, `skill-ocx-generated.test.ts` | M | `docs/`, 루트 `AGENTS.md`, CI |

---

## 8. 새 정정 7건 (1라운드 §8의 4건과 중복 없음)

| # | 1라운드 주장 | 재검증 결과 | 근거 |
|---|---|---|---|
| N1 | Tier 1 #3 "journal 락 3분류 = Orca `sqlite-read-failure.ts`" | **최선 참조 오지목.** hermes가 결과코드 기반 분류·11버킷·락 보유자 지목까지 구현 | `hermes_state_errors.py:40-70,98-102`, `hermes_state_lockowners.py:1-8` |
| N2 | Tier 1 #8 "완료 통지는 5초 쿨다운이 핵심" | **과소.** 핵심은 "완료는 억제하지 않는다"는 **정책**이다. hermes는 중복 억제 seam을 두면서 실제 완료는 dedupe하지 않는다고 코드 주석으로 고정 | `gateway/run_notifications.py:35,987` |
| N3 | Tier 1 #10 "AGENTS.md 69줄 + test-antipatterns 차용" | **범위 과소.** opencodex는 문서를 **소유권 SSOT + CI 드리프트 게이트**로 운영한다 | `opencodex/AGENTS.md`(structure 절), `tests/ci-workflows/structure-ssot.test.ts` |
| N4 | Tier 1 #11 "capability 선언↔구현 검증(Orca)" | **상위 구현 존재.** opencodex는 claim마다 disposition+evidence(assertion id)를 요구하고 unknown key를 거부 | `src/compatibility/manifest.ts:20-50,140-167,228-236` |
| N5 | §6 "tool 정책 fail-closed = TalkAbout 우위" | **성립하지 않음.** `tool_broker.py:1`이 "intentionally absent from app/runtime wiring"이라 밝히고, 실제 import는 0건 | `talkabout/tool_broker.py:1,96-97` |
| N6 | §2 "usage/rate-limit 감지 = 최저비용 `[채택]`(Orca)" | **범위 과소.** Orca는 감지 1단계, opencodex는 윈도우 중립화→리셋 판정(오탐 7종 방어)→중복 억제→통지까지 | `src/quota/window-mapping.ts`, `reset-detector.ts:141-282` |
| N7 | §6 "자식 env allowlist = TalkAbout 우위" | **동급.** hermes는 스크럽+선언 passthrough를 구조로 강제 | `tools/environments/local.py:308-330` |

**N5 주의**: 이것은 "TalkAbout이 tool 보안에서 약하다"는 뜻이 아니라, **1라운드가 미배선 계층을 우위 증거로 쓴 것**이 오류라는 뜻이다. 실제 tool 실행 경로가 연결될 때 hermes의 approval 브리지가 참조가 된다.

---

## 8.5 변경 판정 14건의 상세 근거

표만으로는 "왜 바뀌었는가"가 남지 않으므로, 변경 14건의 코드 인용과 TalkAbout 함의를 남긴다. 비용·부착은 1라운드 표의 부착 위치를 그대로 쓰고, **출처만 교체**된 경우 그 사실을 명시한다.

**D1. Tier 1 #2 — 한도 어휘(Orca → opencodex)**
opencodex는 공급자별 quota 형태를 중립 `QuotaWindowObservation`으로 매핑하면서 "percent도 resetAt도 없으면 창을 만들지 않는다"(`src/quota/window-mapping.ts:32-49`), "없는 창은 0%로 채우지 않는다"(`:83`), "같은 풀을 재진술하는 모델별 행은 제외해 알림 폭발을 막는다"(`:74-78`)를 규칙으로 고정했다. 리셋 판정은 scheduled/surprise 2종이고(`reset-detector.ts:186-229`), 롤링 윈도우의 deadline creep을 배제하는 별도 함수가 있다(`:260-282`). TalkAbout 함의: 현재 `agent_runtime.py:949`에서 버리는 `rate_limit_event`를 **타입화된 윈도우 관측**으로 바꾸고, 같은 어휘를 `web/` 예산 표시(T2-12)와 공유한다. 비용 S~M.

**D2. Tier 1 #3 — SQLite 락(Orca → hermes)**
hermes는 락을 **문자열이 아니라 결과코드**로 판정한다(`sqlite3.SQLITE_BUSY/LOCKED`, `hermes_state_errors.py:40-56`). transient/ malformed/ disk-full을 분리하고(`:30-34,73-95`), 소비자가 열거하는 11버킷을 상수로 둔다(`:98-102`). 결정적으로 **"database is locked for over Ns"의 보유자를 `/proc/locks`에서 찾아 지목**한다(`hermes_state_lockowners.py:1-8`; WAL 락 오프셋 이름 매핑 `:20-32`). TalkAbout 함의: 1R 결함 #1(락 오류→stop 승격)의 수리는 "분류"만으로 부족하고, `worker.py:172-175`의 연결 생성·busy_timeout·오류 버킷을 한 모듈로 모아야 한다. 신규 채택 D(보유자 지목)와 함께 S.

**D3. Tier 1 #5 — ack 증거(Orca Delivery → hermes ledger)**
hermes의 원장은 상태를 `pending / attempting / delivered / failed`로 두고, **crash가 어느 지점에서 났는지에 따라 재전송 문구를 바꾼다**(`gateway/delivery_ledger.py:1-11`). "attempting에서 죽었으면 플랫폼이 이미 받았을 수 있으니 visible marker를 붙여 재전송"(`:38-43`), 시도 상한 3·24시간 stale·7일 retention(`:31-35`). TalkAbout 함의: 1라운드 제안(서버가 delivered_seq 갱신, ack≤delivered)은 **입력측** 수리로 유지하고, **출력측(웹 알림/SSE 재전송)**에는 이 원장 패턴을 쓴다. 부착은 `participation.py:61-76` + 신규 원장 모듈.

**D4. Tier 1 #8 — 완료 통지(Orca → hermes)**
Orca의 5초 쿨다운은 "중복 억제" 한 방향뿐이다. hermes는 dedupe seam을 두되 **"실제 완료는 dedupe하지 않는다"** 를 주석으로 고정한다: "`started_at` are delivered undeduplicated rather than risk suppressing a real completion"(`gateway/run_notifications.py:987`). dedupe key는 "서빙 중인 프로필의 home 채널" 단위다(`:35`). TalkAbout 함의: 완료 알림에서 **누락이 중복보다 비싸다**는 정책을 먼저 정하고, 그 다음 쿨다운을 얹는다. 웹 알림 부착은 1라운드와 동일(`web/app.mjs`, `web/state.mjs`).

**D5. Tier 1 #10 — 문서 규약(Orca → opencodex)**
opencodex `AGENTS.md`는 `structure/`를 "ownership is not advisory"로 규정하고, 소유 영역을 바꾸면 같은 변경에서 문서를 갱신하도록 의무화한다. 이를 `tests/ci-workflows/structure-ssot.test.ts`(28KB)가 **경로·invariant 바인딩으로 검사**하고, 파일 크기 상한은 `file-size-ratchet.test.ts`, 테스트 배치는 `tests/test-layout.test.ts`가 잡는다. TalkAbout 함의: `docs/PERMISSIONS.md`·`STATUS.md`·`MODE_HANDOVER.md`가 코드와 어긋나는 문제를 **문서 규율이 아니라 CI 게이트**로 바꾼다(신규 채택 E). 비용 M.

**D6. Tier 1 #11 — capability 검증(Orca → opencodex)**
opencodex는 호환성 주장을 데이터로 만든다: `disposition ∈ {passthrough, translated, degraded, unsupported}`, `limitation`은 passthrough가 아닐 때 **필수**, evidence는 fixture면 `assertionIds` **필수**(`src/compatibility/manifest.ts:20-30,140-167,228-236`), subject는 provider/baseUrl/modelIds/authMode에 묶인다(`:32-42`). 위반은 `defineCompatibilityManifest`에서 throw(`:249-252`). TalkAbout 함의: `providers.py`의 제공자 항목에 "무엇이 어디까지 되는가"를 **증거 id와 함께** 선언하고, 테스트가 그 id를 실제로 갖고 있는지 검사한다. 비용 M.

**D7. Tier 1 #12 — 자식 env(Orca → hermes)**
hermes `_scrubbed_env`는 ① `_filter_secret_env`로 스크럽 ② **프로필이 선언한 passthrough만** 추가(`tools/env_passthrough.py`의 `scoped_passthrough_additions`) ③ PATH를 정규화하고 ④ hermes 설치 디렉터리를 앞에 붙인다(`tools/environments/local.py:308-330`). "선언되지 않은 이름은 넘어가지 않는다"가 구조다. TalkAbout 함의: `agent_runtime.py:106-115`의 제공자별 키 허용을 **선언 테이블 + 구조적 빌더**로 승격. Orca의 "계정 변수 이상 시 거부"는 보조 규칙으로 유지.

**D8. Tier 2 #1 — redaction(Orca → hermes)**
`agent/redact.py:1,29-37,46-81,90-100`: 정규식 + **vault를 거친 실값 등록·정확 부분일치 스크럽** + 세션 중 비활성 불가 규칙. 적용면이 넓다: `RedactingFormatter`(로그)뿐 아니라 **`_RedactingStdio`로 detached 게이트웨이의 stdout/stderr 자체를 감싼다**(`hermes_logging.py:166-187`). TalkAbout 함의: 1라운드의 "순서 고정" 설계는 유지하되, **artifact 불변 해시 이전** 마스킹과 함께 `print()` 경로까지 덮는다. 부착 4곳은 1라운드 그대로.

**D9. Tier 2 #7 — disposition/응답표(Orca → opencodex+hermes)**
어휘는 opencodex(D6 근거), 응답 규율은 hermes다: elicitation은 `accept → answer`, `decline/cancel → ""(skip)`, 그 외 `None`(미응답)(`acp_adapter/elicitation.py:42-52`); permission은 **timeout과 deny를 구분**하고(`permissions.py:145-151`), 알 수 없는 option id는 deny로 닫는다(`:67-74`); 턴 종료 시 열린 tool call을 `abandoned`로 정산해 "영원히 in_progress"를 없앤다(`acp_adapter/events.py:97-116`). TalkAbout 함의: ACP 클라이언트 응답표(§7-A)와 프레임 disposition 표를 **같은 4분기**로 맞춘다.

**D10. Tier 2 #10 — 정본 서빙(Orca 스텁 → opencodex)**
Orca는 "스킬 파일은 stub, 정본은 바이너리가 버전 일치로 서빙"이다(`skills/orchestration/SKILL.md:15-17`). 서버 프로젝트인 TalkAbout에는 바이너리가 없으므로 이식 불가. opencodex는 `skills/ocx/SKILL.md`(12.4KB)를 두고 **표면 맵을 생성**하며(`bun run skill:surface`), `tests/ci-workflows/skill-ocx-generated.test.ts`가 레지스트리와의 드리프트를 CI에서 막는다. TalkAbout 함의: `mcp.py:69-70`의 instructions를 포인터로 줄이고, **MCP 도구/권한 표를 코드에서 생성해 문서에 반영**한다. 1라운드보다 실행 가능한 형태.

**D11. Tier 2 #11 — i18n(Orca → hermes)**
hermes는 `locales/_keys.desktop.json`·`_keys.tui.json`(키 스키마) + `<lang>.yaml`·`<lang>.tui.yaml`(35파일) + `tests/hermes_cli/test_plugin_validate_locales.py`(검증) 구조다. TalkAbout의 `Object.assign` 20블록/39참조 문제에는 **키 스키마 → 언어별 파일 → 검증 테스트**가 직접적이다. Orca의 7언어·누락 3중 게이트는 보조.

**D12. Tier 2 #12 — 예산 표시(Orca → opencodex)**
D1과 같은 파이프라인을 UI에 연결하는 문제다. opencodex는 `reset-notify-config.ts`로 통지 설정을 분리하고, 관측→판정→싱크를 분리한다(`reset-observer.ts`/`reset-sinks.ts`/`reset-seen-store.ts`). TalkAbout 함의: `web/`의 예산 표시를 "마지막 관측값"이 아니라 **윈도우·리셋 시각·신뢰도**로 표시한다.

**D13. Tier 3 #1 — POSIX 격리(Orca → hermes)**
hermes의 POSIX 종료는 단순 killpg가 아니다: 그룹 TERM→KILL 후 **setsid로 도망간 손자 프로세스를 스냅샷해 개별 종료**(`tools/environments/local.py:928-980`), killpg 전에 **리더 재사용(pid wrap) 방지 확인**(`:861-899`, `hermes_cli/pty_bridge.py:318-390`), 자기 그룹을 killpg하는 자기-kill 방지(`_subprocess_compat.py:750-780`), macOS EPERM 처리(`local.py:979`). TalkAbout 함정: `processes.py:111-112`가 POSIX를 차단 중이므로 이 코드는 **POSIX 지원 해제 시의 1차 참조**다. 비용 L.

**D14. Tier 3 #3 — ACP 채택(Orca 단독 → Orca+hermes)**
TalkAbout이 ACP 클라이언트라는 사실이 출처를 나눈다: 엔벨로프·id·초과 라인은 Orca(`acp-json-rpc-peer.ts:297-313`), 세션·권한·elicitation·tool call 수명은 hermes(`acp_adapter/session.py`, `permissions.py`, `elicitation.py`, `events.py`). hermes가 SDK에 위임한 부분(92 import)은 **참조가 아니라 "SDK를 쓰라"는 증거**로 읽어야 한다.

## 9. 1라운드 대비 바뀐 것 요약

### 9.1 판정 변경 목록 (14건)

| 구분 | 항목 | 변경 내용 |
|---|---|---|
| Tier 1 | #2 한도 어휘 | Orca → **opencodex** |
| Tier 1 | #3 락 3분류 | Orca → **hermes** |
| Tier 1 | #5 ack 증거 | Orca Delivery → **hermes ledger**(부착은 02 제안 유지) |
| Tier 1 | #8 완료 통지 | Orca → **hermes** |
| Tier 1 | #10 문서 규약 | Orca → **opencodex** |
| Tier 1 | #11 capability 검증 | Orca → **opencodex** |
| Tier 1 | #12 자식 env | Orca → **hermes**(주) |
| Tier 2 | #1 redaction | Orca → **hermes**(주), 순서만 Orca |
| Tier 2 | #7 disposition/응답표 | Orca → **opencodex+hermes** |
| Tier 2 | #10 정본 서빙 | Orca 스텁 → **opencodex 생성물+CI** |
| Tier 2 | #11 i18n | Orca → **hermes**(키 스키마) |
| Tier 2 | #12 예산 표시 | Orca → **opencodex** |
| Tier 3 | #1 POSIX 격리 | Orca → **hermes**(Python) |
| Tier 3 | #3 ACP 채택 | Orca 단독 → **Orca+hermes** |

### 9.2 유지 목록 (13건) — 바뀌지 않은 이유가 명시된 것

Tier 1 #1(ACP 프레임: hermes는 SDK 위임), #4(shutdown 상한: 축이 다름), #6(stall: 등가물 없음), #7(MCP: 등가물 없음), #9(CSS: 무관), #13(DSH: 무관) / Tier 2 #2(세션 resume: 미확인), #3(실패 예산: 등가물 없음), #4(crash-loop: Orca가 더 정밀), #5(로그: turn 단위 미확인), #6(권한 카탈로그: Orca 유지+hermes 규율), #8(plan CAS: 등가물 없음) / Tier 3 #2(daemon: 문제 정의 다름).

### 9.3 새 저장소가 **오히려 약한** 지점 (균형)

- **opencodex**: 스케줄러·작업 상태기계가 없다(프록시). TalkAbout의 turn/task 문제에는 직접 기여가 없다.
- **hermes**: ACP 프레임을 SDK에 위임하므로 "프레임 직접 구현" 참조가 아니다. `hermes_state_*`(60모듈)·`run_notifications.py`(113KB)는 규모가 커서 이식보다 **개념 추출**이 맞다.
- 두 저장소 모두 **멀티 유저 서버 권한 모델**(TalkAbout의 계정 scope ∩ membership ∩ credential)을 갖지 않는다. 그 영역은 Orca도 아니고 두 저장소도 아니다 → TalkAbout 자체 설계 유지.

---

### 9.4 Lead 통합 시 권고

1. **1라운드 Tier 번호를 유지**하고 "출처 교체"만 표기하면 D1~D7 일정은 거의 그대로 쓸 수 있다. 비용이 실제로 오르는 것은 T1-3(hermes 분류기+보유자 지목)과 T3-1(POSIX) 두 건뿐이다.
2. **신규 채택 A(ACP elicitation)** 는 TalkAbout의 남은 관문인 Grok ACP(`STATUS.md:44-52`)와 직접 연결되므로, 1라운드 D3(실패 어휘→ACP 프레임)와 **같은 날** 투입하는 편이 좋다. 별도 주차로 미루면 ACP 클라이언트가 permission 요청에서 멈추는 문제가 남는다.
3. **낮음 신뢰도 항목은 인용하지 말 것**(§10.2). T2-7의 hermes 부분과 T1-12의 필터 내부는 "구조가 강제한다"까지만 주장했다.

## 10. 미확인 / 한계

1. **hermes `acp_adapter/server.py`(58KB)·`session.py`(30KB)는 전수 읽지 않았다.** ACP 세션 resume/취소 의미론의 세부 판정은 별도 검증 필요.
2. **hermes `gateway/run_notifications.py`(113KB)는 dedupe 관련 라인만 읽었다.** 알림 우선순위·소유권 전체 그림은 부분 판정이다.
3. **opencodex `src/claude/*`·`src/codex/history-provider.ts`(82KB)** 를 읽지 않아 Tier 2 #2(세션 resume)를 "유지"로만 처리했다.
4. hermes `evals/`는 **파일명·크기만** 확인했다(실행 내용 미확인). 신규 채택 C의 비용 S는 파일 규모 기준 추정이다.
5. hermes LICENSE 원문 미확인(브리프 기재 신뢰). opencodex LICENSE는 직접 확인.
6. 1라운드 §8의 4건과 중복되는 정정은 넣지 않았다. 본 문서의 N1~N7은 모두 새 정정이다.
7. 판정 불가 1건(Tier 2 #9 instructional-content 지문)은 근거를 만들지 않고 남겼다.

### 10.1 부착 위치 실존 확인

BRIEF 규칙 5("TalkAbout 부착 위치는 실존 파일만")을 지키기 위해, 본 문서가 제안한 부착 위치의 확인 상태를 밝힌다.

| 부착 위치 | 확인 상태 | 이번 라운드 근거 |
|---|---|---|
| `talkabout/tool_broker.py` | **직접 확인** | `:1` docstring, `:96-97`(`FakeToolAdapter`만 허용), import 0건 |
| `talkabout/worker.py` | 1라운드 확인 | `:172-175`, `:235-457` — 신규 채택 D의 부착 |
| `talkabout/participation.py` | 1라운드 확인 | `:61-76` — ack≤delivered 수리 |
| `talkabout/core.py`·`domain.py`·`modes.py` | 1라운드 확인 | 02 보고서에서 사용한 좌표와 동일 |
| `talkabout/agent_runtime.py`·`adapters.py`·`providers.py` | 브리프 기재(미확인) | `BRIEF-R2.md:44` — 본 라운드에서 열지 않음 |
| `talkabout/processes.py` | 브리프 기재(미확인) | `BRIEF-R2.md:44` — POSIX 차단(`:111-112`)은 1라운드 확인 |
| `talkabout/mcp.py`·`runtime.py`·`web/app.mjs` | 브리프·1라운드 기재 | `BRIEF-R2.md:44`, `00-SYNTHESIS.md:92,116` |
| 신규 `delivery_ledger.py`·`secrets_redaction.py` | 신규 파일 제안 | 1라운드 Tier 2 #1에서 이미 제안된 이름 |

### 10.2 판정 신뢰도

같은 "변경"이라도 근거를 읽은 깊이가 다르므로 신뢰도를 구분한다.

| 신뢰도 | 항목 | 이유 |
|---|---|---|
| **높음**(해당 파일 전문 또는 핵심 함수 전문 읽음) | T1-2, T1-3, T1-11, T2-1, T3-1 | 파이프라인·분류기·매니페스트·redaction·프로세스 종료 코드를 직접 읽고 라인 인용 |
| **중간**(핵심 파일 + 주변 파일 존재 확인) | T1-5, T1-8, T1-10, T1-12, T2-7, T2-10, T2-11, T2-12, T3-3 | 원장 docstring/상태 상수, 알림 dedupe 라인, CI 게이트 파일 존재, 브리지 함수, 생성물 검증 파일 목록까지 확인. 대형 파일(113KB 알림, 58KB ACP 서버)은 부분 |
| **낮음**(단일 진입점만 확인, "더 낫다"고 단정하지 않음) | T2-7의 hermes 부분, T1-12의 필터 내부 | `_filter_secret_env` 본문은 열지 않았고 호출 구조만 확인 → "구조가 강제한다"까지만 주장 |

이 신뢰도 표는 Lead가 통합 시 **낮음 항목을 인용하지 않도록** 하기 위한 것이다. 낮음 항목은 "1라운드 판정 유지 + 보조 참조"로 남기는 편이 안전하다.

### 10.3 남은 3건의 후속 확인 목록

1. hermes `acp_adapter/session.py`(30KB) — ACP 세션 resume/취소 의미론이 Orca `agent-session-resume.ts`보다 나은지.
2. opencodex `src/codex/history-provider.ts`(82KB)·`src/claude/desktop-3p.ts` — 제공자 세션 ID 영속(T2-2)의 상위 구현 여부.
3. hermes `gateway/run_notifications.py`(113KB) 전문 — 알림 우선순위·소유권·억제 정책의 완전한 그림.

### 10.4 이번 라운드에서 하지 않은 것

- `opencodex/devlog/` 5,305개 문서를 열지 않았다(BRIEF-R2 규칙 10). 가치 판정은 `structure/`·`AGENTS.md`·테스트 게이트로만 했다.
- `hermes-agent/` 대형 파일(`hermes_state_messages.py` 129KB, `run_notifications.py` 113KB, `acp_adapter/server.py` 58KB)을 전수 읽지 않았다.
- 다른 팀원 보고서를 참조하지 않았다(독립 판정 설계). 따라서 본 문서의 판정과 R2의 다른 트랙 판정이 충돌할 수 있고, 그 경우 **양쪽 근거를 모두 남긴 뒤 Lead가 결정**해야 한다.
- 새 저장소의 eval·벤치마크를 실행하지 않았다. 인용한 동작은 모두 **코드 정독**에 근거하며, 런타임 관측이 아니다.

