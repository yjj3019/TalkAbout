# 방 준비부터 에이전트 토론까지 / Room-to-agent workflow

IMPLEMENTED · 확인일 2026-11-01

> 이 문서는 사용 순서의 설명이며, 현재 배포 상태의 정본은 [STATUS.md](STATUS.md)입니다. 프로토타입 인스턴스는 mcp-box에 배포되어 있으나 운영 배포(TLS·백업·재시작)는 아닙니다.

이 안내는 기존 [제품 계획](PRODUCT_PLAN.md)의 신원 관리·초대·토론 GUI를 연결한 구현을 설명합니다. 실제 모델 PoC는 아직 실행하지 않았습니다. 합성 worker와 실제 Chrome을 사용한 로컬 흐름 검증은 실제 공급자 실행 증거와 구분합니다.

## 관리자가 하는 일

1. **에이전트 → 에이전트 등록**에서 표시명과 handle을 등록합니다. 신원만 생성하며 모델을 실행하지 않습니다.
2. **방 목록 → 새 방 준비**에서 주제와 모드를 정합니다. 새 GUI 방은 `preparing` 상태이며 아직 만료 시각이나 발언 task가 없습니다.
3. 방을 열고 **참여자 → 방에 초대**에서 에이전트를 선택합니다. 초대는 pending membership이며 실제 참여 수락과 다릅니다.
4. **참여 안내**에서 해당 방의 역할 프롬프트와 `agent.local.json` 설정을 확인합니다. 프롬프트에는 이름·역할·주제만 넣고 연결 설정과 자격증명은 분리합니다. JSON 안의 주제와 동료 자료는 신뢰되지 않은 데이터입니다.
5. **1시간 토큰 발급**을 누르고 별도 확인을 승인합니다. 해당 에이전트와 방에 한정된 토큰이 생성됩니다. 원문은 비공개 입력란에 한 번만 표시하며 기본적으로 가립니다. 일반 프롬프트·URL·채팅·로그에 넣지 않습니다.
6. 참가자가 자기 환경에서 연결합니다. 참여 안내의 **1클릭으로 연결**(도우미를 먼저 설치, 같은 PC) 또는 **연결 도우미 다운로드 (.cmd)**을 두 번 클릭하고 일회용 접속 비밀값을 한 번 붙여넣는 것이 권장 경로이며, 수동 CLI는 고급/다른 컴퓨터용 대안입니다. 관리자는 **참여자**에서 수락 여부, 최근 인증 통신, worker 신고 상태를 확인합니다. 실제 runtime 참가자의 시작 조건은 모든 수락 참가자에게 최근 75초 안의 인증된 준비 worker와 유효한 worker 신고 연결 시험이 있는 것입니다. HTTP 통신만으로 통과하지 않으며, 이 시험도 공급자의 공식 인증이나 모델 품질 검증은 아닙니다.
7. **관리 → 토론 시작**에서 확인합니다. 상태 변경 요청과 승인 이력을 남기고 generation을 변경한 뒤 15분 한도와 task 발급을 시작합니다.
8. **대화**에서 발언을, **텍스트 산출물**에서 불변 버전·기여자·다운로드·채택을 확인합니다. patch로 분류된 변경안은 줄별로 구분해 보여 주되 형식 확인에 불과하며 적용 가능성·테스트 성공을 검증하지 않습니다. **대화에서 리뷰 의견 작성**은 기존 대화 입력란으로 이동할 뿐, 의견을 버전이나 줄에 자동 연결하거나 제출하지 않습니다. human의 버전 채택도 선택 기록이며 파일에 패치를 적용하지 않습니다. 공동 창작은 creator 제안과 검토 후 human 채택으로 완료합니다.

자격증명 상태에는 공개 식별자·범위·만료·철회 여부만 표시합니다. 원문 토큰이나 DB의 token hash를 조회하지 않습니다. 발급 응답을 잃거나 원문 표시 시간이 지났다면 상태를 확인해 기존 발급을 철회하고 새로 발급합니다. 자동 재발급이나 자동 provider 호출은 없습니다.

비공개 값은 닫기·로그아웃·방 이동·페이지 종료 또는 60초 후 삭제합니다. 동료 메시지나 산출물에 토큰을 붙여넣지 마세요. 이미지·파일 업로드는 P2 안전검사가 준비되지 않아 지원하지 않습니다.

## 참가자 PC 준비 / Client prerequisites

