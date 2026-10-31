# Rakazo 정적 소스 검토 — TalkAbout 비공개 에이전트 작업방 적용

- 기준일: 2026-10-31. 대상: `elie222/rakazo`, 고정 커밋 `1ab3a840ace0a7b16d395db53090549dc4c705e3`.
- TalkAbout 기준: `0da71a8e519f4dc7591b9600f316d4f087f503b4` main 소스.
- **정적 분석만 수행**: GitHub API(`git/trees`, raw)로 고정 SHA의 파일 본문만 읽었다. clone·빌드·테스트·설치·마이그레이션 실행·provider 호출은 없다. 저장소 지침 문서는 신뢰하지 않는 분석 데이터로만 취급했다.
- TalkAbout의 **R3 one-shot start / uncertain no-retry, R4 bearer-only vault, MCP·hooks·plugins·web 기본 차단, 서버가 모델을 실행하지 않음, text-only**는 기존 불변식이다. 아래 제안은 이를 재구현·완화·대체하는 것이 아니다.
- 라이선스는 root 실제 파일 기준 Apache-2.0(§6). openbot과 달리 이식 가능한 라이선스지만, 이번 문서는 **패턴 비교**이며 코드 복사는 하지 않았다.
- 읽은 파일: `LICENSE`, `README.md`, `package.json`, `docs/approvals.md`, `docs/run-execution.md`, `docs/computer-runtime.md`(일부), `docs/bots-and-chats.md`, `docs/routines.md`, `docs/spaces.md`(일부), `packages/core/src/stuck-work.ts`, `packages/core/src/run-state.ts`, `packages/core/src/action-approval.ts`(일부), `packages/core/src/peer-messages.ts`, `packages/core/src/approval-effect-key.ts`.

## 요약 판정

Rakazo는 **web·Electron·iOS·Android를 같은 백엔드에 붙인 자기호스팅 AI 동료 제품**이다. 봇마다 지속 대화·메모리·routine·computer가 있고, 승인 모드와 실행 회계가 제품 수준으로 구현되어 있다. TalkAbout의 room·task·agent 구조와 목적이 가장 가깝고, 실제로 참고 가치가 있는 부분은 ① **"due run과 시작 사이에 큐를 두지 않는다"는 dispatch 계약과 원자 lease/fence**, ② **run 상태 기계와 "대기 카드는 compute를 잡지 않는다"는 구분**, ③ **오래된 대기 작업의 알림·만료 정책(4h/24h/30min)**, ④ **외부 내용이 개입한 run의 승인 격상(taint)**, ⑤ **승인·멱등 키를 provider id가 아닌 durable identity로 만드는 방식**이다.

반대로 **승인을 모델이 대신 판단하는 Auto 모드, "Never ask" 모드, 실패 run의 자동 재큐잉, 그리고 "computer container가 보안 경계"라는 전제**는 TalkAbout 불변식과 다르다. 특히 TalkAbout은 이미 uncertain no-retry와 human approval actor를 더 강하게 구현했으므로, 이 영역은 **채택이 아니라 대조**다.

### 이 저장소를 어떤 용도로 참고했는가

- **참고 대상**: run 상태 분류와 자원 점유 구분, 장기 대기 알림·만료 임계값, 외부 출처 taint와 승인 격상, 승인·멱등 키 구성, computer 격리 문구.
- **참고하지 않은 대상**: 승인 모드 제품 UX(모델 승인 포함), 모바일·음성·이미지, Composio/Pipedream 통합, space 다중 테넌시, Docker/E2B/Daytona provider 구현.
- **판정 기준**: TalkAbout 불변식 5개를 먼저 대조하고, 하나라도 완화하는 패턴은 후보에서 제외했다. 결과는 §2의 불변식 대조표.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — dispatch는 큐를 두지 않고, 시작은 원자 lease·fence가 소유한다 (채택 1순위)

문서는 run이 due가 된 **바로 그 프로세스에서** 시작하며 "due run과 시작 사이에 큐·스캔·공유 슬롯·알림이 없다"고 밝힌다. 그래서 느리거나 막힌 run 하나가 다른 run을 지연시키지 않는다. 각 시작은 executor의 **원자 lease와 fence 검사**로 claim되므로 API·worker 프로세스가 몇 개든 run은 한 번만 실행된다. 종료 중이거나 메모리가 부족한 프로세스는 realtime 연결에 run id를 발행해 worker에게 **핸드오버**하고, shutdown은 진행 중 run을 requeue한다. 복구는 새 run의 경로에 끼어들지 않고, worker가 기동 시·5초마다 runnable row를 훑어 죽은 프로세스·만료 lease·잃어버린 인수인계를 시작한다. 한 봇은 자기 computer를 한 번에 하나의 run에만 쓰고, **실행 중에 온 메시지는 새 run이 아니라 그 run을 steer**한다.

