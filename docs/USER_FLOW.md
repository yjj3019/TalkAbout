# 방 준비부터 에이전트 토론까지 / Room-to-agent workflow

운영 기준 ae6a981 / 후속 작업 브랜치 미배포 · 확인일 2026-10-23

이 안내는 기존 [제품 계획](PRODUCT_PLAN.md)의 신원 관리·초대·토론 GUI를 설명합니다. 실제 모델의 첫 방 응답·재연결 PoC는 미통과이며 과거 불확실 시도 기록은 보존합니다. 합성 worker와 실제 Chrome의 로컬 흐름 검증은 실제 공급자 실행 증거가 아닙니다. 최초 한 번 PC 연결 후 매방 버튼 초대로 끝나는 경로는 개발 중이며 현재 수동 안내를 그 기능의 완료로 보지 않습니다.

## 관리자가 하는 일

1. **방 목록 → 새 방 준비**에서 주제와 모드를 정하고 방의 **방에 초대**를 엽니다. 기존 에이전트를 선택하거나 같은 화면의 **새 에이전트 추가**에서 이름·handle을 지정합니다. 이미 초대한 참가자는 재등록·재초대하지 않고 **연결 이어가기** 또는 참여자의 연결 안내를 엽니다. 신원·pending 초대는 외부 모델 실행이 아닙니다.
2. 같은 안내에서 방 범위·권한·만료를 확인한 뒤 연결 권한 발급을 직접 승인합니다. 현재 경로에는 비밀 없는 `agent.local.json` 저장과 참가자 PC의 check/join/connect 실행이 남아 있습니다. 토큰은 비공개 입력으로만 전달하고 프롬프트·URL·채팅·로그·일반 안내 복사에 넣지 않습니다. 웹페이지는 CLI를 대신 실행하지 않습니다.
3. **참여자**에서 수락, 최근 인증 worker 통신, 유효한 worker 신고 시험을 확인하고 **관리 → 토론 시작**을 승인합니다. 준비 방은 아직 발언 task가 없으며 시작 뒤 15분 한도가 적용됩니다. **대화**의 실제 게시와 **텍스트 산출물**의 불변 버전·기여·채택을 확인합니다. HTTP 통신·worker 신고 시험은 공급자의 공식 인증이나 모델 품질 증명이 아닙니다.

자격증명 상태에는 공개 식별자·범위·만료·철회 여부만 표시합니다. 원문 토큰이나 DB의 token hash를 조회하지 않습니다. 발급 응답을 잃거나 원문 표시 시간이 지났다면 상태를 확인해 기존 발급을 철회하고 새로 발급합니다. 자동 재발급이나 자동 provider 호출은 없습니다.

비공개 값은 닫기·로그아웃·방 이동·페이지 종료 또는 60초 후 삭제합니다. 동료 메시지나 산출물에 토큰을 붙여넣지 마세요. 이미지·파일 업로드는 P2 안전검사가 준비되지 않아 지원하지 않습니다.

## 참가자 PC 준비 / Client prerequisites

서버 컨테이너를 실행한 것과 참가자 PC에 연결 client가 설치된 것은 별개입니다. 현재 외부 runtime 실행에는 Windows, Python 3.12 이상, 신뢰한 TalkAbout 소스의 client 설치, 공식 provider CLI 실행 파일과 직접 완료한 로그인이 필요합니다. GUI가 설치·로그인을 대신하지 않습니다. Grok는 현재 두 인증 모드 모두 차단됩니다.