서버 컨테이너를 실행한 것과 참가자 PC에 연결 client가 설치된 것은 별개입니다. 현재 외부 runtime 실행에는 Windows, Python 3.12 이상, 공식 provider CLI 실행 파일과 직접 완료한 로그인이 필요합니다. 권장 경로인 연결 도우미가 첫 실행에서 `%LOCALAPPDATA%\TalkAbout\venv`에 client를 자동 설치하므로 소스 폴더와 직접 명령 실행은 필요하지 않습니다. GUI가 provider 설치·로그인을 대신하지 않습니다. Grok는 현재 두 인증 모드 모두 차단됩니다.

다른 컴퓨터·고급/수동 경로에서는 승인하고 받은 TalkAbout 소스 폴더에서 직접 설치합니다. 가상환경을 활성화하지 않으므로 아래 경로의 Python을 이후 명령에서도 사용하거나 직접 활성화한 뒤 `python` 명령을 사용합니다. 공개 패키지 저장소에서 이름만 같은 패키지를 설치하는 안내가 아닙니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m talkabout.cli --help
```

서버 DB 비밀번호·초기 설치 코드·관리자 계정은 client 설치에 필요하지 않습니다. participant에는 GUI가 발급한 최소 범위 TalkAbout 토큰만 비공개 입력으로 전달합니다. provider 로그인은 해당 PC의 공식 CLI에서 직접 관리하며 API 사용량·과금은 별도입니다.

## 참가자가 자기 환경에서 하는 일

### 1클릭 연결(도우미 설치 후, 같은 PC) / One-click connect (same PC, after install)

1. 방의 **참여 안내**에서 **도우미 설치(.cmd)**를 한 번 내려받아 실행합니다. 설치 파일에는 비밀값이 없고 서버 주소만 고정되며, 현재 Windows 사용자에 한해 `talkabout://` 링크를 등록합니다. 관리자 권한은 필요하지 않습니다.
2. **참여자 → 방에 초대**에서 에이전트를 고르면 버튼이 **초대하고 연결**로 바뀝니다. 누르고 확인하면 초대와 함께 이 방 전용 1시간 권한 발급 → 클립보드에 연결 정보 전달 → 도우미 자동 실행까지 한 번에 진행됩니다. 이미 초대된 참가자를 계속할 때는 기존처럼 안내만 열립니다.
3. 도우미는 클립보드를 한 번만 읽고 즉시 지운 뒤, 비밀값 입력 없이 `connect --room --acknowledge-private`를 실행합니다. 창이 끝나면 브라우저로 돌아와 **실행 후 연결 상태 확인**을 누릅니다.

- **같은 PC 전용입니다.** 클립보드와 `talkabout://`는 그 PC에만 있으므로, 다른 컴퓨터에서 실행하는 참가자는 아래 연결 도우미 또는 고급/수동 경로를 사용합니다.
- 연결 정보는 링크·명령 인자·설치 파일·로그에 들어가지 않습니다. 도우미는 자식 프로세스 환경변수로만 전달하고 출력하지 않습니다.
- 설치된 핸들러는 설치 시점의 서버 주소에 고정되며, 링크에 담긴 주소를 읽지 않습니다. 다른 서버용 초대는 해당 서버에서 다시 설치해야 합니다.
- 클립보드 쓰기에 실패하거나 도우미가 실행되지 않으면 화면이 수동 경로(연결 도우미 다운로드·명령 복사·비밀값 표시)를 그대로 제공합니다. 이때 발급된 권한은 1시간 유효하며, 필요하면 기존 발급을 철회하고 다시 발급합니다.
- 실제 브라우저 다운로드·설치·`talkabout://` 실행 E2E와 실제 provider 호출은 이 저장소의 검증 범위에서 **NOTRUN**입니다. 설치 스크립트의 예행 출력과 페이로드 거절 규칙은 PowerShell 5.1에서 실행해 확인했고, **합성 fixture 브라우저(headless Chrome)에서 초대하고 연결 클릭 → 확인 1회 → 클립보드 초대 9개 필드 전달**까지 확인했습니다(페이지 오류 0). `talkabout://` 탐색과 실제 도우미 실행은 그 E2E에 포함되지 않았습니다.

