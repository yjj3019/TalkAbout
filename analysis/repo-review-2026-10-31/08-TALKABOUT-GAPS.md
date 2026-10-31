# 7개 저장소 교차 분석 — TalkAbout 도입 후보와 gate

**기준일 2026-10-31 · TalkAbout 기준 commit `0da71a8e519f4dc7591b9600f316d4f087f503b4` · 정적 분석만 · 제품 코드 변경 없음 · 외부/실제 실행 NOTRUN**

이 문서는 [소스 원장](00-SOURCE-MANIFEST.md)과 저장소별 분석 7편을 **TalkAbout 현재 결손에 대조해 하나의 실행 순서로 접은 결과**입니다.
새 라이브러리를 설치하거나 runtime을 교체하는 계획이 아니며, 아래 이름은 전부 **기존 파일·문서·계약을 확장하는 후보**입니다.

## 결론 (한 문단)

7개 저장소는 모두 **"에이전트가 자기 컴퓨터나 자기 샌드박스에서 일하는 것"을 제품화**한 것이고,
TalkAbout은 **"여러 소유자의 에이전트가 서버가 소유한 비공개 room 계약 안에서 만나고, 서버는 모델·도구를 실행하지 않는다"**는 반대 방향의 제품입니다.
따라서 **직접 이식할 코드는 없습니다**(serena=GPL-3.0-or-later, openbot=PolyForm Noncommercial로 코드 도입 자체가 부적합).
가져올 것은 **① 관찰과 권한의 분리, ② 승인 데이터 모델의 결정·전달·소비 분리, ③ 격리·placement 계약을 먼저 문서로 고정하는 방식, ④ 대기·자원 점유를 read-only 관찰로 노출하는 방식, ⑤ provider tool schema 드리프트를 fixture로 고정하는 검증 방식**입니다.
이 5개는 A5(browser)·A6(PDF)를 열기 **전에** 합성 테스트와 문서 gate로 먼저 고정할 수 있고, 실제 provider·VM·브라우저 호출 없이 진행 가능합니다.

## 1. 교차 수렴 — 여러 저장소가 독립적으로 같은 결론에 도달한 지점

