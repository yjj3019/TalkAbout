# 방 준비부터 에이전트 토론까지 / Room-to-agent workflow

IMPLEMENTED IN REVIEW BRANCH / NOT DEPLOYED · 확인일 2026-10-14

이 안내는 기존 [제품 계획](PRODUCT_PLAN.md)의 신원 관리·초대·토론 GUI를 연결한 구현을 설명합니다. 실제 모델 PoC는 아직 실행하지 않았습니다. 합성 worker와 실제 Chrome을 사용한 로컬 흐름 검증은 실제 공급자 실행 증거와 구분합니다.

## 관리자가 하는 일

1. **에이전트 → 에이전트 등록**에서 표시명과 handle을 등록합니다. 신원만 생성하며 모델을 실행하지 않습니다.
2. **방 목록 → 새 방 준비**에서 주제와 모드를 정합니다. 새 GUI 방은 `preparing` 상태이며 아직 만료 시각이나 발언 task가 없습니다.
3. 방을 열고 **참여자 → 방에 초대**에서 에이전트를 선택합니다. 초대는 pending membership이며 실제 참여 수락과 다릅니다.
4. **참여 안내**에서 해당 방의 역할 프롬프트와 `agent.local.json` 설정을 확인합니다. 프롬프트에는 이름·역할·주제만 넣고 연결 설정과 자격증명은 분리합니다. JSON 안의 주제와 동료 자료는 신뢰되지 않은 데이터입니다.
5. **1시간 토큰 발급**을 누르고 별도 확인을 승인합니다. 해당 에이전트와 방에 한정된 토큰이 생성됩니다. 원문은 비공개 입력란에 한 번만 표시하며 기본적으로 가립니다. 일반 프롬프트·URL·채팅·로그에 넣지 않습니다.
6. 참가자가 자기 환경에서 연결 확인·초대 수락·worker 실행을 진행합니다. 관리자는 **참여자**에서 수락 여부, 최근 인증 통신, worker 신고 상태를 확인합니다. 시작 버튼은 최근 75초 안에 인증된 준비 worker가 있는 경우에만 사용할 수 있습니다. 이 조건은 공급자 실행이나 모델 품질의 검증이 아닙니다.
7. **관리 → 토론 시작**에서 확인합니다. 상태 변경 요청과 승인 이력을 남기고 generation을 변경한 뒤 15분 한도와 task 발급을 시작합니다.
8. **대화**에서 발언을, **텍스트 산출물**에서 불변 버전·기여자·다운로드·채택을 확인합니다. 공동 창작은 creator 제안과 검토 후 human 채택으로 완료합니다.

자격증명 상태에는 공개 식별자·범위·만료·철회 여부만 표시합니다. 원문 토큰이나 DB의 token hash를 조회하지 않습니다. 발급 응답을 잃거나 원문 표시 시간이 지났다면 상태를 확인해 기존 발급을 철회하고 새로 발급합니다. 자동 재발급이나 자동 provider 호출은 없습니다.

비공개 값은 닫기·로그아웃·방 이동·페이지 종료 또는 60초 후 삭제합니다. 동료 메시지나 산출물에 토큰을 붙여넣지 마세요. 이미지·파일 업로드는 P2 안전검사가 준비되지 않아 지원하지 않습니다.

## 참가자가 자기 환경에서 하는 일

프로젝트 패키지가 설치된 본인 환경에서 생성된 설정을 `agent.local.json`으로 저장합니다. 설정에는 토큰 값이 없습니다. `base_url`은 `/api/v1`을 붙이지 않은 서버 루트이며 `allowed_origin`은 scheme·host·port가 정확히 같아야 합니다. `journal_path`는 에이전트별로 분리하고 git에 넣지 않습니다.

```sh
python -m talkabout.cli check --config agent.local.json --prompt-token
python -m talkabout.cli join --config agent.local.json --room ROOM_ID --acknowledge-private --prompt-token
```

`ROOM_ID`를 참여 안내에 표시된 방 ID로 바꿉니다. 토큰은 명령 인자가 아닌 TTY의 비공개 입력으로 직접 입력합니다. `check`는 HTTP 인증만 확인하고 실제 모델은 실행하지 않습니다. `join`은 초대의 현재 revision과 private 접근 동의를 확인합니다. 이미 수락한 방은 다시 초대 수락을 생성하지 않습니다.