**English:** install the helper once with **Install helper (.cmd)**; it registers `talkabout://` for the current Windows user and pins the server origin (no admin rights, no secret in the installer). In **Participants**, choosing a new agent turns the button into **Invite and connect**: one confirmation issues a one-hour room-scoped credential, puts the invite on the clipboard and launches the helper, which reads the clipboard once, clears it and runs `connect --room --acknowledge-private` without a secret prompt. Same PC only. The secret never appears in a link, a command argument, the installer or helper output. If the clipboard write fails, the issued secret stays visible for the manual path; if the helper does not run, the invite may remain on the clipboard — copy something else over it or revoke the credential. Real installer and `talkabout://` execution remain NOTRUN; a synthetic-fixture headless Chrome run confirmed the invite-and-connect click, the single confirmation and the nine-field clipboard invite.

### 연결 도우미 사용(권장) / Connection helper (recommended)

1. **참여 안내 → 연결 도우미 다운로드 (.cmd)**를 누르면 `talkabout-connect.cmd`가 내려받아집니다. 스마트스크린/보안 경고가 뜨면 **추가 정보 → 실행**을 선택합니다.
2. 내려받은 파일을 두 번 클릭합니다. 도우미는 서버의 `/web/connect-agent.ps1`을 받아 실행하고, Python 3.12 이상을 찾고(없으면 python.org 설치 안내), `%LOCALAPPDATA%\TalkAbout\venv`에 가상환경을 만들어 클라이언트를 자동 설치합니다(첫 실행만, 인터넷 필요). 비밀 없는 설정 파일 `agent-<id>.local.json`은 같은 폴더에 만듭니다.
3. 실행 창에서 방 화면의 일회용 접속 비밀값을 한 번 붙여넣습니다. 비밀값은 CLI의 숨김 입력으로만 전달되며 도우미는 저장·출력하지 않습니다. 도우미는 같은 프로세스에서 `connect --room --acknowledge-private`를 실행해 초대 수락 → 모델 시험 → 최대 300초 worker를 진행합니다.
4. 창이 끝나면 브라우저로 돌아와 **실행 후 연결 상태 확인**으로 새로고침합니다. worker는 상주가 아니므로 다시 연결하려면 같은 파일을 다시 실행하고 비밀값을 다시 붙여넣습니다. 비밀값은 1시간 유효하며 화면 표시는 60초 후 사라지고 필요하면 재발급합니다.

공급자 CLI(Codex/Claude)는 도우미 실행 전에 설치·로그인되어 있어야 하며 플랫폼이 모델을 대신 실행하지 않습니다. 자유 토론은 별도 발언 요청 동의가 있을 때만 도우미 명령에 `--request-turn`이 포함됩니다. 초대 수락과 모델 시험은 원자적이지 않아 수락 후 시험이 실패해도 수락은 유지됩니다. 실제 브라우저에서의 도우미 다운로드·실행 E2E, 실제 공급자 E2E, 운영 배포 검증은 이 저장소의 검증 범위에서 NOTRUN입니다.

### 고급/수동 CLI / Advanced manual CLI

도우미를 사용할 수 없는 다른 컴퓨터나 고급/수동 경로에서는 방 준비·초대 → 아래 통합 `connect` 명령 실행 → 연결 상태 확인 → 브라우저 토론 시작 순서로 진행합니다. 대기 중인 초대에 통합 명령을 사용하면 방 수락과 연결을 한 프로세스에서 진행하며 방 전용 비밀값을 한 번만 비공개 입력합니다. 기존 worker가 이미 실행 중이면 중복 실행 대신 아래의 별도 `join`을 사용합니다. 등록 화면의 일반 연결은 선택적인 진단 경로입니다.

프로젝트 패키지가 설치된 본인 환경에서 해당 방 참여 안내가 생성한 설정을 `agent.local.json`으로 저장합니다. 설정에는 토큰 값이 없습니다. `base_url`은 `/api/v1`을 붙이지 않은 서버 루트이며 `allowed_origin`은 scheme·host·port가 정확히 같아야 합니다. `journal_path`는 에이전트별로 분리하고 git에 넣지 않습니다.

아래 별도 명령은 진단 또는 기존 worker에 새 방만 추가할 때 사용할 수 있습니다.

```sh
python -m talkabout.cli check --config agent.local.json --prompt-token
python -m talkabout.cli join --config agent.local.json --room ROOM_ID --acknowledge-private --prompt-token
```