| 수렴 항목 | 근거 저장소 | 공통 관찰 | TalkAbout에서의 의미 |
|---|---|---|---|
| **관찰 ≠ 권한, 관찰 실패 ≠ 허용** | openbot(F1 lane 사유), rakazo(F2 run 상태), OpenMausBot(관찰 불가 규칙), munder(F1 hook fail-open 반면교사), serena(도구 표시≠권한), codex(사후 관찰) | 화면·이벤트로 보인 것과 실제 권한·성공은 별개이며, 관측 실패를 허용으로 승격하는 구현은 사고로 이어짐 | A0/A4 계약에 "**관측 실패·parser 오류·hook 실패는 success/allowed로 승격하지 않고 `unknown` 유지**"를 명시적 항목으로 추가. [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L45-L68)·[REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97) |
| **승인은 사람·fence·digest에 묶고, 결정/전달/소비를 분리; 자동승인 기본 0** | openbot(F3 alwaysAsk/ownerOnly, 자동 grant 경고), rakazo(F5 taint 격상), open-duet(F2 3분리), OpenMausBot(자동 승인 어휘), munder(hook fail-open 금지), codex(approval 응답 스키마) | 승인 UI나 플래그가 실행 권한을 넓히면 안 되고, "누가·무엇을·언제" 답했는지가 남아야 함 | A5/A6 승인 gate의 **데이터 요건을 구현 전에 확정**: `Approval`에 `source(human/saved_rule/timeout)`, 결정·전달·소비 시각, 대상 digest, fence. 채팅 메시지·모델 판단은 승인 근거가 아님([TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L28-L42)) |
| **격리·경계를 먼저 계약표로 고정하고 "sandbox" 주장 금지** | rakazo(F7 container≠workspace), OpenMausBot(placement×isolation×lease), munder(경로 containment), openbot(danger-full-access 반면교사), serena(trusted path·symlink) | 제품마다 경계가 다르며, 폴더 하나를 "격리"라고 부르는 구현이 흔함 | A5/A6 착수 전 **placement×isolation×lease 계약표** 문서 gate(표 밖 backend는 실행 거절). W1 문구는 계속 "sandbox 아님" 유지([WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L83-L89)) |
| **대기·자원 점유·만료를 상태 저장 없이 관찰로 노출** | openbot(F1 보류 사유 축약), rakazo(F2 compute 대기 vs 사람 대기, F3 4h/24h/30min), open-duet(F8 read-only 진단) | "왜 아직 안 시작됐는가"를 닫힌 enum으로 보여주되, 그 표시가 상태를 바꾸면 안 됨 | A4 후속 **read-only 진단/대기 사유 표면**: snapshot 계산 시점에만 유도, 저장 금지, `uncertain`·`stale_attempt`·`dispatch_held`를 `agent-busy`로 접지 않음([REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97)) |
| **provider schema는 fixture로 고정하고 드리프트를 감지** | codex(F2 exec item 표면 확장·flag 의미 변화), serena(F? 도구 descriptor 3단 투영) | provider가 필드·도구를 추가하면 parser가 조용히 무시하거나 실패로 승격 | A0 후속 **exec JSONL fixture + 허용/거절 표 + 버전 태그**. 신규 item/필드/상한 초과는 `runtime_tool_policy_violation` 또는 `unknown`으로만 종료, 성공 승격 금지 |
| **(반면교사) 모델이 승인·라우팅·재시도를 대신 판단** | openbot(danger-full-access·approval never·grant), rakazo(Never ask·실패 run 자동 재큐잉), OpenMausBot(하네스가 CLI 소유), munder(bypassPermissions·hook fail-open) | 편의 기본값이 권한·재시도 경계를 무너뜨림 | **채택 금지.** R3 one-shot start, uncertain no-retry, 사람 승인 actor를 유지. 자동 승인·자동 재큐잉·모델 adjudication은 어떤 형태로도 도입하지 않음 |

## 2. TalkAbout 현재 결손 (G) — 7개 저장소가 가리키는 지점