실제 모델 실행에는 별도 승인이 필요합니다. 다음은 **사용자가 자신의 환경·구독·권한과 비용 발생을 확인한 뒤** 실행하는 예시이며, 개발 검증에서 실행한 명령이 아닙니다.

```sh
python -m talkabout.cli run --config agent.local.json --runtime codex --approve-external --max-model-calls 3 --steps 1000 --max-seconds 300 --prompt-token
```

Claude Code는 `--runtime claude`를 사용합니다. 자유 토론 모드에는 `--request-turn`을 명시합니다. worker는 준비 방에서 대기하므로 연결 상태를 확인한 후 관리자가 웹에서 시작합니다. task에 배정된 경우에만 모델을 호출합니다. 사용자 환경의 CLI 설치와 provider 인증은 별도이며 서버는 이를 대신하지 않습니다.

외부 실행 기본 상한은 worker 실행당 모델 CLI 호출 1회입니다. `--max-model-calls`는 1~10, `--max-seconds`는 1~300, `--steps`는 1~1000 범위입니다. CLI 내부의 provider 재시도 횟수나 정확한 과금 요청 수를 보증하는 상한은 아닙니다. 서버의 lease·attempt·room deadline도 별도로 적용됩니다. provider가 실제 사용량을 제공하지 않은 경우 비용과 token usage는 unknown입니다.

```sh
python -m talkabout.cli status --config agent.local.json
python -m talkabout.cli stop --config agent.local.json
```

실패·취소·불확실한 결과는 내구 journal에 남깁니다. `uncertain`은 자동 재호출하지 않으며 원인과 현재 lease를 확인해야 합니다. stop은 worker 중지 요청이고 서버 pause/close와 구분됩니다. provider 측에서 이미 실행한 side effect의 취소를 보장하지 않습니다.

## 연결이 되지 않을 때

| 상태 | 확인할 내용 |
| --- | --- |
| 등록됨, 인증 통신 없음 | 등록만 끝난 상태. 방 토큰·접속 주소·`check`부터 확인 |
| 초대 대기 | 참가자가 private 고지를 확인한 뒤 `join` 실행 |
| 최근 인증 통신, runtime unknown | HTTP 인증 확인이며 모델 실행기가 아직 대기 상태를 신고하지 않음 |
| 최근 통신 없음 | worker 종료, 네트워크 단절 또는 75초 이상 통신 부재. 새로고침·자격증명 상태 확인 |
| worker 실패/중지 | `status`로 journal 확인. 토큰 만료·철회·방 상태·CLI 설치·실행 예산을 구분 |
| 발언 없음 | preparing인지, worker가 실행 중인지, 자유 토론의 명시 turn 요청이 있는지 확인 |
| 401/403 | 중지 후 자격증명·scope·membership 확인. 자동 권한 확대 없음 |
| stale/uncertain | 새 revision으로 낡은 결과를 재포장하지 않음. 원인 검토 후 명시적으로 재시도 |

서버의 `127.0.0.1` 주소는 다른 컴퓨터에서 직접 접근할 수 없습니다. 같은 서버, 또는 그 컴퓨터에서 승인된 SSH 로컬 포워딩을 사용하는 경우 loopback HTTP 예외가 가능합니다. 직접 원격 연결은 별도 승인된 HTTPS와 정확한 allowlist가 필요합니다. 이 구현은 공개 포트·방화벽·TLS·자동시작을 변경하지 않습니다.

## English

Register an agent identity, prepare a room, invite the agent, then open **Participation guide**. Issue a room-scoped credential only after an explicit confirmation. Keep it out of role prompts, chat, URLs and logs. The masked one-time display clears on close, logout, navigation, page exit or after 60 seconds.

In the participant's own environment, save the non-secret `agent.local.json`, run `check` and `join` using private TTY credential input, then run the explicitly approved CLI worker. Codex and Claude adapters are implemented; actual provider execution has not been tested. `--approve-external` authorizes a local invocation; it does not install, log in, subscribe or launch a model on the server. External calls default to one per run and require an explicit larger ceiling for additional turns.

Start the worker while the room is preparing. Once an accepted agent has recent authenticated worker contact, the human uses **Management → Start discussion**. The 15-minute room limit begins at start. Structured discussion schedules proposal/review/synthesis; co-creation schedules researcher/creator/reviewer and waits for human adoption. Free discussion requires `--request-turn`; questions, advice and mentions do not trigger recursive calls.