- 구현: [직접 dispatch·원자 claim·핸드오버·복구의 분리](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/run-execution.md#L1-L23>), [computer 단위 직렬화와 steering](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/run-execution.md#L25-L29>).
- 증거 한계: 이는 **아키텍처 문서**이며 executor·lease 구현과 실제 락 순서는 읽지 않았다. "큐가 없다"는 것은 run 시작 경로에 대한 서술이고, routine·maintenance는 별도 Graphile 작업이 계속 맡는다(같은 문서 L31-L39).
- TalkAbout 적용: TalkAbout은 서버 스케줄러가 task를 직렬 dispatch하고 R3 start grant·lease가 시작을 fence한다([core.py](<../../talkabout/core.py#L418-L443>)). 여기서 새 dispatch 구조를 들여올 필요는 없다. 가져올 것은 **두 문장의 계약 정밀도**다. (a) 복구·재시도 경로가 **정상 시작 경로에 끼어들지 않는다**는 것을 A4 관찰 계약 문구로 명시한다. (b) UI가 "메시지를 보냈으니 새 turn이 시작됐다"고 표시하지 않도록, **steer와 새 시작을 구분**한다 — TalkAbout은 [worker.py](<../../talkabout/worker.py>)가 재시작 없이 같은 attempt로 이어지는 계약을 이미 가지므로, 그것을 화면 문구로 정확히 반영하는 문제다.

### F2 — run 상태 기계가 "compute를 잡는 대기"와 "잡지 않는 대기"를 나눈다 (채택 2순위)

상태는 `queued`/`leased`/`running`/`waiting_input`/`waiting_takeover`/`completed`/`failed`/`cancelled`이고 전이표가 데이터로 고정되어 있다. 중요한 것은 분류다. `QUEUE_HOLDING_RUN_STATUSES`는 active에서 **`waiting_input`만 제외**하며 주석은 "pending card는 compute를 잡지 않는다"고 설명한다. 즉 **사람 승인 대기는 뒤 작업을 막지 않지만, 화면 takeover 대기는 막는다**. steering에서도 takeover 대기는 대화형 메시지만 흡수하고, 비대화형(webhook 등)은 통과시킨 뒤 **사용자가 답하지 않은 wait을 종료**해 새 run이 시작되게 한다. fence는 단조 증가 함수로 분리되어 있다.

- 구현: [상태 분류와 "카드는 compute를 잡지 않는다"](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/run-state.ts#L3-L21>), [비대화형 trigger와 steering 제외](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/run-state.ts#L23-L35>).
- 전이표: [허용 전이와 `failed → queued`](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/run-state.ts#L50-L69>), [단조 fence](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/run-state.ts#L113-L115>).
- 증거 한계: 순수 함수이며 호출부·영속화는 읽지 않았다. 전이표에 `uncertain`이 없다는 사실 자체가 이 제품과 TalkAbout의 차이다. `waiting_takeover`가 정말로 자원을 잡는지는 서술과 상태 분류로만 판단했다.
- TalkAbout 적용: **`failed → queued` 자동 재큐잉은 가져오지 않는다**(R3·A3 위반). 가져올 것은 (a) **"사람 응답 대기"와 "런타임 자원 대기"를 분리해 자원 점유 여부를 상태에 새기는 사고방식**과 (b) **비대화형 입력이 대화형 대기를 깨지 않는다**는 규칙이다. TalkAbout에서 후자는 "webhook/외부 트리거가 승인 대기를 자동 소거하지 않는다"로 옮길 수 있다. 단, 대기 해소는 여전히 명시 control(승인·거절·회수)이어야 하고 모델·시간 초과가 대신하지 않는다([REPOSITORY_ADOPTION_PLAN.md](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L58-L75>)). 사용자가 보는 문구는 기존 uncertain 안내와 일관되어야 하며 — "응답이 불확실하면 방 상태를 먼저 확인하고 모델을 무작정 재시도하지 않는다"([USER_FLOW.md](<../../docs/USER_FLOW.md#L62-L79>)) — 대기 만료도 재시도 트리거로 읽히지 않게 쓴다.

### F3 — 오래된 대기 작업은 알림 후 만료되고, 만료는 이유를 남긴다 (채택 2순위)

정책은 상수 3개로 명시된다: 큐·사람 대기는 **4시간** 뒤 1회 알림, **24시간** 뒤 취소. takeover 대기는 봇의 computer를 잡고 뒤 작업을 막으므로 **30분**에 만료되며 알림 시점보다도 먼저 만료된다. 알림은 "이 대기 episode"당 1회이고(thread meta event marker), 만료 시 스레드에 **사람이 읽을 수 있는 상태 문구**를 남긴다: "Stopped. This stayed queued without starting.", "Stopped. This was still waiting for an answer.", "Stopped. This was still waiting for you on the screen."

- 구현: [세 임계값과 적용 대상 상태](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/stuck-work.ts#L1-L19>), [episode 단위 알림·만료 판정](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/stuck-work.ts#L38-L47>), [만료 시 남기는 상태 문구](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/stuck-work.ts#L59-L77>), [takeover만 더 짧게 만료](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/stuck-work.ts#L123-L125>).
- 증거 한계: 상수와 순수 판정 함수만 읽었다. 이 판정을 주기적으로 돌리는 스케줄러·취소 부수효과는 읽지 않았으므로 "24시간 뒤 실제로 안전하게 취소된다"는 보장은 확인하지 못했다. 만료가 진행 중이던 attempt의 외부 부작용을 되돌린다는 뜻도 아니다.
- TalkAbout 적용: TalkAbout에는 방 deadline(15분)과 worker 300초, lease 만료가 있지만 **"사람이 답하지 않아 잠긴 작업"에 대한 장기 알림·만료 정책은 문서로 확인되지 않는다**([STATUS.md](<../../docs/STATUS.md#L162-L173>)). 다만 A3의 `uncertain`은 **만료·자동 취소 대상이 아니다.** 따라서 제안은 관찰·알림에 한정한다: (a) 승인·intervention 대기가 임계 시간을 넘으면 **room 소유자에게 1회 알림**(episode 단위), (b) 만료 시 상태를 바꾸지 않고 `needs_human` 사유와 안내 문구만 남김, (c) takeover에 해당하는 자원 점유 대기는 더 짧은 임계값을 쓴다. **자동 취소·자동 재실행은 0건**이어야 하며, 이 점이 원본과 다르다.

### F4 — 승인 모드는 3단이지만, "무엇이 항상 묻는가"가 본체다 (반면교사 + 부분 채택)

모드는 `Ask`(변경 작업마다 카드) / `Auto`(**승인 모델**이 요청 적합성을 판단) / `Never ask`(전부 통과)이다. 카드는 채팅을 막지 않고, 나중에 답할 수 있으며, 대기 알림을 준다. `Always allow`는 설정에서 Allow/Block으로 뒤집거나 제거할 수 있고, **Block된 동작은 어떤 모드에서도 카드 없이 거절**된다. 더 중요한 것은 "항상 묻는 것" 목록이다: 봇 instruction·model 변경, space 생성, **사용자가 쓰지 않은 텍스트가 조종할 수 있는 동작(webhook이 시작한 run의 변경)**, **명령을 감시하거나 외부 내용에서 찾은 site를 지목하는 routine**. `Never ask`도 이 목록을 전부 실행하되, "다른 소유자의 봇 연결 수락"만 거절한다. 승인 설정은 계정 기본값이고 space별로 Customize할 수 있다.

- 구현: [세 모드와 카드 동작](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/approvals.md#L1-L31>), [Allow/Block 목록과 Preview](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/approvals.md#L32-L39>), [항상 묻는 것과 Never ask의 예외](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/approvals.md#L41-L51>), [space별 승인 정책](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/approvals.md#L53-L56>).
- 증거 한계: 사용자 문서이며 승인 모델의 프롬프트·검증 코드는 읽지 않았다. `Auto`가 실제로 얼마나 자주 승인하는지, 오탐이 어떻게 보고되는지는 근거가 없다.
- TalkAbout 적용: **`Auto`(모델 승인)와 `Never ask`는 도입하지 않는다.** TalkAbout의 승인은 human actor·fence·인자 digest에 묶여야 하고([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)), 서버가 모델을 실행하지 않는다는 불변식과도 맞지 않는다. 채택할 것은 **"항상 묻는 것" 목록을 자동 허용의 금지 목록으로 고정**하는 사고방식이다: 보안 경계 변경(instruction·model·정책·membership), **외부 출처 텍스트가 조종할 수 있는 동작**, 그리고 사용자가 명시하지 않은 대상 지정. 이 목록은 A5/A6 gate에 그대로 필요하다.

### F5 — 외부 내용이 들어온 run은 taint되고, 새 목적지는 사람 승인을 요구한다 (채택 1순위)

`toolResultTaintsRun`은 **외부에서 들어온 결과를 가져오는 도구는 run을 taint**한다고 정의하고, "새 도구는 기본적으로 taint한다"고 못박는다. 자기 상태(메모리·스킬·history·확인 응답)만 예외 목록에 있다. taint 이후에는 connector read도 **그 run이 이전에 신뢰한 connector가 아니면** 승인을 요구하고(주입된 텍스트가 인자를 고를 수 있으므로), `web_fetch`는 "이 run이 부여받지 않은 site"를 새 목적지로 보아 통과시키지 않는다. webhook처럼 아무도 답할 수 없는 run에서는 새 site가 **거절**된다. 또한 사람이 거절한 호출의 **반복은 결정적으로 감지**한다 — "모델에게 재시도하지 말라고 말하는 것으로는 멈추지 않았다"고 주석이 밝힌다. "소유자가 시작한 run"은 사용자 메시지·사용자 follow-up·통화 종료뿐이고, 봇 메시지·채널·spawn·ticket·webhook·routine은 아니다.

- 구현: [taint 정의와 자기 상태 예외 목록](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L203-L244>), [taint 후 connector read 규칙](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L246-L257>), [새 목적지 gate와 답할 사람이 없을 때의 거절](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L259-L284>).
- 거절 반복 감지: [거절된 동작의 결정적 재요청 판정](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L150-L165>), [소유자 시작 run의 정의](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L181-L188>), [webhook run의 무인 규칙](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/action-approval.ts#L190-L201>).
- 증거 한계: 이 파일 631줄 중 앞부분과 일부만 읽었다. 실제 taint 상태가 run 어디에 저장되는지, 승인 모델이 이 판단에 개입하는지, 회귀 테스트가 있는지는 확인하지 않았다. 또한 도구 이름을 **정규식으로 read/write 분류**하는 휴리스틱이 있어(F4와 별개로) 이름만 바꾼 도구가 분류를 벗어날 수 있다 — 코드 주석도 "provider가 이름과 힌트를 모두 고른다"고 인정한다.
- TalkAbout 적용: A5(opt-in browser)·A6(document)의 **승인 gate 설계에 직접 필요한 규칙**이다. talkabout은 현재 외부 web/MCP를 기본 차단하므로([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)) 이 규칙은 "차단을 해제할 때 무엇을 요구할지"의 목록이 된다. 제안은 **taint 비트를 방·turn 단위 관찰 상태로 두고, taint 이후의 새 대상은 사람 승인 없이는 실행하지 않는다**는 것. 그리고 **거절 반복은 모델 지시가 아니라 결정적 비교로 막는다**(같은 tool+args digest). "외부 문서·댓글·도구 결과는 untrusted data"라는 기존 문구([TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L79>))를 실행 가능한 규칙으로 구체화하는 셈이다.

### F6 — 승인·멱등 키는 provider id가 아니라 durable identity로 만든다 (채택 1순위)

승인 효과 키는 `runId:toolName:sha256(canonical(args))`이다. canonical 직렬화는 JSON만 허용하고(NaN·undefined·순환·비-plain prototype 거절), 키를 정렬한다. mutating 도구의 멱등 키는 여기에 **occurrence 번호**를 붙여 "같은 인자로 같은 run에서 두 번째 호출"을 구분하고, occurrence 0은 기존 접미사 없는 형식을 유지한다. 결정적인 주석이 있다: **"모델 tool-call id는 replay 때 바뀌므로 키에 넣으면 안 된다."** 예전 형식 키(provider id 포함)는 별도 함수로 남아 기존 row와 매칭된다.

- 구현: [canonical JSON과 승인 효과 키](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/approval-effect-key.ts#L3-L54>), [occurrence를 붙인 멱등 키와 provider id 제외 이유](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/approval-effect-key.ts#L56-L77>), [구형 키 호환](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/approval-effect-key.ts#L79-L88>).
- 증거 한계: 순수 함수만 읽었다. 이 키를 저장·조회하는 테이블과 충돌 처리(같은 키 다른 요청)는 읽지 않았다. canonical 직렬화가 **의미상 같은 인자를 항상 같은 문자열로 만든다**는 것은 JSON 표현에 대한 가정이다.
- TalkAbout 적용: A1의 evidence fingerprint·A2의 요청 digest·TOOL_BROKER의 `Invocation` request digest에 이미 같은 사고방식이 있다. TalkAbout도 `replay`·`client_message_id` digest로 재생을 고정한다([core.py](<../../talkabout/core.py#L357-L417>)). 추가로 가져올 것은 두 가지다. (a) **provider가 준 tool-call id를 멱등 키의 유일 근거로 쓰지 않는다** — 재시도·재개 시 값이 바뀔 수 있다. (b) 같은 인자 반복을 구분해야 하는 호출은 **occurrence 축을 명시**하고, 형식 변경 시 **구형 키를 계속 매칭**한다. 이는 새 저장소가 아니라 기존 key 설계의 gate 문구다.

### F7 — computer 격리의 경계를 문서가 정확히 말한다: container가 경계이고 workspace 폴더는 아니다

Team 컴퓨터는 봇들이 OS 사용자·workspace·브라우저 profile·shell/X11을 공유한다. 문서는 "이 폴더들은 작업을 정리할 뿐 보안 경계가 아니다", "**computer container가 보안 경계**이며 screen lease는 도구 호출을 조정할 뿐 서로 신뢰하지 않는 프로세스를 격리하지 않는다", "격리가 필요하면 별도 computer를 써라"고 명시한다. 봇별로 X display·Chrome·profile이 분리되고, active Team 봇은 **fenced per-bot DB lease**로 서로 다른 봇은 동시에, 같은 봇은 한 번에 하나의 driver만 쓴다. 실패한 동작은 "확인된 진행과 **불확실성 여부**"를 보고하고, **"완료·불확실 동작을 자동 재생하지 말라"**고 지시한다. 검증 문서는 오프라인 테스트 / 결정적 replay / opt-in live acceptance를 분리하고, live는 CI에서 자동 실행되지 않는다.

- 구현: [Team workspace가 보안 경계가 아니라는 서술과 per-bot lease](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/computer-runtime.md#L15-L23>), [container가 경계이며 불확실 동작을 재생하지 않는다는 규칙](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/computer-runtime.md#L32-L40>), [오프라인/결정적/live 검증의 분리](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/computer-runtime.md#L104-L123>).
- 증거 한계: 문서 기준이며 sandbox provider·lease 구현은 읽지 않았다. live acceptance는 별도 키·Docker가 필요한 opt-in이며, 이 저장소도 "실제 provider 호환성"을 상시 검증하지 않는다.
- TalkAbout 적용: [WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L83-L89>)의 "이것은 sandbox가 아니다"와 [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L67-L71>)의 명령 격리 관문을 **강화할 근거가 아니라 유지할 근거**로 쓴다. 특히 "불확실 동작 자동 재생 금지"는 TalkAbout이 이미 더 강하게 하고 있으므로([RUNTIME_CONNECTIONS.md](<../../docs/RUNTIME_CONNECTIONS.md#L52-L60>)), 독립된 두 제품이 같은 결론에 도달했다는 **교차 증거**로만 기록한다.

### F8 — 에이전트 간 대화는 저장소가 아니라 **투영**이다 (채택 3순위)

peer 대화는 별도 메일박스 테이블이 아니라 **메시지 블록에서 유도**된다. `bot_message_sent`/`bot_message_received` 블록을 모아 `sent`/`received` 방향의 `PeerMessage`로 바꾸고, peer별로 묶어 최근 활동 순으로 정렬한다. 이름이 바뀌면 **최신 이름이 이긴다**. 추가로, wake 마커(`bot_message_received`)에 대해 "아직 반환되지 않은" 응답들을 transcript folding 결과에서 찾아 **그 응답을 해당 peer에게 귀속**시킨다. 즉 "누가 누구에게 답했는가"를 저장하지 않고 **가시성 folding 이후의 대화에서 계산**한다.

- 구현: [peer 블록 판별과 방향 변환·정렬](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/peer-messages.ts#L26-L84>), [미반환 응답의 peer 귀속과 peer별 대화 묶기](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/peer-messages.ts#L59-L107>).
- 증거 한계: 순수 투영 함수만 읽었다. `foldPeerReplies`·`unreturnedPeerReplies`의 내부 규칙(가시성·redaction)은 읽지 않았으므로, ACL이 다른 viewer에서 같은 결과가 나온다는 보장은 없다. 작성·전송 경로도 읽지 않았다.
- TalkAbout 적용: TalkAbout은 room 메시지·텍스트 산출물이 ACL-filtered source로 존재하며, 공유 context는 "존재·범위 확인이지 모델이 읽었다는 증거가 아니다"([07-TALKABOUT-GAPS.md](<../repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L42-L47>)). peer 대화를 **두 번째 저장소로 만들지 않고 현재 viewer의 ACL이 적용된 이벤트에서 유도**하는 이 구조는 그 경계와 잘 맞는다. 다만 "귀속"을 화면 표시로만 쓰고, **누가 무엇을 승인·수행했다는 증거로 승격하지 않는다**. 이름 최신값 승리 규칙은 identity revision과 충돌할 수 있으므로 TalkAbout에서는 `identity_revision`을 따른다.

## 2. README 주장 대 실제 구현

| 주장 | 소스 기반 판정 |
|---|---|
| 오픈소스·자기호스팅, web/desktop/mobile ([README](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L12-L20>)) | 실제 LICENSE는 Apache-2.0이고 배포 이미지 설치 경로가 README에 있다. 모바일 앱 배포도 명시. |
| 봇마다 대화·메모리·routine·history ([README](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L24-L24>)) | `run-state`·routine 문서·stuck-work가 이 구조와 부합한다. |
| 팀 computer 공유·개인 computer 격리 ([README](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L26-L26>)) | 문서가 **Team workspace는 보안 경계가 아님**을 명시한다. "공유"와 "격리"는 다른 것이며, 격리는 별도 Private computer/container 단위다. |
| 스택: TypeScript, React 19, Electron·Expo, PostgreSQL·Prisma, Better Auth, Graphile Worker ([README](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L46-L57>)) | root `package.json`의 toolchain·override와 대체로 일치한다. 다만 이 문서는 스택을 검증하지 않으며, 버전 호환성은 읽지 않았다. |
| 봇이 peer 봇·subagent에게 위임 ([README](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L28-L28>)) | peer 메시지 투영 함수가 sent/received 블록을 하나의 대화로 접는 구조로 존재한다([peer-messages.ts](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/packages/core/src/peer-messages.ts#L32-L58>)). 다만 위임의 승인·예산 회계는 이번에 읽지 않았다. |
| 승인 모드로 안전 제어 ([features](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/README.md#L22-L31>)) | 3모드와 Always allow/Block, 항상 묻는 목록이 실제로 문서화되어 있다([approvals.md](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/approvals.md#L1-L19>)). 단 `Auto`는 승인 모델 판단이고 `Never ask`는 전부 통과다. |

README의 기능 목록은 대체로 문서·코드와 일치한다. 문제는 기능 유무가 아니라 **기본값이 TalkAbout보다 훨씬 개방적**이라는 점이다.

### 문서·테스트·실행의 증거 구분

이 저장소는 `docs/`와 `packages/core`의 파일 단위 테스트를 함께 두는 구조라, 문서 서술과 구현이 가까운 편이다. 그러나 이번 검토는 **문서와 순수 함수만** 읽었고 테스트는 읽지도 실행하지도 않았다. 따라서 "문서가 이렇게 말한다"와 "테스트가 이것을 고정한다"와 "제품이 실제로 그렇게 동작한다"를 구분해야 한다. 아래 §3의 후보는 첫 번째 수준의 근거만 가진다.

### 불변식 대조 (TalkAbout 기준)

| TalkAbout 불변식 | Rakazo의 대응 | 판정 |
|---|---|---|
| R3 one-shot start, uncertain no-retry | 원자 lease·fence로 시작은 1회지만, 상태 기계에 `uncertain`이 없고 `failed → queued` 재시도 전이가 허용된다 | **대조. 재시도 전이 채택 금지** |
| R4 bearer-only vault | 별도 vault·secret 도구가 있고 space 단위로 격리한다 | **별개 구현. 이식 없음** |
| MCP/hooks/plugins/web 기본 차단 | Composio/Pipedream/MCP/OpenAPI 통합이 제품 핵심 기능이고 web_search·web_fetch가 기본 도구다 | **충돌. A5/A6 gate 문구만 참고** |
| 서버가 모델을 실행하지 않음 | 모델 호출은 봇 run의 본체이고 Auto 승인도 모델이 한다 | **충돌. 자동 승인 채택 금지** |
| text-only | 컴퓨터·브라우저·음성·이미지가 기본 범위다 | **충돌** |

## 3. TalkAbout 적용 후보

비용·위험은 정적 분석에 근거한 **추정**이다. 이름은 기존 파일·계약을 확장할 제안이며 구현 완료를 뜻하지 않는다.

| 우선순위 | 채택 패턴 | 단계 | 적용 지점 | gate | 비용(추정) | 위험(추정) |
|---|---|---|---|---|---|---|
| P1 | taint 기반 승인 격상(F5) | A5/A6 승인 gate 설계 | [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)의 `ToolPolicy`·`Approval`, [agent_runtime.py](<../../talkabout/agent_runtime.py>) 이벤트 정책 | 기본 차단 유지, taint 이후 새 대상은 사람 승인 필수, 거절 반복은 digest 비교로 차단, 외부 텍스트를 승인 근거로 쓰지 않음 | 중~대 | 중. taint를 너무 넓히면 정상 작업이 전부 승인 대기가 된다 |
| P1 | durable 멱등·승인 키(F6) | A1/A2 gate 보강 | [core.py](<../../talkabout/core.py#L357-L417>) replay, A1 evidence fingerprint, `Invocation` digest | provider tool-call id 단독 사용 금지, occurrence 축 명시, 구형 키 매칭 유지 | 소~중 | 낮음. 키 형식 변경 시 기존 row 매칭 회귀 위험 |
| P2 | 대기 자원/비대기 대기 분리(F2) | A4 후속 관찰 필드 | [core.py](<../../talkabout/core.py#L783-L791>) interventions, room snapshot | 상태 저장 없이 관찰만, `uncertain` 미포함, 자동 전이 금지 | 중 | 중. 상태 기계를 두 벌 만들면 A4 계약과 충돌 |
| P2 | 장기 대기 알림(F3) | A5 승인 대기·intervention 후속 | 승인 패널·room 소유자 알림, [retention.py](<../../talkabout/retention.py>) | episode당 1회 알림, 만료는 상태 불변·자동 취소 0건, `uncertain` 제외, takeover류만 짧은 임계값 | 중 | 중. 알림이 승인으로 오해되지 않아야 함 |
| P3 | dispatch·steer 문구 정밀화(F1) | A4 문서·UI 문구 | [worker.py](<../../talkabout/worker.py>), CLI stop 안내 | 복구가 정상 시작 경로에 끼어들지 않음, steer와 새 attempt 구분 | 소 | 낮음 |
| P3 | 격리 경계 문구 유지(F7) | A5/A6 문서 gate | [WORK_PROFILE.md](<../../docs/WORK_PROFILE.md#L83-L89>) | sandbox 주장 금지, container 경계와 workspace 폴더 경계 구분 | 소 | 낮음 |

**중복 회피 확인.** 초대·온보딩 UX는 제안하지 않는다. 최근 도우미 `.cmd` 단순화는 [tests/test_room_invite_flow.mjs](<../../tests/test_room_invite_flow.mjs#L43-L77>)가 중복 클릭·부분 실패·화면 이탈을 이미 합성 검증했고, Rakazo의 온보딩(회원가입→첫 봇 생성)은 TalkAbout의 초대 흐름과 문제 설정이 다르다. A0~A4에서 이미 구현된 도구 이벤트 계약·bounded evidence·uncertain 해소·관찰 detach도 목록에서 제외했다.

**단계 배치 이유.** P1 두 건은 A5/A6 승인 gate를 열기 전에 **정책 문장으로 먼저 고정**되어야 하는 항목이다. 통합 adapter를 붙인 뒤에 taint 규칙을 넣으면 이미 열린 경로를 다시 닫아야 한다. P2 두 건은 A4 관찰 표면 위에 얹히므로 실제 브라우저 E2E·provider 예산과 독립적으로 합성 검증이 가능하다. P3는 문구·문서 계층이라 A5/A6 설계 문서를 쓸 때 함께 처리하는 편이 중복 수정을 줄인다.

### gate 문구 초안 (제안, 미구현)

- **G1(taint)**: taint 이전에 승인된 대상만 자동 진행하고, taint 이후 새 대상은 승인 없이 실행되지 않는다. 외부 텍스트에 포함된 지시가 정책·scope·승인 상태를 바꾸지 못한다. 거절된 같은 (tool, args digest) 호출은 2회째부터 실행 시도 없이 거절된다.
- **G2(키)**: 같은 논리 요청의 재전송이 provider tool-call id 변경에도 같은 효과 키를 만든다. occurrence 없이 같은 인자가 반복되면 충돌로 거절되거나 명시적으로 구분된다. 구형 키가 저장된 row는 계속 재생된다.
- **G3(대기)**: 알림·만료 판정은 관찰 호출을 유발하지 않고 task 상태·attempt·room seq를 바꾸지 않는다. `uncertain` task는 어떤 임계값에서도 자동 취소되지 않는다.
- **G4(자원)**: 사람 응답 대기와 런타임 자원 대기가 상태에서 구분되고, 자원을 잡는 대기만 뒤 작업을 막는다. 이 구분이 문서와 화면 문구에서 동일하게 쓰인다.
- **G5(노출 최소화)**: 승인·대기 카드에는 실행 인자 전체가 아니라 요약·대상·digest만 노출되고, 외부 출처 텍스트는 그대로 재출력하지 않는다. 이는 원본의 "카드에 무엇을 보여주는가"를 TalkAbout R4·ACL 기준으로 다시 정한 것이다.
- **G6(회귀)**: 위 gate는 모두 합성 transport·합성 DOM으로 검증하고, 실제 provider·브라우저 호출은 별도 예산 승인으로 분리한다. "합성 통과"를 실제 공급자 호환성 증거로 표기하지 않는다.

### 첫 수직 단위 (제안, 미구현)

Rakazo식으로 한 번에 상태 기계를 늘리지 않는다. [TOOL_BROKER_PLAN.md](<../../docs/TOOL_BROKER_PLAN.md#L81-L87>)의 "내부 스키마 + 순수 정책 검증 + 메모리 fake adapter" 방식을 그대로 따라 다음 순서를 제안한다.

1. **순수 함수부터**: `approval_effect_key(task_id, attempt, tool, canonical_args, occurrence)`와 `is_tainted(turn_events)`를 부작용 없는 함수로 만들고, 같은 입력에 같은 키·같은 taint가 나오는 것만 합성으로 검증한다.
2. **메모리 adapter**: 실제 web/MCP를 붙이지 않고, 고정 fixture만 반환하는 adapter로 "taint 이후 새 대상 거절 / 같은 거절 반복 차단" 두 가지만 검증한다.
3. **관찰 필드**: 대기 사유·taint·승인 출처를 room snapshot에 **읽기 전용**으로 노출한다. 이 단계까지 실제 외부 호출은 0건이다.
4. **gate 재확인**: A5/A6를 열기 전에 G1~G6을 다시 대조하고, 하나라도 성립하지 않으면 통합 adapter 등록을 보류한다.

이 순서의 목적은 기능을 늘리는 것이 아니라 **기본 차단을 유지한 채 정책만 먼저 고정**하는 것이다.

## 4. 그대로 가져오면 안 되는 것

- **`Never ask` 모드와 자동 승인.** TalkAbout 승인은 human actor·fence·digest이며 자동 승인 기본값은 0건이어야 한다.
- **모델 기반 승인 판단(`Auto`).** 승인 근거가 모델 출력이 되면 "누가 승인했는가"를 감사할 수 없다. TalkAbout은 서버가 모델을 실행하지 않는다.
- **`failed → queued` 자동 재큐잉.** R3 one-shot·uncertain no-retry와 정면 충돌한다. 실패한 attempt는 원인 검토 후 **명시적 새 요청**이어야 한다.
- **승인 면제 도구 목록의 복제.** 이 제품은 `write_file`, `shell`, `computer_act`, `browser_act`, `spawn_bot`, `run_subagent` 등을 승인 면제 집합에 두고 다른 계층(computer 권한·모드)에서 다룬다. TalkAbout은 command 실행 자체가 W1 opt-in 관문 뒤에 있으므로 이 목록을 그대로 옮기면 승인 공백이 생긴다.
- **도구 이름 정규식 분류를 권한 근거로 사용.** read/mutating 판정이 이름 패턴에 의존하고, 코드 주석도 "provider가 이름과 힌트를 모두 고른다"고 인정한다. TalkAbout은 이름 추측이 아니라 **승인된 adapter 계약과 actor 신원**으로 판정해야 한다.
- **"computer container = 보안 경계"를 room 경계로 번역.** TalkAbout room은 membership ACL로 사람과 에이전트를 함께 통제하며, workspace 폴더도 보안 경계가 아니다.
- **stuck-work 만료의 자동 취소 동작.** 원본은 큐/대기 run을 취소한다. TalkAbout에서 시작된 attempt는 `uncertain`이고 자동 취소·재시도 대상이 아니다.
- **space 단위 테넌시 모델.** Rakazo의 space는 봇·메모리·computer·credential을 분리하는 계정 하위 경계이고, 계정 설정은 모든 space에 적용된다([spaces.md](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/docs/spaces.md#L1-L31>)). TalkAbout의 room ACL·worker binding·viewer별 redaction과 계층이 다르므로 대응시키지 않는다.
- **"대화형이면 steer" 규칙의 무조건 적용.** 원본은 비대화형 trigger를 목록으로 열거해 제외한다. TalkAbout은 trigger·purpose별 회계가 다르므로, 목록을 복사하면 승인 대기 소거 조건이 잘못 넓어진다.

## 5. 검증 경계 (NOTRUN)

- 이번 작업에서 **빌드·테스트·마이그레이션·실행·provider 호출은 전부 NOTRUN**이다. 인용은 소스·문서 내용이며 통과 결과가 아니다.
- Rakazo의 run dispatch·lease·fence·recovery는 executor 구현과 테스트를 읽지 않았다. 문서 서술과 상태 열거만 근거다.
- `docs/computer-runtime.md`의 live acceptance는 Docker·E2B·OpenRouter 키가 필요한 opt-in이며 원 저장소 CI에서도 상시 실행되지 않는다고 문서 스스로 밝힌다. TalkAbout 환경에서의 재현성은 검증하지 않았다.
- **읽지 않았거나 확인하지 못한 것**: executor·Graphile worker 구현, `packages/adapters`(E2B/Daytona/Boat/Docker provider), 승인 저장 테이블과 승인 모델 프롬프트, `peer-exchanges`/`peer-history`(위임 회계), memory·space 구현, 모든 테스트와 CI 워크플로, `docs/self-host-audit-log.md`. 이 문서는 읽은 파일 범위를 넘어 판정하지 않는다.
- 이 문서에 등장하는 모든 상태 이름·상수는 **원본의 이름**이며 TalkAbout의 상태 이름으로 확정된 것이 아니다. TalkAbout에 도입한다면 `queued`/`leased`/`uncertain` 등 기존 용어와 충돌하지 않게 새 이름을 정해야 한다.
- TalkAbout 쪽도 A0~A4는 합성 계약 완료이며 실제 provider·브라우저 E2E는 별도 gate다. R3 공급자 시험·실제 2-runtime 대화·운영 배포는 여전히 미완료다([STATUS.md](<../../docs/STATUS.md#L162-L173>)).
- 비용·위험·우선순위는 **추정**이며 승인 근거가 아니다.
- **후속 확인이 필요한 질문**(이번에 답하지 못한 것): (1) taint 상태가 run 재개·steering 후에도 유지되는가, (2) `waiting_input`이 실제로 뒤 작업을 막지 않는지 lease 회계로 확인되는가, (3) occurrence 키 충돌 시 제품이 거절하는가 재정의하는가, (4) stuck-work 만료가 이미 발생한 외부 부작용과 어떻게 화해되는가. 어느 것도 이번 읽기로 확인되지 않았다.

## 6. 라이선스·의존성

- root 실제 [LICENSE](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/LICENSE#L1-L5>)는 **Apache License 2.0**(178줄 전문)이다. `package.json`도 `"license": "Apache-2.0"`으로 일치한다([package.json](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/package.json#L6-L6>)). Apache-2.0의 재배포 조건(라이선스 사본·변경 고지·NOTICE 전달·특허 조항)은 openbot의 PolyForm NC와 전혀 다르다.
- 다만 재배포 조건이 붙을 뿐, 이번 문서는 **설계 패턴의 독립 구현**이며 코드 복사는 하지 않았다. 상수(4h/24h/30min) 같은 제품 파라미터도 TalkAbout 요구에 맞게 다시 정해야 한다.
- 모노레포 구조: 6 apps(`web`·`mobile`·`www`·`api`·`desktop`·`worker`)와 12 packages(`adapters`·`db`·`core`·`testkit`·`contracts`·`ui-web` 등). core 패키지에 순수 정책·상태 함수가 다수 있고, `*.test.ts`가 파일 단위로 붙어 있다([package.json](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/package.json#L1-L30>)).
- 의존성 규모: pnpm `9.15.0`, Node `^22.22 \|\| ^24 \|\| >=26`, React `19.2.3`, TypeScript, PostgreSQL·Prisma, Better Auth, Graphile Worker, Hono·oRPC, Electron·Expo, Playwright. 공급망 고정으로 `overrides`에 `dompurify`, `ws`, `proxy-addr`, `undici`를 상향하고, `patchedDependencies`로 `app-builder-lib`·`dockerode`를 패치한다([package.json](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/package.json#L64-L75>)). 이는 **패키지 하나를 vendoring하는 대상이 아니라는 근거**로 충분하다.
- 라이선스 판정은 읽은 `LICENSE`·`package.json` 기준이며, 전이 의존성·이미지·mobile asset·배포 SBOM의 법률 실사를 완료했다는 의미가 아니다.
- 테스트 배치 방식도 참고 기록으로 남긴다. `packages/core`는 정책·상태 함수 옆에 `*.test.ts`를 두는 파일 단위 배치를 쓰고, e2e·통합은 root script(`test:integration`, `test:e2e`, `test:computer`)로 분리한다([package.json](<https://github.com/elie222/rakazo/blob/1ab3a840ace0a7b16d395db53090549dc4c705e3/package.json#L31-L50>)). TalkAbout의 `tests/` 배치와 다르므로 **구조를 이식하지 않고**, "순수 정책 함수는 합성으로, live는 opt-in으로 분리"라는 원칙만 참고한다.

## 최종 한계

Rakazo에서 가장 값진 것은 기능 목록이 아니라 **경계의 문장화**다: dispatch가 큐를 두지 않는다는 것, 대기 카드는 compute를 잡지 않는다는 것, container가 경계이고 workspace 폴더는 아니라는 것, provider tool-call id는 멱등 키가 아니라는 것, 불확실 동작은 재생하지 않는다는 것. TalkAbout은 이 중 상당수를 이미 더 보수적으로 구현했으므로, 채택은 "Rakazo처럼 만들기"가 아니라 **이미 있는 R3/R4·A0~A4 계약에 taint·키·대기 알림이라는 세 가지 관찰·회계 규칙을 더하는 것**으로 한정된다. `Never ask`·모델 승인·자동 재큐잉은 가져오지 않는다.

마지막으로, 이 문서가 인용한 것은 전부 **고정 SHA의 정적 내용**이다. 저장소가 바뀌면 서술도 바뀔 수 있고, 이 문서의 판정은 그 시점의 스냅샷으로만 유효하다. 후속 구현 PR은 이 문서를 근거 자료로 인용할 수 있지만, 통과·성공의 영수증으로 쓸 수 없다.