| ID | 결손 | 근거(현재 상태) | 7개 저장소에서 온 신호 |
|---|---|---|---|
| **G1** | A0 exec item 표면 확장 미대응. 허용 schema에 대한 고정 fixture·버전 태그·드리프트 진단 없음 | [WORK_PROFILE.md §P0](../../docs/WORK_PROFILE.md#L45-L68) — "Codex 0.164 확인 필드 기준" | codex: exec item 종류 확장·flag 의미 변화·`features.*` no-op. serena: 도구 descriptor 3단 투영 |
| **G2** | W1 workspace 경계 재검증이 예약 전·runtime 생성 시점에 머묾. 종료 후·junction/reparse/UNC 경계 미검증 | [agent_runtime.py](../../talkabout/agent_runtime.py) workspace 검증 | serena: lexical+realpath, trusted=symlink 추종. munder: final/intermediate/dangling symlink 테스트 |
| **G3** | 승인 데이터 모델(자동승인 0, `source`, 결정/전달/소비, digest) 미확정 — A5/A6의 선행 조건 | [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L28-L42) 초안만 존재 | openbot·rakazo·open-duet·OpenMausBot·codex·munder 전부 이 지점을 건드림 |
| **G4** | A4 후속으로 "왜 아직 시작 안 됐는가"·장기 대기를 보여주는 read-only 표면 없음 | [REPOSITORY_ADOPTION_PLAN.md A4](../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97)는 detach·identity까지만 | openbot: lane 사유 enum. rakazo: 대기 종류 구분·알림 임계값. open-duet: read-only 진단 |
| **G5** | A5/A6 격리·placement 계약표 부재. "sandbox" 용어 오독 위험 | [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L83-L89), A5/A6 gate 미착수 | rakazo(container 경계), OpenMausBot(3축 표), munder(경로 containment), openbot(금지 기본값) |
| **G6** | POSIX P4(고아 회수) 미구현 → C2 활성화 보류 (기존 관문) | [POSIX_CONTAINMENT_PLAN.md](../../docs/POSIX_CONTAINMENT_PLAN.md) §7 | OpenMausBot: lease·격리 축. munder: PID start-time·지연 sweep |
| **G7** | A2 실행 활성화 전 선언 write scope 직렬화·겹침·symlink 테스트 항목 부재 | [REPOSITORY_ADOPTION_PLAN.md A2](../../docs/REPOSITORY_ADOPTION_PLAN.md#L34-L48) | openbot: 선언 자원 직렬화. munder: default-deny descriptor·containment 테스트 |
| **G8** | provider config override의 실효성(no-op/removed flag) 미확인 — "꺼졌다고 주장" 금지 문구만 존재 | [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L83-L89) | codex: `features.*` removed/no-op, 위험 flag 감지 |

## 3. 도입 후보 통합 표 (제안, 미구현)

비용·위험은 정적 분석에 근거한 **추정**입니다. "코드 변경"과 "문서·테스트 항목"을 구분합니다.

| 우선순위 | 후보 | 해결 | 단계/산출물 | 출처 | gate(증거) | 비용 | 위험 |
|---|---|---|---|---|---|---|---|
| **P0-1** | 관측 실패를 허용·성공으로 승격하지 않는 규칙 명문화 + 합성 회귀 | G1·G4 | A0/A4 문서·테스트 항목(코드 변경 없음) | munder F1, OpenMausBot, openbot, codex | stream/hook/parser 실패가 `unknown` 유지, success·allowed 승격 0건 | 1일 | 기존 unknown 의미와 중복 정의 → 한 곳(용어집)에서만 정의 |
| **P0-2** | A0 exec JSONL 고정 fixture + 허용/거절 표 + 버전 태그 | G1·G8 | 테스트 전용(A0 후속) | codex, serena | 신규 item·추가 필드·상한 초과가 성공으로 승격되지 않고 정책 위반/unknown | 1~2일 | fixture가 특정 버전에 낡음 → 버전 태그·갱신 절차 필수 |
| **P0-3** | 승인 데이터 요건 확정: `source`·결정/전달/소비·digest·fence, 자동승인 0건 | G3 | A5/A6 gate **설계 문서**(구현 전) | openbot, rakazo, open-duet, OpenMausBot, codex, munder | 자동 허용 0건 기본, source 필수, 채팅·모델 판단은 승인 아님, 전달 미확인은 unknown | 2~3일 | "승인 없는 승인" 오해 UI → 문구·source 표기 리뷰 |
| **P0-4** | 격리·placement 계약표(호스트/원격/컨테이너 × 구현/미구현/거절) + "sandbox" 용어 금지 | G5·G6 | A5/A6 착수 전 문서 gate | OpenMausBot, rakazo, munder, openbot | 표 밖 backend 실행 거절, 상태 열 필수, 구현 완료로 표기 금지 | 0.5~1일 | 표가 "승인된 구현"으로 오독 → 상태 열·범례 필수 |
| **P1-1** | W1 workspace 경계 3회 재검증 + junction/reparse/symlink 거부 단위 테스트 | G2·G7 | W1 보강 + 테스트 | serena, munder | Windows junction/symlink 거부, 겹침 거부 유지, sandbox 주장 금지 | 2~3일 | 8.3·UNC·WSL 특수 경로 오탐/누락 |
| **P1-2** | A4 후속 read-only 진단/대기 사유 표면(닫힌 enum, 저장 없음) | G4 | 관찰 API·UI(합성 DOM) | openbot, rakazo, open-duet | 호출 전후 task/attempt/`last_seq`/event 수 불변, `uncertain` 미접힘 | 2~3일 | 상태 기계 2벌 위험 → 투영만, 저장 금지 |
| **P1-3** | A2 선언 write scope 예약 직렬화·겹침 거절·symlink 미해소 명시 | G7 | A2 실행 활성화 **이전** 설계 | openbot, munder | 겹침 거절은 "권한 거절"이 아니라 "충돌"로 표시, 경로 중계 검사 | 2~4일 | 선언을 권한으로 오인 → 문구 gate |
| **P2-1** | provider config override 실효성·위험 flag 감지(help 파싱·문서) | G8 | A0 보강 | codex | 적용 config 미확인 시 "off 주장 금지", 위험 flag 감지 시 차단/경고 | 1~2일 | help 형식 의존, app-server 경로는 RPC라 별도 승인 |
| **P2-2** | 승인 3분리(결정/전달/소비) 필드 확장 — 기존 흐름 유지, 미확인은 unknown | G3 | A5/A6 후속(스키마) | open-duet, rakazo | 승인 재전송은 digest 같을 때만 replay, 전달 미확인 unknown | 1~2일 | 기존 승인 row 매칭 회귀 |
| **P2-3** | per-turn capability token·만료/회수·unbrokered 목록은 A5 설계 **입력으로만** | G5 | A5 자격증명 설계 문서 | OpenMausBot | R4 bearer-only vault 범위 확장 금지, provider key·cookie 저장 금지 | 3~5일 | 브로커가 새 실행 주체로 오독 → "서버 미실행" 유지 |
| **P3** | POSIX P4(고아 회수) 구현 → C2 활성화 | G6 | 기존 관문 그대로 | munder PID 가드 참고 | supervisor SIGKILL 후 자식 self-kill, 기존 C1 회귀 | 기존 계획 | 별도 승인·Linux 실행 필요 |

**첫 수직 단위(권장).** P0-1·P0-2·P0-3·P0-4는 **코드 실행 경로를 열지 않고** 문서·순수 검증기·합성 fixture로 끝낼 수 있습니다.
그 다음 P1-1(경로 검증) → P1-2(진단 표면) → P1-3(선언 scope) 순서로 진행하고, **A5/A6 실제 adapter는 위 gate가 모두 통과한 뒤** 별도 예산 승인으로 엽니다.

## 4. 그대로 가져오면 안 되는 것 (7개 저장소 공통)

1. **권한 기본값**: `danger-full-access`(openbot), `bypassPermissions`(munder), "never ask"(rakazo), 하네스가 CLI 설정을 소유(OpenMausBot) — TalkAbout의 text-only 기본·MCP/hooks/plugins/web 차단과 정면 충돌.
2. **모델/자동화가 승인·라우팅·재시도를 대신하는 구조**: 자동 승인 grant, 모델 승인, 실패 run 자동 재큐잉, 채널 lead의 모델 라우팅. R3 one-shot start·uncertain no-retry·사람 승인 actor를 대체할 수 없음.
3. **"sandbox"·"격리"를 폴더·containment로 주장하는 문구**: rakazo가 스스로 "container 경계, workspace 폴더 아님"이라고 밝힌 수준. TalkAbout은 W1에서 이미 "sandbox 아님"을 명시하고 이를 유지.
4. **provider tool-call id 단독 멱등키**: codex/rakazo 모두 provider id가 바뀔 수 있음을 시사. 서버 발급 attempt·digest 기준 키를 유지.
5. **초대·온보딩 UX 재설계**: 2026-10-30 도우미 `.cmd`(PR #51)로 이미 단순화했고 합성 계약이 있다([tests/test_room_invite_flow.mjs](../../tests/test_room_invite_flow.mjs)). 이번 7개 저장소 중 어떤 것도 이 흐름을 개선할 근거를 제공하지 않음.
6. **코드 복사**: serena(GPL-3.0-or-later), openbot(PolyForm Noncommercial)은 **코드 도입 부적합**. 나머지 Apache-2.0/MIT도 설계 참고만 하고, 도입 시 별도 NOTICE·라이선스 검토.

## 5. 라이선스 제약 (실측)

| 저장소 | 실측 라이선스 | TalkAbout(MIT) 도입 판정 |
|---|---|---|
| serena | GPL-3.0-or-later(앱) + MIT(SolidLSP) | **코드 도입 불가**(전염). 설계·용어 참고만 |
| openbot | PolyForm Noncommercial 1.0.0 | **코드 도입 불가**(비상업). 패턴 참고만 |
| codex | Apache-2.0 + NOTICE | 참고·fixture 설계만. 코드 도입 시 고지·특허 조항 검토 |
| rakazo | Apache-2.0 | 참고·설계만 |
| open-duet | Apache-2.0 | 참고·설계만 |
| OpenMausBot | Apache-2.0 (`enterprise/` 별도 조건) | 참고·설계만, enterprise 디렉터리 제외 |
| munder-difflin | MIT(코드) + LimeZu 아트 에셋 별도 라이선스 | 코드 패턴만 참고. 에셋·폰트 제외 |

## 6. 검증 계획과 경계

**합성으로 먼저 검증할 것(provider 예산 0):**
1. P0-1: 관측 실패 → `unknown` 유지, success 승격 0건 (합성 stream·fake hook)
2. P0-2: exec JSONL fixture의 허용/거절/상한 초과 분류, 버전 태그 회귀
3. P0-3: 승인 source·digest·fence·전달 미확인 unknown의 순수 정책 테스트
4. P0-4: 계약표에 없는 backend 실행 거절(문서 gate + 표 검증 스크립트)
5. P1-1: junction/symlink/reparse 경로 거부(Windows 단위 테스트)
6. P1-2: 진단 호출 전후 상태 불변(A4 계약 재사용)
7. P1-3: 겹치는 선언 scope 예약 거절이 "충돌"로 표시

**NOTRUN:** 실제 provider 호출, app-server, 실제 VM/컨테이너/Podman, 실제 LSP·MCP, 실제 브라우저 다운로드·격리 E2E, 성능 측정, 보안 감사.
이 문서의 후보는 **설계 제안**이며, 채택 시 `docs/REPOSITORY_ADOPTION_PLAN.md`에 단계로 편입하는 별도 PR(문서 gate → 합성 테스트 → 필요 시 실제 예산 승인)이 필요합니다.

## 7. 저장소별 판정 요약

| 저장소 | 가장 유용한 것 | 그대로 가져오면 안 되는 것 | 판정 |
|---|---|---|---|
| [serena](01-serena.md) | 도구 descriptor/capability 계약, 경로 봉쇄(lexical+realpath), 심볼 유일성→LSP edit 계약 | GPL 코드, LSP/REPL 상주, 서버 측 편집 실행 | **설계 참고만(P1 경로 검증)** |
| [codex](02-codex.md) | exec item 표면·flag 드리프트 fixture, 승인 응답 스키마, 재개 계약 | exec-server/remote·auto_review, sandbox 집행 주장, 코드 복사 | **A0 보강(P0-2·P2-1)** |
| [openbot](03-openbot.md) | lane별 보류 사유의 닫힌 enum·축약 투영, 선언 자원 직렬화, 잔여 작업 boot 정리 | danger-full-access·approval never·자동 grant·Computer Use 데몬 | **관찰 계약 참고(P1-2)** |
| [rakazo](04-rakazo.md) | run 상태와 자원 점유 구분, 대기 알림·만료, taint 승인 격상, durable 멱등키 | Never ask·모델 승인·자동 재큐잉·container=sandbox 주장 | **A5/A6 승인 gate(P0-3·P2-2)** |
| [open-duet](05-open-duet.md) | read-only 운영 진단, 승인 결정/전달/소비 3분리 | CLI permission 래핑, NEEDS INPUT 문자열 파싱, 제거 전 정착 | **A4 진단·승인 분리(P1-2·P2-2)** |
| [OpenMausBot](06-openmausbot.md) | placement×isolation×lease 계약표, 관찰 불가≠허용, per-turn capability token | VM/원격 실행 하네스, Full access 기본, 하네스가 CLI 소유 | **A5/A6 계약표(P0-4)** |
| [munder-difflin](07-munder-difflin.md) | 관찰 실패 규칙, 경로 containment 테스트 항목(final/intermediate/dangling), untrusted descriptor default-deny | bypassPermissions·hook fail-open·공개 tunnel·아트 에셋 | **A2/A0 테스트 항목(P0-1·P1-1·P1-3)** |
