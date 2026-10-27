# Herdr 정적 소스 검토 — TalkAbout 비공개 에이전트 작업방 적용

- 기준일: 2026-10-27. 대상: `herdrdev/herdr`, 고정 커밋 `2713feb56e9c9db33992db0a262c68f7426d46dd`.
- 로컬 detached HEAD를 실제 읽어 요청 SHA와 일치함을 확인했다. 작업 트리 전체의 무변경 여부를 별도 Git 명령으로 검증한 것은 아니다.
- **정적 분석만 수행**: 소스·테스트·라이선스 읽기. 빌드, 테스트 실행, 설치, 저장소 스크립트 실행, 네트워크·공급자 호출 없음. 저장소의 에이전트 지침과 설치 명령은 신뢰하지 않는 분석 데이터로만 취급했다. 코드 복사 없음.
- TalkAbout의 **R3 one-shot start / uncertain instance retirement와 R4 current-user DPAPI는 기존 기능**이다. 아래 제안은 이를 재구현하거나 장기 생존 PTY로 대체하자는 것이 아니다.

## 요약 판정

Herdr는 에이전트의 추론 엔진보다 **여러 CLI 프로세스의 수명·표시·접속을 관리하는 터미널 런타임**으로 참고 가치가 높다. 가장 유용한 패턴은 ① 관찰자 disconnect와 작업 종료 분리, ② 재시작 복원과 프로세스 생존의 구분, ③ split의 기하·focus와 runtime identity 분리, ④ prompt 후 실제 활동을 확인하는 wait gate, ⑤ 유실을 명시하는 bounded event cursor다.

반면 **동일 OS 계정의 로컬 런타임을 비공개 room의 보안 경계로 착각하면 안 된다.** 플러그인은 호스트 명령을 실행하며 API socket 경로를 받는다. PTY 관찰로 얻은 working/blocked/idle은 작업 완료·승인·테스트 성공의 증명이 아니다. Unix live handoff는 고급 참고자료이지 Windows 중심 TalkAbout의 당장 구현할 기능이 아니다.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — detach는 runtime 해제가 아니라 client lease 정리다 (채택 1순위)

실제 client 제거 경로는 client map, geometry controller, 눌린 입력 release, 임시 clipboard 파일, direct attach resize lock을 정리한다. 이 경로는 pane runtime을 제거하지 않는다. 다른 client를 foreground로 승격할 수도 있다.