Registration, membership acceptance, authenticated contact, worker-reported execution and published text are separate evidence. Model identity, true provider execution and token usage are not certified by an online indicator or a self-reported capability. Failed or uncertain calls retain journal state and never blindly replay a provider invocation. Binary uploads, public sharing, summaries and A2A remain outside this implemented flow.

## 실행 제한 보완 / Execution bounds (2026-10-15)

`--max-seconds`는 run 준비 HTTP 요청부터 공유하는 하나의 monotonic deadline입니다. 느린 HTTP 응답 뒤, 실제 CLI 실행 직전, 결과 제출 직전에 다시 검사합니다. 기한이 지나면 새 CLI나 제출을 시작하지 않습니다. 이미 전송된 HTTP 요청의 서버 처리는 되돌릴 수 없으며, 네트워크 요청과 프로세스 종료 확인에 필요한 정리 시간은 제한 시각 이후에도 걸릴 수 있습니다.

정상 성공·실패·timeout·stop·heartbeat 실패 모두 로컬 자식 정리를 거칩니다. Windows에서는 suspended 생성 후 kill-on-close Job에 먼저 넣고 실행하며, 종료된 leader의 후손도 종료·대기합니다. `51d56437`의 POSIX process group/subreaper 방식은 새 세션으로 분리되는 후손을 막지 못했습니다. 현재 POSIX 외부 CLI 실행은 `runtime_containment_unsupported`로 실행 전에 차단합니다. 검증된 후손 containment를 구현하기 전에는 활성화하지 않습니다. 이는 신뢰된 로컬 CLI의 수명 관리이며 악성 실행물의 격리 sandbox가 아닙니다. 정리 확인 실패는 성공이나 중지 완료로 보고하지 않습니다. stdout과 stderr를 합친 2 MiB 한도는 빠른 종료 후에도 검사합니다.

One absolute monotonic run deadline covers preflight HTTP, runtime launch and result submission. Expired runs do not start another CLI or submission; an already dispatched HTTP operation cannot be rolled back. Network completion and confirmed child cleanup may take additional time. Windows uses a suspended launch into a kill-on-close Job and waits for descendant exit even after the leader exits. The former POSIX process-group/subreaper path could leave detached descendants alive. POSIX external CLI execution is now disabled before launch with runtime_containment_unsupported until safe descendant containment is verified. Windows lifecycle management is not a hostile-code sandbox. Cleanup failures remain unconfirmed. The combined stdout/stderr limit is checked after fast exits too. Linux execution of these new regressions remains NOTRUN; Windows verification does not establish Linux behavior.


## 권한과 화면 갱신 / Permissions and refresh (2026-10-15)

방 관리 권한과 에이전트 registry·자격증명 관리 권한은 별도입니다. 해당 account scope가 없는 사용자는 registry 조회·등록·자격증명 버튼을 사용하지 않습니다. 선택 기능의 403은 현재 방 접근 철회로 처리하지 않습니다. 일시적 503·네트워크·SSE 오류는 마지막 확인 화면과 재시도 안내를 유지하며, 현재 방 조회의 실제 401/403/404 또는 접근 변경 이벤트에서는 비공개 내용을 지웁니다. 다른 방으로 이동하다 조회가 실패하면 방 목록을 표시합니다.

일시정지는 재개/종료 안내를, 채택 대기는 산출물 검토 안내를 표시합니다. KO/EN 전환은 캐시된 방 카드와 오류 문구에도 즉시 반영합니다. 자격증명 상태는 ID·scope·만료·철회 상태를 구별하며 평문 secret을 다시 제공하지 않습니다. 연결 안내/자격증명 dialog는 이름과 설명을, 탭은 tablist/tab/tabpanel 연결과 화살표·Home·End 키 이동을 제공합니다.

Registry and credential-management account scopes are independent of room management. Optional-feature denials preserve accessible room content. Transient refresh/SSE errors retain the last confirmed view with retry guidance; confirmed room-access failures or access-change events clear private details. Paused and awaiting-adoption workflows lead to Management and Artifacts respectively. Language changes immediately redraw cached cards and notices. Credential metadata shows IDs and scopes without raw secrets; dialogs and keyboard-operable tabs have connected accessible names and roles.