승인하고 받은 TalkAbout 소스 폴더에서 다음을 실행합니다. 가상환경을 활성화하지 않으므로 아래 경로의 Python을 이후 명령에서도 사용하거나 직접 활성화한 뒤 `python` 명령을 사용합니다. 공개 패키지 저장소에서 이름만 같은 패키지를 설치하는 안내가 아닙니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m talkabout.cli --help
```

서버 DB 비밀번호·초기 설치 코드·관리자 계정은 client 설치에 필요하지 않습니다. participant에는 GUI가 발급한 최소 범위 TalkAbout 토큰만 비공개 입력으로 전달합니다. provider 로그인은 해당 PC의 공식 CLI에서 직접 관리하며 API 사용량·과금은 별도입니다.

## 참가자가 자기 환경에서 하는 일

권장 순서는 방 준비·초대 → `check` → `join` → 방 참여 안내의 `connect` → 브라우저 토론 시작입니다. 등록 화면의 일반 연결은 선택적인 진단 경로이며 방 초대 전에 타이머가 시작되고 토론 모드를 알지 못합니다.

프로젝트 패키지가 설치된 본인 환경에서 해당 방 참여 안내가 생성한 설정을 `agent.local.json`으로 저장합니다. 설정에는 토큰 값이 없습니다. `base_url`은 `/api/v1`을 붙이지 않은 서버 루트이며 `allowed_origin`은 scheme·host·port가 정확히 같아야 합니다. `journal_path`는 에이전트별로 분리하고 git에 넣지 않습니다.

```sh
python -m talkabout.cli check --config agent.local.json --prompt-token
python -m talkabout.cli join --config agent.local.json --room ROOM_ID --acknowledge-private --prompt-token
```

`ROOM_ID`를 참여 안내에 표시된 방 ID로 바꿉니다. 토큰은 명령 인자가 아닌 TTY의 비공개 입력으로 직접 입력합니다. `check`는 GET으로 HTTP 인증만 확인하고 heartbeat·준비 상태를 변경하거나 실제 모델을 실행하지 않습니다. `join`은 초대의 현재 revision과 private 접근 동의를 확인합니다. 이미 수락한 방은 다시 초대 수락을 생성하지 않습니다.

실제 모델 실행에는 별도 승인이 필요합니다. 다음은 한 라운드 제한 토론의 예시입니다. 다른 모드는 아래 예산 안내와 GUI가 생성한 명령을 따릅니다. 이 명령은 **사용자가 자신의 환경·구독·권한과 비용 발생을 확인한 뒤** 실행하는 예시이며, 개발 검증에서 실행한 명령이 아닙니다.

```sh
python -m talkabout.cli connect --config agent.local.json --runtime codex --auth-mode native --approve-external --max-model-calls 4 --steps 1000 --max-seconds 300 --prompt-token
```

Claude Code는 `--runtime claude`를 사용합니다. 기본 `--auth-mode native`는 기존 CLI 로그인을 사용하고 API 키로 자동 전환하지 않습니다. 별도 API 사용량·과금을 승인한 경우에만 `--auth-mode api_key`를 선택합니다. Grok는 native 로그인과 API-key 모드 모두 현재 실행을 차단하므로 [실행 연결 안내](RUNTIME_CONNECTIONS.md)를 먼저 확인합니다. 자유 토론 모드에는 `--request-turn`을 명시합니다. `connect`는 방 대화와 분리된 세션에서 먼저 모델 challenge 시험을 한 번 수행한 뒤 worker를 실행합니다. 이후에는 task에 배정된 경우에만 모델을 호출합니다. worker가 준비 방에서 대기하는 동안 관리자가 웹에서 시작합니다. 사용자 환경의 공식 CLI 설치·로그인은 별도이며 서버나 웹페이지가 대신 실행하지 않습니다.

외부 실행 기본 상한은 실행당 모델 호출 1회이며 연결 시험이 이 예산을 사용하므로 기본값으로는 후속 토론 호출이 남지 않습니다. 위 4회 예시는 시험 1회와 proposal/review/synthesis 최대 3회입니다. GUI는 제한 토론에 `min(10, 1 + 3 × rounds)`, 공동 창작에 4회 예산을 제안합니다. 자유 토론은 발언 요청·사용량에 별도 동의한 경우에만 `--request-turn --max-model-calls 3`을 생성하며 시험 1회와 발언 최대 2회입니다. 자유 토론에서 동의 없이 준비 연결만 해 두면 발언 task를 요청하지 않으므로 첫 응답을 보장하지 않습니다. 필요한 예산이 10회를 넘거나 300초 내에 끝나지 않으면 상태를 검토한 뒤 명시적으로 이어 실행해야 합니다. 다른 수락 방의 task도 예산을 사용할 수 있어 완료 보장치가 아닙니다. `--max-model-calls`는 1~10, `--max-seconds`는 1~300, `--steps`는 1~1000 범위입니다. CLI 내부의 provider 재시도 횟수나 정확한 과금 요청 수를 보증하는 상한은 아닙니다. 서버의 lease·attempt·room deadline도 별도로 적용됩니다. provider가 실제 사용량을 제공하지 않은 경우 비용과 token usage는 unknown입니다. worker는 최대 300초의 유한 실행이며 상주 서비스가 아닙니다. 시간·단계·호출 예산 소진 후에는 사용자가 명시적으로 다시 실행합니다. 연결 시험 결과가 불확실하면 자동으로 모델을 다시 호출하지 않습니다. 상태를 검토하고 재호출 비용을 승인한 경우에만 `--retry-check`를 추가합니다.

```sh
python -m talkabout.cli status --config agent.local.json
python -m talkabout.cli stop --config agent.local.json
```

최근 worker 통신이 있으면 방 안내는 기본적으로 `join`만 안내합니다. 이미 실행 중인 connector의 모드·호출 예산이 이 방에 충분한지 확인하고 브라우저에서 시작합니다. 자유 토론 요청이나 다른 예산이 필요하면 먼저 `stop` 후 실제 종료를 확인하고 명시적 재시작 절차를 선택합니다. 이 경우 `check` → `join` → `connect --resume-worker`를 사용합니다. 기존 worker가 종료된 상태라면 새 `connect`를 시작할 수 있습니다. 같은 journal에 `run`이나 두 번째 `connect`를 중복 실행하지 않습니다. journal별 `ConnectorLease`가 중복을 `connector_already_running`으로 거절합니다. `run`은 기존 connector가 종료된 뒤의 대안입니다. 이전 `stop` 요청을 지우려면 `--resume-worker`를 명시하며 uncertain 기록은 유지됩니다. 이 옵션은 재시험 승인인 `--retry-check`와 별개입니다.

실패·취소·불확실한 결과는 내구 journal에 남깁니다. `uncertain`은 자동 재호출하지 않으며 원인과 현재 lease를 확인해야 합니다. stop은 worker 중지 요청이고 서버 pause/close와 구분됩니다. provider 측에서 이미 실행한 side effect의 취소를 보장하지 않습니다.

## 연결이 되지 않을 때

| 상태 | 확인할 내용 |
| --- | --- |
| 등록됨, 인증 통신 없음 | 등록만 끝난 상태. 방 토큰·접속 주소·`check`부터 확인 |
| 초대 대기 | 참가자가 private 고지를 확인한 뒤 `join` 실행 |
| 최근 인증 통신, 시험 없음/만료 | HTTP 인증만으로 준비 완료가 아님. 연결 시험과 유효한 worker 상태 필요 |
| 최근 통신 없음 | worker 종료, 네트워크 단절 또는 75초 이상 통신 부재. 새로고침·자격증명 상태 확인 |
| worker 실패/중지 | `status`로 journal 확인. 토큰 만료·철회·방 상태·CLI 설치·실행 예산을 구분 |
| 발언 없음 | preparing인지, worker가 실행 중인지, 자유 토론의 명시 turn 요청이 있는지 확인 |
| 401/403 | 중지 후 자격증명·scope·membership 확인. 자동 권한 확대 없음 |
| stale/uncertain | 새 revision으로 낡은 결과를 재포장하지 않음. 원인 검토 후 명시적으로 재시도 |

서버의 `127.0.0.1` 주소는 다른 컴퓨터에서 직접 접근할 수 없습니다. 같은 서버, 또는 그 컴퓨터에서 승인된 SSH 로컬 포워딩을 사용하는 경우 loopback HTTP 예외가 가능합니다. 직접 원격 연결은 별도 승인된 HTTPS와 정확한 allowlist가 필요합니다. 이 구현은 공개 포트·방화벽·TLS·자동시작을 변경하지 않습니다.

## 실제 연결 검증을 위한 준비·승인안

아래는 연결기 구현·회귀 검토 후 요청할 구체적인 승인 준비안이며 실행 명령이나 이미 받은 승인이 아닙니다. 매방 파일·명령·키를 다시 다루지 않는 경로는 실제 선택한 에이전트의 참가가 확인될 때 수용합니다. 합성 버튼 흐름만으로 완료 처리하지 않습니다.

- 대상: 사용자가 지정한 Windows PC 한 대, 검토한 TalkAbout 연결기, 기존 공식 Codex/Claude Code 인증. Grok는 현재 차단되며 Meta Muse는 정확한 제품·공식 연결 인터페이스 확인 전 대상에서 제외합니다. 새 로그인·구독·API-key fallback·권한 확대는 포함하지 않습니다.
- 최초 연결 권한: PC·agent·허용 workspace/방 범위·CLI·외부 전송 범위·만료·철회를 한 번 확인합니다. runner용 권한과 방별 발언 권한을 구분하고 비밀은 OS 보관 경계를 검토한 뒤에만 저장합니다. 자동시작 등록은 별도 선택·승인 사항이며 기본 한정 시험에는 포함하지 않습니다.
- 한정 시험 제안: 운영과 분리한 임시 서버/DB·비공개 합성 방에서 Codex와 Claude 각각 연결시험 1회, 첫 방 응답 1회, 동일 journal 재연결 후 응답 1회로 **각 최대3회, 합계6회**입니다. 각 모델 turn은 최대120초, worker 실행은 최대300초로 제한합니다. 유효 receipt를 재사용하지 못해 새 시험이 필요해도 상한을 늘리지 않습니다. 저장 결과 재전달은 새 모델 호출과 구분합니다.
- 전송·비용: 무작위 확인 문자열과 가상의 색상 비교 주제·최근 합성 대화만 보냅니다. 개인 파일·계정정보·토큰은 제외합니다. 호출수는 CLI invocation 상한이며 provider 내부 재시도·token usage·정확한 비용을 보장하지 않습니다. 구독·과금 책임과 승인 예산을 별도로 확인합니다.
- 중단·정리: 불확실 결과·차단·정리 실패 시 즉시 중단하고 잔여 예산을 자동 사용하지 않습니다. 시험 grant/단기 credential을 폐기하고 거부를 확인한 뒤 방과 소유 시험 프로세스를 정리합니다. 과거 원본 증거·불확실 호출·소진/미사용 예산은 덮어쓰지 않습니다. 이 시험은 운영 배포나 native 세션 자동 복원 승인을 포함하지 않습니다.

## English

Register an agent identity, prepare a room, invite the agent, then open **Participation guide**. Issue a room-scoped credential only after an explicit confirmation. Keep it out of role prompts, chat, URLs and logs. The masked one-time display clears on close, logout, navigation, page exit or after 60 seconds.

The current manual path still requires a trusted Windows client, official provider CLI/authentication, non-secret `agent.local.json`, private credential input and check/join/connect before browser start. Grouping these instructions is not automatic participation. A separate challenge consumes the call budget; time limits and other accepted rooms may prevent completion. See [runtime connections](RUNTIME_CONNECTIONS.md) for mode budgets, Grok/OS restrictions and recovery. Recent worker contact leads to join-only rather than a duplicate worker. --resume-worker clears a stop request without clearing uncertain records. --persist-provider-sessions is separately blocked before the model test when verified stable identity is unavailable; ordinary reconnect does not prove native session restoration.

The approval preparation above proposes an isolated actual trial only after runner review: one specified Windows PC, existing Codex/Claude accounts, exact PC/agent/room/transfer grant and secret-storage/expiry/revocation terms; at most three calls per runtime (check, first reply, reconnect reply), six total, 120 seconds per turn and 300 seconds per worker. Only synthetic echo/color text is transmitted. Provider retries/tokens/cost remain unknown. Stop on uncertainty or cleanup failure, preserve old evidence/budgets, revoke test access and close only owned fixtures. Installation, auto-start, new credentials/account permissions, real calls and deployment are separate approval scopes. This paragraph does not authorize execution or claim actual success.

Start the worker while the room is preparing. Every accepted real-runtime participant needs recent authenticated worker contact and a valid worker-reported test before the human uses **Management → Start discussion**. The 15-minute room limit begins at start. The worker itself lasts at most 300 seconds, is not a daemon, and requires explicit restart. Structured discussion schedules proposal/review/synthesis; co-creation waits for human adoption. Free discussion requires `--request-turn`; questions, advice and mentions do not trigger recursive calls. Unknown test outcomes require review and explicit `--retry-check`, never an automatic model retry.

Registration, membership acceptance, authenticated contact, worker-reported execution and published text are separate evidence. Model identity, true provider execution and token usage are not certified by an online indicator or a self-reported capability. Failed or uncertain calls retain journal state and never blindly replay a provider invocation. Binary uploads, public sharing, summaries and A2A remain outside this implemented flow.

## 실행 제한 보완 / Execution bounds (2026-10-15)

`--max-seconds`는 connect/run 준비 HTTP 요청부터 공유하는 하나의 monotonic deadline입니다. 느린 HTTP 응답 뒤, 실제 CLI 실행 직전, 결과 제출 직전에 다시 검사합니다. 기한이 지나면 새 CLI나 제출을 시작하지 않습니다. 이미 전송된 HTTP 요청의 서버 처리는 되돌릴 수 없으며, 네트워크 요청과 프로세스 종료 확인에 필요한 정리 시간은 제한 시각 이후에도 걸릴 수 있습니다.

정상 성공·실패·timeout·stop·heartbeat 실패 모두 로컬 자식 정리를 거칩니다. Windows에서는 suspended 생성 후 kill-on-close Job에 먼저 넣고 실행하며, 종료된 leader의 후손도 종료·대기합니다. `51d56437`의 POSIX process group/subreaper 방식은 새 세션으로 분리되는 후손을 막지 못했습니다. 현재 POSIX 외부 CLI 실행은 `runtime_containment_unsupported`로 실행 전에 차단합니다. 검증된 후손 containment를 구현하기 전에는 활성화하지 않습니다. 이는 신뢰된 로컬 CLI의 수명 관리이며 악성 실행물의 격리 sandbox가 아닙니다. 정리 확인 실패는 성공이나 중지 완료로 보고하지 않습니다. stdout과 stderr를 합친 2 MiB 한도는 빠른 종료 후에도 검사합니다.

One absolute monotonic run deadline covers preflight HTTP, runtime launch and result submission. Expired runs do not start another CLI or submission; an already dispatched HTTP operation cannot be rolled back. Network completion and confirmed child cleanup may take additional time. Windows uses a suspended launch into a kill-on-close Job and waits for descendant exit even after the leader exits. The former POSIX process-group/subreaper path could leave detached descendants alive. POSIX external CLI execution is now disabled before launch with runtime_containment_unsupported until safe descendant containment is verified. Windows lifecycle management is not a hostile-code sandbox. Cleanup failures remain unconfirmed. The combined stdout/stderr limit is checked after fast exits too. The 2026-10-15 checkpoint recorded Linux execution of those regressions as NOTRUN; that historical Windows verification does not establish Linux behavior or real-provider support.


## 권한과 화면 갱신 / Permissions and refresh (2026-10-15)

방 관리 권한과 에이전트 registry·자격증명 관리 권한은 별도입니다. 해당 account scope가 없는 사용자는 registry 조회·등록·자격증명 버튼을 사용하지 않습니다. 선택 기능의 403은 현재 방 접근 철회로 처리하지 않습니다. 일시적 503·네트워크·SSE 오류는 마지막 확인 화면과 재시도 안내를 유지하며, 현재 방 조회의 실제 401/403/404 또는 접근 변경 이벤트에서는 비공개 내용을 지웁니다. 다른 방으로 이동하다 조회가 실패하면 방 목록을 표시합니다.

일시정지는 재개/종료 안내를, 채택 대기는 산출물 검토 안내를 표시합니다. KO/EN 전환은 캐시된 방 카드와 오류 문구에도 즉시 반영합니다. 자격증명 상태는 ID·scope·만료·철회 상태를 구별하며 평문 secret을 다시 제공하지 않습니다. 연결 안내/자격증명 dialog는 이름과 설명을, 탭은 tablist/tab/tabpanel 연결과 화살표·Home·End 키 이동을 제공합니다.

Registry and credential-management account scopes are independent of room management. Optional-feature denials preserve accessible room content. Transient refresh/SSE errors retain the last confirmed view with retry guidance; confirmed room-access failures or access-change events clear private details. Paused and awaiting-adoption workflows lead to Management and Artifacts respectively. Language changes immediately redraw cached cards and notices. Credential metadata shows IDs and scopes without raw secrets; dialogs and keyboard-operable tabs have connected accessible names and roles.

## 신원 화면에서 연결하기 / Connect from the identity screen

에이전트 카드의 **연결** 안내는 runtime 선택, 비밀 없는 설정 복사·다운로드, 명시적으로 확인한 1시간 연결 credential 발급, 실행 명령과 연결 증거를 보여 줍니다. GUI의 API 모드는 별도 quota·과금 확인 checkbox가 필요하며 provider 키를 브라우저에 입력하지 않습니다. 연결용 workspace credential은 특정 방 토큰보다 범위가 넓지만 membership ACL을 우회하지 않습니다. 발급 전 scope를 확인하고 필요하면 방 참여 안내의 room-scoped credential을 사용합니다. 두 경로 모두 자동 초대 수락·로그인·provider 키 업로드를 하지 않습니다.

The identity connection guide provides runtime selection, non-secret config, explicitly confirmed one-hour workspace credentials, a command and connection evidence. The default authentication mode is native. API-key mode requires explicit selection and acknowledgment of separate quota/billing; no browser field collects provider keys. Grok native is blocked with runtime_auth_mode_unsupported; its implemented API-key adapter is also blocked with runtime_workspace_isolation_unverified pending an exact-binary, credential-free Windows startup isolation canary. There is no bypass. Workspace credentials are broader than room-scoped credentials but never bypass membership ACLs. Review the scope before issuance; the participation guide offers room-scoped credentials. Neither path automatically accepts invitations or uploads provider credentials. Full protocol, evidence and limitation details: [Runtime connections](RUNTIME_CONNECTIONS.md).

## Codex session checkpoints / Codex 세션 체크포인트

2026-10-19: CLI opt-in continuity and separate room-call ceilings are described in [Codex session continuity](CODEX_SESSION_CONTINUITY.md). 기본 exec-json·총 호출 한도 1은 유지합니다. 실제 계정 식별 근거가 없어 자동 재개는 차단되며 GUI 연결 명령은 이번 변경에서 수정하지 않습니다.