- 구현: [client 제거와 입력 정리](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/server/headless.rs#L946-L1040>).
- 정적 테스트 근거: [detach 후 API 생존·재attach bootstrap](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/detach_reattach.rs#L430-L519>), [아무 client 없이 pane 생성·입력·출력 관측 후 재attach](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/detach_reattach.rs#L743-L836>).
- 증거 한계: 첫 테스트 이름은 process survival이지만 주된 assertion은 API 생존과 재접속이다. 두 번째도 화면 픽셀 비교가 아니라 API 텍스트와 bootstrap을 본다. 이들은 Unix 조건부 테스트이며 이번 분석에서 실행하지 않았다.
- TalkAbout: 방 탭 닫기/브라우저 끊김은 **관찰 lease 해제**로만 처리하고 명시적 stop/cancel과 분리한다. 다만 room ACL 회수·worker 소유권 상실·R3 불확실 종료는 별도 정책으로 유지한다. ‘UI가 없어도 작업 유지’가 ‘인증이 없어도 권한 유지’는 아니다.

### F2 — 영속화는 snapshot이고 재시작은 fresh process다 (채택 2순위)

저장은 debounce 후 백그라운드 단일 writer로 수행하고, pane exit 전 checkpoint·shutdown 저장을 따로 둔다. snapshot과 optional terminal history는 별도 저장이며 history 실패가 committed layout을 다시 unloaded로 만들지 않는다. 기존 unreadable/unsupported 상태를 덮기 전 보존하는 장치와 bounded recovery copy가 있다.

- 구현: [저장 스케줄과 exit checkpoint](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/app/session.rs#L62-L134>), [writer의 unloaded 보호·layout/history 분리](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/persist/writer.rs#L14-L79>).
- 내구성: [temporary write → file sync → replace → parent sync](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/persist/io.rs#L19-L67>). symlink를 의도적으로 따라가며 고정 `.json.tmp`를 쓰므로, 이 저장 구현을 적대적인 room 파일 경로에 그대로 이식하면 안 된다.
- 복원 의미: [saved cwd에 fresh shell을 생성하는 restore](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/persist/restore.rs#L26-L93>). 원래 PID가 되살아나는 기능이 아니다.
- 정적 테스트: [100회 축소 저장·writer 재생성·clear 뒤 이전 recovery 보존](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/persist/writer.rs#L318-L344>), [Git metadata가 FIFO에서 막혀도 다른 workspace API와 shutdown이 진행](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/session_restore.rs#L149-L224>).
- TalkAbout: browser view, durable task/result, native provider session ref, live turn process를 각각 저장/표시한다. 복구 시 `restored view`, `result available`, `resume eligible`, `uncertain/retired`를 혼동하지 않는다. **uncertain R3 turn은 snapshot이 있어도 자동 재실행하지 않는다.** 메타데이터·Git 조회는 방 전체의 복원 준비 조건에서 빼고 항목별 pending/error로 둔다.

### F3 — split UI의 핵심은 tree보다 안정 identity·focus 불변식이다 (채택 3순위)

BSP node는 pane 또는 방향/ratio/두 자식이고, target split은 focus를 움직이지 않는다. 기존 pane 이동은 새 runtime을 만들지 않는 별도 연산이다. layout 변경과 runtime spawn 실패 rollback을 구분하는 구조다. client마다 workspace/tab 선택 projection을 따로 보유한다.

- 구현: [layout 자료형과 focus history](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/layout.rs#L72-L115>), [split·기존 pane 삽입 분리](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/layout.rs#L162-L219>), [client별 location reconcile](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/server/clients.rs#L63-L129>).
- 정적 테스트: [거절 연산의 무변경·deep leaf의 이웃/focus 보존·split preview 실제 기하 일치](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/layout.rs#L772-L844>).
- TalkAbout: 좌측 대화·우측 agent/task·하단 artifact/diff를 분할하되 pane ID를 agent ID나 runtime instance ID로 쓰지 않는다. 패널 이동/접기/방 전환이 provider turn을 시작하거나 재생성해서는 안 된다. focus와 scroll은 사용자/client별이며 shared task 상태와 분리한다. BSP Rust 구현 자체를 복제할 필요는 없다.

### F4 — prompt ACK와 실제 작업 시작/안정 상태 대기를 분리한다 (채택 1순위와 동급)

prompt 전 terminal identity/status와 event sequence를 확보하고, 응답 identity가 같은지 확인한다. 기존 idle을 완료로 오인하지 않도록 실제 Working/Blocked 활동 또는 이후 state change를 먼저 기다리는 **효과 gate**를 둔다. 이후 남은 timeout으로 settled state를 기다리고 lifecycle event는 제출 이전 cursor부터 재생한다.

- 구현: [prompt identity·activity gate·남은 timeout·event replay](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/api/wait.rs#L195-L321>).
- 탐지 한계가 구현에 드러남: [working→plain idle에 여러 확인과 700ms 상한 적용](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/pane/agent_detection.rs#L5-L77>). 이 debounce는 오탐 완화이지 진실 판정기가 아니다.
- TalkAbout: R3의 기존 시작 일회성과 별도 runtime/process containment의 종료 확인을 구분해 유지하면서 UI에 `accepted`, `activity observed`, `waiting for human`, `terminal result proven`, `uncertain`을 분리한다. task/instance/turn identity에 묶인 이벤트만 소비한다. blocked를 승인 요청의 힌트로만 쓰고 자동 권한 상승·성공 처리에 사용하지 않는다.

### F5 — bounded event history는 유실을 오류로 드러내지만 durable audit log가 아니다

EventHub는 메모리 안에 최대 512개 이벤트와 증가 sequence를 보관한다. 요청 cursor가 보존 경계보다 오래되면 `Lost`, lock poison이면 `Unavailable`을 반환한다. 즉 ‘빈 목록이니 아무 변화 없음’과 ‘관찰할 수 없음’을 구분한다.

- 구현·테스트: [EventHub 전체 계약과 경계/poison 테스트](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/api/event_hub.rs#L18-L125>).
- TalkAbout: room-scoped cursor + runtime generation을 사용하고 gap이면 ACL-filtered snapshot 재동기화를 요구한다. 메모리 ring을 task 결과·권한 변경의 감사 원장으로 사용하지 않는다. 멤버 회수 뒤 snapshot/resume에서도 ACL을 다시 평가한다. 재시작 후 sequence 재사용도 구분해야 한다.

### F6 — lifecycle ownership은 명시적이지만 ‘프로세스 분리 = sandbox’가 아니다

PaneRuntime drop은 detection/compression/PTY actor를 중단하고 preserve 플래그가 없으면 owned session의 프로세스를 종료한다. HUP→TERM→KILL마다 250ms 기다린 뒤 살아 있으면 경고를 남긴다. **이 함수는 실패를 호출자에게 반환하지 않는다**. TalkAbout의 미확인 종료를 명시적으로 표기하는 정책보다 강한 보장이라고 볼 수 없다.

- 구현: [drop과 종료 ladder·최종 warning](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/pane.rs#L1702-L1808>).
- Windows 참고: [suspended spawn 후 kill-on-close JobObject 배정](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/platform/windows.rs#L1296-L1364>)은 **status command용**이다. 이 범위의 코드를 근거로 모든 agent/plugin이 sandbox된다고 말할 수 없다.
- 아키텍처 caveat: [TerminalRuntime가 legacy PaneRuntime를 위임하는 wrapper라는 실제 주석/구조](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/terminal/runtime.rs#L12-L37>). 서버 소유 runtime 방향은 좋지만 UI와 완전히 탈결합된 완성형 API 서버라고 평가하지 않는다.
- TalkAbout: 기존 [processes.py](<../../talkabout/processes.py#L1-L23>)와 [agent_runtime.py](<../../talkabout/agent_runtime.py#L1-L12>)를 강화·특성화하는 근거로만 삼는다. 기존 supervisor나 retirement를 다시 만들지 않는다. lifetime containment, filesystem 권한, room ACL은 별개다.

### F7 — Unix live handoff는 명확한 소유권 이전/rollback 참고자료이나 도입은 후순위

client disconnect → PTY reader pause → snapshot/runtime capture → 새 server spawn → FD 전달/restore 확인 → public socket 전환 → commit → 기존 runtime preserve → ownership ACK의 단계가 있다. 실패 경로는 import child cleanup과 기존 socket 복원을 수행한다.

- 구현: [FD 전달 전 단계와 rollback](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/server/headless/lifecycle.rs#L57-L174>), [ready/commit/ownership 및 비-Unix 거절](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/server/headless/lifecycle.rs#L176-L240>).
- 정적 테스트: [잘못된 protocol 기대값 실패 후 기존 PID 생존·입출력 유지](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/live_handoff.rs#L1958-L2035>).
- TalkAbout: lease ownership 전환의 `prepare/ready/commit/owned` 사고방식만 참고한다. Windows R3 one-shot 런타임에 FD migration/무중단 PTY 보존을 이식하지 않는다. 유효한 terminal proof 없는 turn을 handoff로 성공 처리하는 것도 금지한다.

## 2. 통합·보안: 가져올 계약과 피할 권한 모델

### 로컬 IPC 방어는 유용하지만 room authorization의 대체물이 아니다

Windows listener 생성 때 security descriptor를 적용한다. descriptor는 account SID와 SYSTEM을 허용하고 elevated server는 기본적으로 낮은 integrity client의 읽기·쓰기를 막는다. 명시적 opt-in이 ordinary same-account 접속을 허용한다.

- 구현: [IPC bind 시 descriptor 적용](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/ipc.rs#L99-L125>), [account SID DACL + integrity label](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/platform/windows.rs#L181-L268>).
- 정적 테스트: [두 endpoint와 process-local consent 설정 조합](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/windows_server_security.rs#L113-L161>), [낮은 integrity 거절/명시적 sharing 허용 assertion](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/tests/windows_server_security.rs#L235-L254>).
- TalkAbout 비공개 방은 별도로 membership, worker binding, 승인 capability, viewer별 결과 필터가 필요하다. socket을 안다는 사실이나 env의 pane ID를 방 권한으로 인정하지 않는다. DPAPI는 credential at-rest 보호이고 이 IPC/room 권한 문제와 별개다. 기존 R4 [credential_vault.py](<../../talkabout/credential_vault.py#L1-L6>) 재구현 제안이 아니다.

### integrations는 ‘그대로 실행’ 외에도 실제 전역 설정 변경을 한다

Claude/Codex 등의 integration 설치는 hook 파일과 settings/config를 다룬다. config 경로는 여러 CLI별 env override 또는 사용자 home에서 파생한다. pane은 API socket과 binary 경로를 env로 받는다. 따라서 README의 ‘터미널 소유, agent 비대체’는 타당하지만 **integration을 켜도 외부 설정 영향이 없다**는 의미는 아니다.

- 구현: [Claude/Codex 설치 결과의 hook/settings/config](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/integration/actions.rs#L114-L139>), [pane API env와 CLI별 config 경로](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/integration/env.rs#L8-L65>).
- 정적 테스트: [따옴표·공백·Unicode가 든 Windows hook 경로의 encoded invocation 검증](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/integration/tests.rs#L18-L40>). 이것은 quoting 계약 증거이며 실제 설치/공급자 호환성 검증은 아니다.
- TalkAbout: 기존 native protocol 우선. 추가 hook은 사용자 opt-in, room 전용 config home, 변경 diff/rollback과 버전/플랫폼 capability 표시를 요구한다. untrusted report의 resume argv를 shell에 입력하는 모델보다 검증된 실행 계약을 유지한다. Herdr도 [resume argv의 개수/크기/제어문자/실행명 검증](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/agent_resume.rs#L56-L89>)을 별도로 한다.

### 플러그인은 신뢰된 호스트 확장이며 private room 실행 격리가 아니다

플러그인 command에는 API socket과 room 유사 workspace/pane context가 env로 주입되고, 설치 root에서 외부 program을 spawn한다. 32개 동시 실행 상한, 출력 64KiB, 로그 200개 제한은 자원/관측 관리이지 권한 격리가 아니다. worker가 App teardown 이후에도 생존할 수 있다는 주석도 있다.

- 구현: [상한과 API/context env](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/app/api/plugins/runtime.rs#L11-L82>), [worker lifetime 및 host process spawn](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/src/app/api/plugins/runtime.rs#L118-L158>).
- 피할 것: marketplace/plugin manifest를 room agent가 보냈다는 이유만으로 실행, full API endpoint/env를 agent에 일괄 제공, plugin stdout를 승인 명령으로 재해석. TalkAbout은 기존 tool broker의 최소권한 경계를 유지해야 한다.

## 3. README 주장 대 실제 구현

| 주장 | 소스 기반 판정 |
|---|---|
| detach해도 작업 유지 | F1과 Unix 테스트로 뒷받침된다. 서버/호스트 장애 생존과는 다르다. |
| 재시작 후 layout 복원·지원 agent resume | README 자체도 원래 process는 생존하지 않는다고 명시한다. F2와 일치한다. 지원 CLI/session ref/정책에 의존한다. |
| working/blocked/idle 표시, genuinely blocked wait | F4의 활동 gate·안정화가 실재한다. CLI 화면/보고의 의미 정확성이나 작업 성공 증명은 별도다. |
| agent들이 CLI/socket으로 spawn·prompt·wait | prompt/wait가 실제 서버 API에 존재한다. 이것만으로 private-room ACL, shared coding 충돌 조정, 결과 검증이 제공되는 것은 아니다. |
| split/drag와 여러 client | 실제 layout·client-local projection 구현과 정적 테스트가 있다. 웹 협업 UX 자체는 제공하지 않는 TUI다. |
| agents를 대체하지 않고 terminals를 소유 | 맞다. 하지만 integration opt-in은 사용자 CLI 설정을 수정할 수 있고 plugin은 host command를 실행한다. |
| one Rust binary, no Electron | Rust package와 terminal UI 의존성은 확인했다. [Cargo 설정](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/Cargo.toml#L23-L58>)에 native/vendor 의존성이 있어 TalkAbout에 경량 라이브러리처럼 삽입할 대상으로 보지는 않는다. |

주장 원문: [README 기능 설명](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/README.md#L29-L38>). SSH/여러 machine의 운영 신뢰성·성능은 이번 읽은 근거만으로 인증하지 않는다.

## 4. 라이선스 — badge가 아니라 실제 파일 기준

- root 실제 [LICENSE](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/LICENSE#L1-L5>)는 Apache License 2.0. [재배포 조건](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/LICENSE#L89-L128>)에는 라이선스 사본, 변경 고지, 관련 attribution 유지, NOTICE가 있는 경우 해당 고지 전달 의무가 있다. [상표권 비허여·무보증](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/LICENSE#L138-L151>)도 구분한다.
- subpackage [ghostty-vt manifest](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/crates/ghostty-vt/Cargo.toml#L1-L8>)의 binding은 Apache-2.0 표기다. 그러나 실제 vendored [libghostty-vt LICENSE](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/vendor/libghostty-vt/LICENSE#L1-L21>)와 [portable-pty LICENSE](<https://github.com/herdrdev/herdr/blob/2713feb56e9c9db33992db0a262c68f7426d46dd/vendor/portable-pty/LICENSE.md#L1-L21>)는 각각 MIT이며 저작권/허가 고지 보존 조건이 있다.
- root Apache 표기 하나로 vendor와 전이 의존성 전체의 라이선스를 덮지 않는다. 위 실제 파일과 manifest만 확인했으며 모든 asset, vendor 내부 하위 구성요소, 배포 binary SBOM의 법률 실사를 완료했다는 의미가 아니다. 이번 권고는 **설계 패턴의 독립 구현**이며 코드 복사는 하지 않았다.

## 5. TalkAbout 구체 적용 순위

아래 이름은 기존 파일을 확장할 제안이지 현재 구현되어 있다고 주장하는 새 기능명이 아니다.

| 순위 | 채택 패턴 | TalkAbout 적용 지점 | 추가할 정적/향후 테스트 계약 |
|---|---|---|---|
| P0 | UI detach ≠ 작업 cancel | [http_runtime.py](<../../talkabout/http_runtime.py>), [worker.py](<../../talkabout/worker.py>), [runtime.py](<../../talkabout/runtime.py>): observer와 worker/turn ownership 상태 분리 | browser 끊김은 turn 중복 시작을 유발하지 않음; ACL 회수는 새 관찰을 차단; owner lease 상실의 기존 정책 유지 |
| P0 | ACK/활동/terminal proof 분리 | [agent_runtime.py](<../../talkabout/agent_runtime.py>), [trial_observation.py](<../../talkabout/trial_observation.py>): 기존 R3 start grant·uncertain retirement와 별도 프로세스 cleanup 확인을 구분하고 관측 phase만 추가 | idle-before-send를 성공으로 처리하지 않음; 잘못된 turn ID 이벤트 무시; timeout 후 retired instance 재사용 금지 |
| P1 | durable room projection와 live runtime 분리 | [store.py](<../../talkabout/store.py>), [collaboration.py](<../../talkabout/collaboration.py>), [codex_sessions.py](<../../talkabout/codex_sessions.py>): task/result/session ref/view를 다른 종류로 복구 | crash 뒤 완료 결과 재조회 가능; uncertain turn 자동 재실행 없음; viewer별 source redaction 유지 |
| P1 | split와 runtime identity 분리 | [웹 화면](<../../talkabout/web/index.html>): 대화/agent 상태/증거·diff의 비교 패널, client별 focus/scroll | panel 이동/접기로 provider 시작 안 됨; spawn 실패 시 layout rollback; agent 교체 시 이전 결과 identity 유지 |
| P1 | cursor gap→snapshot resync | [api.py](<../../talkabout/api.py>), [runtime.py](<../../talkabout/runtime.py>), [retention.py](<../../talkabout/retention.py>): bounded 전달과 durable 결과의 역할 분리 | ring overflow·restart generation 변경을 명시; snapshot도 현재 ACL 적용 |
| P2 | integration capability/version/opt-in | [providers.py](<../../talkabout/providers.py>), [connector.py](<../../talkabout/connector.py>), [tool_broker.py](<../../talkabout/tool_broker.py>): hook 지원 조건과 승인 scope 표시 | config 변경은 명시 opt-in; room 밖 config 미변경; full socket capability 유출 금지 |

**도입하지 않을 것 / 보류 순위**

1. **피함:** CLI terminal을 private room 보안 경계로 사용하거나 임의 plugin에 full API 접근 부여. hook 상태를 권한 승인·성공 증명으로 사용.
2. **피함:** R3 one-shot을 persistent PTY로 교체, uncertain instance의 자동 복구/재시도, R4 DPAPI 저장소 재개발.
3. **보류:** Unix FD live-handoff와 SSH multi-machine 복제. 먼저 browser 재접속·durable result replay·ACL-filtered snapshot으로 사용자 문제를 푼다.
4. **피함:** Herdr shutdown의 warning-only 최종 경로를 가져와 TalkAbout의 미확인 정리 실패를 숨기기. 기존 ProcessCleanupError/retirement 계약을 보존한다.
5. **피함:** TUI BSP/native VT stack 자체의 포팅. 웹 UI에는 identity/focus/rollback 불변식만 독립 적용한다.

## 최종 한계

위 테스트들은 **소스에 존재하는 assertion의 증거**이며 통과 결과가 아니다. 실제 CLI 버전별 탐지, Windows runner 권한, 강제 전원 차단 내구성, SSH reconnect, 동시 작업 성능은 실행 검증하지 않았다. Herdr가 에이전트끼리 코딩할 환경을 제공하는 것과 TalkAbout의 비공개 방에서 누가 무엇을 보고 실행할 수 있는지를 보장하는 것은 다른 문제다. 채택의 중심은 ‘새 runtime 만들기’가 아니라 **기존 R3/R4 위에 관찰·복구·분할 화면의 정확한 계약을 얹는 것**이다.