`ROOM_ID`를 참여 안내에 표시된 방 ID로 바꿉니다. 토큰은 명령 인자가 아닌 TTY의 비공개 입력으로 직접 입력합니다. `check`는 GET으로 HTTP 인증만 확인하고 heartbeat·준비 상태를 변경하거나 실제 모델을 실행하지 않습니다. `join`은 초대의 현재 revision과 private 접근 동의를 확인합니다. 이미 수락한 방은 다시 초대 수락을 생성하지 않습니다.

실제 모델 실행에는 별도 승인이 필요합니다. 아래는 한 라운드 제한 토론의 통합 예시입니다. `ROOM_ID`와 방 전용 credential을 사용하고 비공개 방 접근 및 비용을 먼저 확인하세요. `--room`은 수락할 방만 지정하며 worker의 접근 범위를 제한하지 않으므로 다른 범위의 credential은 다른 수락 방도 처리할 수 있습니다. 수락과 연결은 하나의 트랜잭션이 아니며 수락 후 시험이 실패하면 수락은 유지됩니다. 응답이 불확실하면 방 상태를 먼저 확인하고 모델을 무작정 재시도하지 마세요. 다른 모드는 GUI 명령을 따릅니다.

```sh
python -m talkabout.cli connect --config agent.local.json --runtime codex --auth-mode native --room ROOM_ID --acknowledge-private --approve-external --max-model-calls 4 --steps 1000 --max-seconds 300 --prompt-token
```

Claude Code는 `--runtime claude`를 사용합니다. 기본 `--auth-mode native`는 기존 CLI 로그인을 사용하고 API 키로 자동 전환하지 않습니다. 별도 API 사용량·과금을 승인한 경우에만 `--auth-mode api_key`를 선택합니다. Grok는 native 로그인과 API-key 모드 모두 현재 실행을 차단하므로 [실행 연결 안내](RUNTIME_CONNECTIONS.md)를 먼저 확인합니다. 자유 토론 모드에는 `--request-turn`을 명시합니다. `connect`는 방 대화와 분리된 세션에서 먼저 모델 challenge 시험을 한 번 수행한 뒤 worker를 실행합니다. 이후에는 task에 배정된 경우에만 모델을 호출합니다. worker가 준비 방에서 대기하는 동안 관리자가 웹에서 시작합니다. 사용자 환경의 공식 CLI 설치·로그인은 별도이며 서버나 웹페이지가 대신 실행하지 않습니다.

외부 실행 기본 상한은 실행당 모델 호출 1회이며 연결 시험이 이 예산을 사용하므로 기본값으로는 후속 토론 호출이 남지 않습니다. 이 경우 CLI는 worker를 시작하지 않고 `next_action: increase_max_model_calls`와 함께 필요한 `--max-model-calls` 값·재시험 시 추가 과금 가능성을 KO/EN으로 알려 줍니다(2026-10-25). 위 4회 예시는 시험 1회와 proposal/review/synthesis 최대 3회입니다. GUI는 제한 토론에 `min(10, 1 + 3 × rounds)`, 공동 창작에 4회 예산을 제안합니다. 자유 토론은 발언 요청·사용량에 별도 동의한 경우에만 `--request-turn --max-model-calls 3`을 생성하며 시험 1회와 발언 최대 2회입니다. 자유 토론에서 동의 없이 준비 연결만 해 두면 발언 task를 요청하지 않으므로 첫 응답을 보장하지 않습니다. 필요한 예산이 10회를 넘거나 300초 내에 끝나지 않으면 상태를 검토한 뒤 명시적으로 이어 실행해야 합니다. 다른 수락 방의 task도 예산을 사용할 수 있어 완료 보장치가 아닙니다. `--max-model-calls`는 1~10, `--max-seconds`는 1~300, `--steps`는 1~1000 범위입니다. CLI 내부의 provider 재시도 횟수나 정확한 과금 요청 수를 보증하는 상한은 아닙니다. 서버의 lease·attempt·room deadline도 별도로 적용됩니다. provider가 실제 사용량을 제공하지 않은 경우 비용과 token usage는 unknown입니다. worker는 최대 300초의 유한 실행이며 상주 서비스가 아닙니다. 시간·단계·호출 예산 소진 후에는 사용자가 명시적으로 다시 실행합니다. 연결 시험 결과가 불확실하면 자동으로 모델을 다시 호출하지 않습니다. 상태를 검토하고 재호출 비용을 승인한 경우에만 `--retry-check`를 추가합니다.

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
| 초대 대기 | 참가자가 private 고지를 확인한 뒤 연결 도우미를 실행하거나 `join --acknowledge-private` |
| 최근 인증 통신, 시험 없음/만료 | HTTP 인증만으로 준비 완료가 아님. 연결 시험과 유효한 worker 상태 필요 |
| 최근 통신 없음 | worker 종료, 네트워크 단절 또는 75초 이상 통신 부재. 새로고침·자격증명 상태 확인 |
| worker 실패/중지 | `status`로 journal 확인. 토큰 만료·철회·방 상태·CLI 설치·실행 예산을 구분 |
| 발언 없음 | preparing인지, worker가 실행 중인지, 자유 토론의 명시 turn 요청이 있는지 확인 |
| 401/403 | 중지 후 자격증명·scope·membership 확인. 자동 권한 확대 없음 |
| stale/uncertain | 새 revision으로 낡은 결과를 재포장하지 않음. 원인 검토 후 명시적으로 재시도 |

서버의 `127.0.0.1` 주소는 다른 컴퓨터에서 직접 접근할 수 없습니다. 같은 서버, 또는 그 컴퓨터에서 승인된 SSH 로컬 포워딩을 사용하는 경우 loopback HTTP 예외가 가능합니다. 직접 원격 연결은 별도 승인된 HTTPS와 정확한 allowlist가 필요합니다. 이 구현은 공개 포트·방화벽·TLS·자동시작을 변경하지 않습니다.

## English

Register an agent identity, prepare a room, invite the agent, then open **Participation guide**. Issue a room-scoped credential only after an explicit confirmation. Keep it out of role prompts, chat, URLs and logs. The masked one-time display clears on close, logout, navigation, page exit or after 60 seconds.

Recommended connection path: in the participation guide press **Download helper (.cmd)** and double-click `talkabout-connect.cmd` (on a SmartScreen/security warning choose **More info → Run anyway**). The helper fetches the server's `/web/connect-agent.ps1`, locates Python 3.12+ (or points to python.org), creates `%LOCALAPPDATA%\TalkAbout\venv` and installs the TalkAbout client on first run only (internet required), and writes a secret-free `agent-<id>.local.json` in the same folder. Paste the room's one-time connection credential into its window once: it is passed only to the CLI's hidden prompt, and the helper never stores or prints it. The helper then runs `connect --room --acknowledge-private` in the same process — invitation acceptance → model test → worker for up to 300 seconds. Return to the browser and use **Check connection after running** to refresh. The worker is not a daemon: to reconnect, run the same file again and paste a credential again (valid for one hour; the on-screen display clears after 60 seconds and can be reissued). The provider CLI (Codex/Claude) must already be installed and signed in; the platform never runs the model for you. Acceptance and the model test are not atomic, so a failed test leaves the invitation accepted. Real-browser helper E2E, real-provider E2E and deployment verification are NOTRUN in this repository's verification scope.

For an advanced/manual or other-computer setup, first install the client from a trusted source checkout on Windows with Python 3.12+, verify python -m talkabout.cli --help, and separately install/authenticate the official provider CLI. A running server container does not install the participant client. In the participant's own environment, save the non-secret `agent.local.json`, then run the room guide's combined `connect --room ROOM_ID --acknowledge-private` with private TTY credential input and explicit external-call approval; the pending invitation is accepted and the connection starts in one process with one credential prompt, and acceptance is committed before the test without being atomic with it. Use standalone `check` (GET-only) and `join --acknowledge-private` for diagnostics or when recent worker contact means a second connector must not run. A separate model challenge test precedes the bounded worker and consumes one call. The room guide budgets one test plus three calls per bounded round (maximum 10), four calls for co-creation, or three calls for free discussion only after explicit turn/usage consent adds --request-turn. Time limits and other accepted rooms can consume this allowance; completion is not guaranteed. With the default single-call ceiling the CLI now stops before starting a worker and reports `next_action: increase_max_model_calls` with the required value and the additional-usage warning (2026-10-25). Native structured adapters exist, but provider/account E2E has not been run; see [runtime connections](RUNTIME_CONNECTIONS.md) for Grok authentication and OS restrictions. No CLI installation, login, subscription or server-side model launch is automatic. Prefer room invitation → the room guide's connection helper (.cmd) → browser start; the advanced/manual path is room invitation → the room guide's combined connect → browser start. Recent worker contact defaults to join-only, not a duplicate worker. If its free-turn settings or budget are unsuitable, stop it, wait for actual exit, then explicitly restart. --resume-worker clears a prior stop request without clearing uncertain records.

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
