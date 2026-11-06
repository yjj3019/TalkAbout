# Session-Peer (abruption/session-peer) 정밀 분석 — R6

- 분석 대상: `C:\AI-Codding\claude\TalkAbout\.analysis-repos\session-peer`
  (전체 히스토리 클론, 수정 금지, 테스트/설치 미실행)
- 분석 시점 HEAD: `902a5fe1d5d1e612769df16f5a93df0f08aa8dc8`
  (`git describe`: `v1.4.2-1-g902a5fe`, v1.4.2 공개 이후 1커밋, 2026-11-05)
- 총 커밋 305개, 태그 `v0.6.1`~`v1.4.2`,
  `docs/releases/`에 v0.6.1~v1.4.2 공개 기록 21건 보존
- 목적: 다중 업체 AI 에이전트(Claude Code/Codex/Antigravity)가
  **토론 + 공동 코딩 + 버전화된 산출물 공동 생산**하는 작업장
  (TalkAbout 호스트 프로젝트: Python/FastAPI+PostgreSQL, 방 단위 토론,
  `analysis`/`change_proposal`+통합 diff 산출물, 업체별 프로바이더 어댑터,
  실행 예약/영수증, 웹 GUI)에 대한 개선점을 증거 기반으로 도출

> 인용 경로는 저장소 루트 기준. 코드 복사 없이 참조만 함.

---

## §0 요약

| 항목 | 내용 |
| --- | --- |
| 성격 | 단일 파일 Python CLI + 선택적(relay/MCP) 확장으로, Claude Code·Codex·Antigravity **세션을 발견·주소 지정·1:1 메시징**하는 크로스-에이전트 브리지 ("Find and message Claude Code and Codex sessions from one CLI, locally or over SSH.", `README.md:12`) |
| 스택 | Python ≥3.9 무의존 코어(`session_peer.py` 11,771줄 단일 파일, `pyproject.toml:11`), relay extra: `websockets>=17.1,<18` + `cryptography>=50.0.1,<51`(Py3.11+), MCP extra: `mcp>=1.30,<2`(Py3.10+, `pyproject.toml:40-46`), Control: Node 22/24 + React/Vite + BetterAuth 1.7.5(→1.7.7 패치, `control/src/server`) |
| 활동 | 2026-09 공개 시작 후 v1.0.0→v1.4.2까지 ~6주간 20회 이상 릴리스. CI 9개 직업 전체가 릴리스 게이트(`.github/workflows/ci.yml:321-339`), GitHub Immutable Releases + PyPI Trusted Publisher + 불변 자산 7종/어테스테이션 검증(RELEASING.md), 주간 의존성 감사(`SECURITY.md:47-54`) |
| 라이선스 | MIT, 저자 YEONGUK LEE (`pyproject.toml:12-14`), Development Status 4-Beta(`pyproject.toml:17`) |
| 핵심 가치 | ① 업체별 네이티브 수신로(Claude inbox 소켓/파이프, Codex queue CLI/앱서버, Antigravity bridge)에 대한 **fail-closed 래퍼** ② SSH는 원격 설치 없이 동작(소스 스트리밍, `docs/architecture/agent-transports.md:13-16`) ③ "제출 ≠ 소비 ≠ ACK"라는 **보수적 전달 증거 모델**(`sending.py:170`, `adapters.py:76-78`) ④ 릴리스/보안/문서 훈련이 극단적으로 성숙(준비/공개 분리, 해시/타임스탬프/원인 미주장 원칙, `docs/releases/v1.4.2.md:20-24`) |
| 핵심 판정 | **"에이전트 간 신뢰 경계와 전달 증거" 분야는 참고할 최고 수준 레퍼런스** — 그러나 **"공동 작업장"으로는 아님**: 방/토론/브로드캐스트/산출물 저장/버전 관리가 없고, 메시지는 1:1·온라인 전용·재전송 불가·순서 미보장. TalkAbout에는 ① 전달 증거/영수증 계약 ② 권한 경계 규율 ③ 제한 프레임 원칙 ④ 릴리스 게이트를 가져오고, 피해야 할 것은 ⑤ 대역폭 비대칭/최초 입력 부재와 ⅰ) 단일 파일 11,771줄 ⅱ) 4국어 문서 동기화 부담이다. |

### 0.1 분석 방법

| 단계 | 방법 |
| --- | --- |
| 1 | 리포지토리 구조/릴리스 히스토리·태그·HEAD 확인 (`git log`, 파일 목록) |
| 2 | README·SECURITY·RELEASING·docs(paired-devices, relay-auth, mcp, wake, handoff-runtime, antigravity, agent-transports, v1.4.2) 판독 |
| 3 | 코어 세그먼트(`session_peer_core/*`), 릴레이(`session_peer_relay/*`), MCP, CI, 생성 도구 정독 |
| 4 | 발견→주소→전달→수신→세션 주입 경로의 file:line 추적 |
| 5 | 결함/개선점을 증거와 함께 정리, TalkAbout 적용안 매핑 |

---

## §1 아키텍처

### 1.1 소스 구성과 생성 파이프라인

- 정식 소스는 `session_peer_core/`의 함수 세그먼트 33개이며,
  `tools/generate_session_peer.py:16-50`가 고정 순서로 이어붙여
  `session_peer.py`(11,771줄)를 생성한다.
- CI가 `--check`로 스테일 생성물을 거부한다(`ci.yml:71-72`).
- 단일 평면 네임스페이스로 import/패치 계약을 보존하며,
  SSH는 이 생성물을 원격 `python3 -`로 스트리밍한다(`agent-transports.md:8-17`).
- 애드온 3종: `session_peer_relay/`(Direct/릴레이 트랜스포트 + 키/스토어/
  로테이션/리커버리/페어링/설정 26모듈), `session_peer_mcp.py`(stdio MCP
  정책 서버 841줄), `session_peer_shorthand/`(`sp.sh`/`sp.ps1` 경로 출력,
  `session_peer_shorthand/__main__.py:7-12`).
- `cc_peer.py`는 동결된 레거시(0.5.1)이며 `skills/cc-peer/cc_peer.py`에 사본 중복.
- `control/`은 별도 Node 서비스(운영자용 OAuth/기기 관리/공개 상태),
  `deploy/`는 템플릿/실측 배포를 역할별 분리(`deploy/README.md:3-18`),
  `experiments/`는 **정식 지원이 아닌 탐사** 영역(sdist 제외, `pyproject.toml:53`).
- 시험은 `tests/` 아래 core/adapters/codex/relay/mcp_integration/control_integration/
  distribution/docs로 분리, CI 매트릭스는 Ubuntu/macOS × Py3.9/3.13/3.14 +
  Windows 3.13(`ci.yml:46-58`).

### 1.2 데이터 흐름: 발견 → 주소 → 전달 → 수신 → 세션 주입

1. **발견**
   - Claude: `~/.claude/sessions/*.json` 레코드(또는 `CLAUDE_CONFIG_DIR`, `claude.py:9-15`)
     에서 `messagingSocketPath`+PID를 읽고, 소켓 엔드포인트 정체성/프로세스 생존을
     교차 검증한다(`claude.py:95-131`, `:280-313`).
   - Codex: 홈의 `state_5.sqlite`+스레드-작성자 락 검사(`codex.py:1066-1108`),
     홈 다중화/모호성 판정(`codex.py:1122-1166`), macOS 설정 계층 보수적 확인
     (`codex.py:19-46`).
   - Antigravity: TUI 내부 bridge 등록(`/tmp/session-peer-agy-UID`, `antigravity.py:26-34`),
     부모 `agy` 프로세스·UID·presence FD 검증(`antigravity.py:58-89`).
   - 어댑터 계약 v1 + 레지스트리 검증, 미등록 접두사는 리터럴 Claude 이름으로
     하위 호환 처리(`registry.py:32-37`).
2. **주소**
   - `claude:<PID/발견 ID>` 또는 사용자 지정 이름, `codex:<UUID>`, `antigravity:<UUID>`.
   - Reply-To URI(`session-peer:v1:/reply?agent=…&session=…&host=…&transport=…&codexHome=…`,
     `replies.py:322-351`)가 회신 경로, `address`/`preset` 하위명령으로 로컬 수신자
     바인딩+리비전 고정(v1.3.0, `docs/mcp.md:82-96`).
   - "Reply-To가 한 에이전트를 주장하면 등록된 다른 에이전트 접두사를 향할 수 없다"
     (`agent-transports.md:59-60`).
3. **전달**
   - 송신자가 1회 구성: 기본 봉투 `wrap_message()`으로 From/Reply-To 추가
     (`replies.py:286-311`), SSH는 base64로 원격 재실행(`sending.py:24-52`).
   - "봉투는 송신자 측에서 한 번 구성해 기존 base64 표현을 그대로 전송"
     (`agent-transports.md:45-47`).
   - "제출 후 자동 재시도 없음, `outcome_unknown`이면 재전송 금지"가 전 계층 관례
     (`sending.py:160-163`, `adapters.py:76-78`, `handoff.py:1550-1554`).
4. **수신**
   - 로컬 = 같은 프로세스에서 어댑터 호출(`registry.py:46-64`).
   - SSH = 발송된 소스가 원격에서 CLI 파싱 후 로컬 어댑터 호출.
   - 릴레이/Direct = 수신자 앱이 TLS 피어 → 스토어 프린시펄 → 정책(policy.json)
     검사 → 네이티브 워커 위임(`app.py:137-228`).
   - 워커는 프레임 바이트 예산을 이중 검사(`framing.py:55-78`).
5. **세션 주입**
   - Claude: inbox Unix 소켓에 `{"type":"user","message":{"role":"user",
     "content":text}}` JSON 라인 기입(`claude.py:536-597`), Windows는 네임드
     파이프+세션 `.key`의 peerToken(`claude.py:486-533`).
   - Codex: `[exe,"queue","--thread",id,"--message="+text]` 실행(`codex.py:1379-1380`,
     `:1446-1448`), 표준 출력에서 `Queued message <id> …` 파싱(`codex.py:1460-1462`).
   - 선택 wake: `codex app-server` JSON-RPC `thread/resume`→턴 완료 대기
     (`docs/wake.md:23-25`).
   - Antigravity: bridge가 `<home>/bin/agentapi send-message` 실행(`antigravity.py:393-396`).
   - 어떤 경로도 "소비/턴 완료"를 주장하지 않음 — `consumptionConfirmed` 상수 false
     (`antigravity.py:390-392`, `wake.md:41`, `codex.py:1413`).

### 1.3 전송 4종 실현 방식

| 전송 | 실현 | 핵심 제약 |
| --- | --- | --- |
| 로컬 | 어댑터 직접 호출 | 대상 갱신 세대(`targetGeneration`/PID 바인딩) 검사(`claude.py:122-131`); 원자 바인딩은 Claude 전용(`adapters.py:70-74`) |
| SSH | 생성 소스를 원격 `python3 -`에 스트리밍, 인자 base64 | `--ssh-opt` 화이트리스트 13개 접속 옵션(`ssh.py:10-19`), `--host` 문자 검증(`ssh.py:22-29`), 원격 90,000자/로컬 1,000,000자(`common.py:53,58`, `claude.py:464-483`) |
| MCP | stdio MCP 서버가 정책 검사 후 CLI 하위프로세스 실행 | 리스트 기본, `send`/`preflight`/`diagnostics`/`presets` 권한 분리(`docs/mcp.md:44-60,82-96`), 도구 응답 64KiB(`session_peer_mcp.py:18-21`) |
| 릴레이 | Direct=TCP+핀 TLS 1.3(`app.py:110-113`), 릴레이=WSS 블라인드 중계(`relay.py:1`) | 수신자 온라인 전용, pre-auth 슬롯 Direct 16/릴레이 2/인증 8(`app.py:24-26`), 텍스트 32KiB(`framing.py:6`), TLS 2초/요청 45초(`wire.py:251-259`) |

### 1.4 Control(운영 UI)와 릴레이 토폴로지

- 접속 전 어드미션: Control API(`/v1/session`, `relay.py:17`)로 1회용 티켓,
  `attemptId`로 시도 추적(`paired-devices.md:176-185`).
- Control 로그인: GitHub/Google OAuth 전용, bearer=BetterAuth 세션 토큰(24h 절대
  수명, `relay-auth.md:34-52`), 허용 목록 기본값(`relay-auth.md:20-25`).
- 릴레이 미터링: 루프백 메트릭 포트(`relay.py:460-474`), 소스별 레이트 리밋
  (글로벌 10/유저 8/기기 4 접속 등 `relay.py:60-68`).
- 신뢰 규칙: 소켓 IP만 신뢰, 역프록시는 공유 비밀 헤더 전용,
  `X-Forwarded-For`/`CF-Connecting-IP` 미신뢰, 중복 헤더는 추측 금지(`relay.py:22-57`).

### 1.5 CLI 표면 (명령어)

| 명령 | 역할 | 소스 |
| --- | --- | --- |
| `list` | 세션 발견(로컬/SSH/기기) | `cli.py:103` |
| `send` | 1:1 메시지 전송 | `cli.py:140` |
| `doctor` | 발견/회신 경로 진단 | `cli.py:119` |
| `preset` | 라우트 프리셋 관리 | `cli.py:204` |
| `handoff` | 상관 인텐트 관리(prepare/status/wait/prepare-…) | `cli.py:220` |
| `ack` | 비공개 IPC 영수증 제출 | `cli.py:245` |
| `update` | 설치 갱신 | `cli.py:250` |
| `antigravity-bridge` | agy TUI 내부 bridge | `cli.py:273` |
| `device` | 페어링/로테이션/상태/복구 | `cli.py:288` |
| `setup` | 안내형 초기화/페어링 | `cli.py:292` |
| `address` | 로컬 수신자 참조 | `cli.py:323` |
| `session-peer-mcp` | stdio MCP 서버 | `pyproject.toml:46` |

### 1.6 점진 성숙 경로 (v0.6.1 → v1.4.2)

| 시기 | 마일스톤 |
| --- | --- |
| v0.6.1~v0.9.0 | 로컬/SSH 기반, 어댑터/트랜스포트 분리(#47), Antigravity 어댑터, MCP 첫 도입 |
| v1.0.0 계열 | 페어링/릴레이 v1(핀 TLS, 로테이션, Direct), RC4 캠페인(ACK 20/21) |
| v1.1.0~v1.2.x | handoff v1(실험), wake, 바운드 발견/스킬/프런트매터, SSH 구성 신뢰 옵션 |
| v1.3.0~v1.4.2 | 라우트 프리셋/주소, 전달 설명, 제한 진단, MCP 정책 확장, 3.14 CI, 유지보수+문서 훈련 |

---

### 2.6 핵심 상수·한도 요약

| 상수/한도 | 값 | 출처 |
| --- | --- | --- |
| 로컬 메시지 최대 | 1,000,000자 | `common.py:53` |
| SSH 메시지 최대 | 90,000자 | `common.py:58` |
| 프레임화 텍스트 최대 | 32KiB | `framing.py:6` |
| 애플리케이션 프레임 최대 | 64KiB | `framing.py:7` |
| 워커 프레임 최대 | 256KiB | `framing.py:9` |
| 핸드오프 인텐트/저장 | 10,000개/32MiB | `handoff.py:3-5` |
| 인증 요청 응답 시간 | 45초 | `wire.py:251-259` |
| 인증 접속 상한 | 8 | `app.py:26` |
| 액세스 슬롯(Direct/릴레이 pre-auth) | 16/2 | `app.py:24-25` |
| 메시지 인장/티켓 수명 | 10분/1회용 | `store.py:171` |
| 핸드오프 수명 | 24시간 | `handoff.py:836` |

### 2.7 릴리스/품질 게이트 요약

- CI 9개 직업(relay-control, test, docs, package, shell, install-smoke, mcp,
  relay, dependency-audit) 전체 성공이 게이트(`ci.yml:321-339`).
- 생성 단일 파일 스테일 검사(`ci.yml:71-72`), 스킬 자산 해시 고정
  (`RELEASING.md:21`), 퍼블릭 릴리스 전 준비/검증 절차 분리(`RELEASING.md:143-148`).
- 쿼런틴/리커버리/스냅샷 백업 도구(`deploy/ops/abruption-kr/session-peer-backup-snapshot.py`)
  등 운영 재해 절차 문서화.

---

## §2 메시징·신원·E2E 모델

### 2.1 주소/별명

- 세션 식별 = (agent, host, identifier, [codex_home])(`adapters.py:10-14`).
- Codex UUID는 홈 간 중복 가능 → host+home 보존(`agent-transports.md:36-39`).
- 별명/프리셋은 로컬 수신자 바인딩 저장소("저장된 라우트는 발견/타깃 선택/프로브
  를 하지 않는다", `docs/mcp.md:86`).
- Reply-To는 URI를 데이터로 파싱, "어떤 필드도 명령으로 평가하지 않는다"
  (`replies.py:354-355`).
- SSH 복귀 경로는 doctor `--check-return-route`로 별도 검증, 미검증 시 경고
  (`sending.py:174-181`).

### 2.2 키 교환·암호화 계층

- E2E = TLS 1.3(min=max=TLSv1_3, ALPN `session-peer-device-v1`) + 양끝 인증서 핀
  고정(`identity.py:120-133`).
- `check_hostname=False`는 "공개 DNS가 아닌 초대된 기기 인증서가 신뢰"라는 설계
  (`identity.py:124-126`).
- 서버 쪽 client cert는 `CERT_OPTIONAL` — 핀 고정 리스트에 있을 때만 peer로 인정,
  무인증 접속은 페어링 요청 1회로 제한(`identity.py:127`, `app.py:266-268`).
- 키 수명주기: self-signed EC P-256 365일(`identity.py:99-117`), 로테이션
  3단계(prepare/commit/status, 제한 중 probe/status만 허용 `app.py:160-163`),
  폐기/리커버리 툼스톤(`store.py:121-150`), 복원 DB는 쿼런틴 필수
  (`handoff-runtime.md:83`).
- 전방 비밀성: TLS 1.3 ECDHE로 세션 키 FS 확보, 장기 키는 서명/인증용.
- 리플레이: 릴레이 티켓 1회용(`paired-devices.md:141-147`), antigravity는 같은
  세대 내 requestId 중복 억제·변경 본문 거부(`antigravity.py:383-388`),
  handoff는 토큰 해시+에폭+boot 클록 증명(`handoff.py:56-59,436-461`).
- 명시 한계: 중계자에게 타임스탬프·프레임 크기 노출 가능, 잘못된 홉은
  지연/리플레이/DoS 가능, 평문·개인키는 불가("Endpoint/private-key or trusted-
  confirmation-channel compromise is outside this protection", `paired-devices.md:126`).

### 2.3 신뢰 경계

- 수신 에이전트의 네이티브 인박스(Claude 권한 경고, Codex 승인 정책)가 신뢰 경계.
- "완료된 쓰기 ≠ 소비 증명"(`agent-transports.md:34-35`).
- 릴레이 수신자 정책: 피어 화이트리스트+능력 부여 — "페어링은 네이티브 승인을
  주지 않는다"(`app.py:157`, `paired-devices.md:513`).
- MCP: 정책=신뢰 경계, "등록은 send/wake 권한을 부여하지 않는다"
  (`agent-transports.md:27-30`).
- 프레임 규율: 디렉토리 0700/파일 0600, O_NOFOLLOW·fsync·atomic replace
  (`identity.py:30-70`), systemd credentials 예외(`identity.py:30-40`).

### 2.4 재전송·순서·손실 처리

- 순서 보장 없음, 자동 재전송 없음, durable outbox 없음, exactly-once 없음
  (`antigravity.md:87-88`, `handoff.py:942`).
- 특이 사례: 수신자 스토어 `begin/finish`로 동일 (binding, message, requestId)
  중복 응답 캐시(`app.py:220-228`).
- handoff는 유일한 예외: 부팅 클록+epoch 의도 ledger, 1회-의도 커밋 펜스,
  톰스톤 30일(연속성 증명 하), 1만 인텐트/32MiB 쿼터(`handoff.py:401-425`,
  `handoff-runtime.md:81-83`).

### 2.5 "실험" vs 프로덕션 경계(명시적)

- ACK는 로컬 POSIX Claude 전용, Codex/SSH는 상관관계 전용, Windows·SSH 수신
  부트스트랩·Codex ACK 미지원(`handoff-runtime.md:7-9`).
- 최초 입력: 수동 전용 앱서버+TUI에서만 성공 — "It does not make the public
  session-peer send support it."(`experiments/codex_first_input/RESULTS.md:30`).
- Antigravity 어댑터는 "experimental" 표기(`antigravity.md:1`), 기존 TUI+bridge 전제.
- 문서가 "정식 지원이 아님"을 문장마다 반복 강조하는 신중함 = 신뢰성 문화.

---

## §3 보안·경계

### 3.1 주입면(최대 위험 표면)

- 본문이 곧 에이전트 턴 입력이 된다(오답: "에이전트가 실행하는 도구 입력").
- 방어 ① 봉투 인용: `peer_delivery_message()` — 발신 본문의 줄바꿈/제어/양방향
  문자를 `| ` 접두·`\uXXXX` 이스케이프 → 본문이 봉투 필드/디리미터 위조 불가
  (`replies.py:252-283`).
- 방어 ② 프레임 바이트 예산: 32KiB(`framing.py:6-10`).
- 방어 ③ 수신자 전용 지문 필드: 프레임 필드로 인증 지문 강제, 본문 클레임 무시
  (`app.py:204-206`).
- 방어 ④ "queued ≠ ACK, 소비 미확인" 강조(`antigravity.md:74-80`).
- 방어 ⑤ "프롬프트 주입에 대한 보장은 경계를 우회하지 않는다"
  (`paired-devices.md:514`).

### 3.2 자격증명 처리

- 하드코딩 없음. SSH = 사용자 ssh_config/키(운영자 신뢰, `ssh.py:7-9`).
- Windows Claude = 세션 `.key` peerToken(`claude.py:486-499`).
- Control = OAuth 기기 코드(클라이언트 1개 고정, 5분 코드, 24h 토큰,
  `relay-auth.md:36-52`).
- 릴레이 어드미션 = 해시 계정/티켓, 개인키·평문 부재(`relay.py:1`, `app.py:93`).
- 비밀 필터: 로그/진단/공개 응답 제외(`relay-auth.md:457-461`).

### 3.3 네이티브 워커 격리

- 워커는 서브프로세스 격리(`native.py:244-261`: 35s/12s 타임아웃, 출력 60KiB
  제한, 실패 시 `native_outcome_unknown`).
- WSL 경유 Windows Codex 바인딩은 경로/실행 파일 검증(`native.py:32-79`).
- Antigravity bridge는 동일 UID+0700/0600+프레임 제한(`antigravity.py:19-34`).

### 3.4 인정된 한계

- `token_possession`은 "독립적 모델 신원이 아니다"(`handoff-runtime.md:75`).
- `tailscale status --json` 서브프로세스 감지(`replies.py:32-56`) 의존.
- 같은 OS 사용자 프로세스는 같은 신뢰 경계(`antigravity.md:94-95`).
- 실측 EC2/퍼블릭 엣지 스톨 원인(#161/#316) 미확정 — 반복 설명만 존재.

---

## §4 결함·품질 이슈 (실제 확인, file:line)

1. **전송별 크기 비대칭**
   - 로컬 1,000,000자(`common.py:53`) vs SSH 90,000자(`common.py:58`,
     초과 시 제출 전 거부 `claude.py:477-483`) vs 릴레이/네이티브 32KiB
     (`framing.py:6`) — 코드 리뷰/통합 패치 운반에 실질 장벽.
   - 효과: 통일된 아티팩트 채널(참조 방식) 부재.
2. **최초 입력(콜드 스타트) 부재**
   - Claude도 Codex도 이미 존재하는 세션만 대상.
   - 직접 명시: "does not make the public session-peer send support it"
     (`experiments/codex_first_input/RESULTS.md:30`).
   - wake는 Codex 0.154.0 단일 버전 핀(`codex.py:1468`, `wake.md:17`).
3. **ACK/증거 불균형**
   - `consumptionConfirmed` 상수 false(`codex.py:1413`).
   - ACK는 로컬 POSIX Claude 한정(`handoff-runtime.md:9`).
   - RC4 5호스트 캠페인도 ACK 20/21, 1건 TUI 종료로 미확인(RELEASING.md:139).
4. **온라인 전용 + 무순서 + 무큐잉**
   - 수신자 접속 중일 때만 전달, 재전송 금지 고정(`sending.py:170`).
   - 순서 보장 없음(`wire.py:56-63` 프레임 단위 전달).
   - 수신자 대기실 60초 idle → 재접속 지수 백오프(`paired-devices.md:198-204`).
5. **단일 파일 11,771줄 + 숨은 내부 플래그**
   - `--_handoff-receipt-collector`/`--_handoff-receipt-producer`(`handoff.py:6-7`),
     `--_return-host`(`commands.py:136`), `--b64` 등.
   - CLI 참조 문서 106KB(`docs/cli-reference.md`) — 운영/학습 부담.
6. **문서 4국어 + 생성물 중복**
   - README/RELEASING/docs ×4국어 동기화 부담 — v1.4.1/1.4.2가 순수 i18n
     용어 정정 릴리스(#373/#374, `docs/releases/v1.4.2.md:8`).
   - `cc_peer.py` 동결본이 루트+`skills/cc-peer/` 중복.
   - wheel에 `session_peer_core/` 미포함(`pyproject.toml:75-76`) → 설치본 디버깅 시
     세그먼트 사상 필요.
7. **미해결 근본 원인 다수(스스로 명시)**
   - #161 외부 스톨 원인 미확정, #316 릴레이 exit139 원인 미확정
     ("passing reruns do not establish a cause", RELEASING.md:130).
   - #181 handoff 게이트(쿼런틴 만족 대기 무효), #268 네이티브 설정 계층,
     #367 집계 발견, #370 테스트 스톨.
8. **릴리스 빈도 vs 기능 안정성**
   - 6주간 20+ 릴리스의 대부분이 "not a runtime defect fix" 문구 반복
     (`v1.4.2.md:9`) — 수용 게이트 미해결 상태로 유지보수 릴리스가 쌓이는 패턴.
9. **Windows 대칭성 부족**
   - 발신/릴레이는 되나 수신 워커/핸드오프/Direct는 POSIX 전제 다수
     (`native.py:22-29`, `handoff-runtime.md:9`).
   - 3.14/free-threaded는 별도 인정 게이트(RELEASING.md:41).
10. **진단 과잉/운영 복잡성**
   - `attemptId`/룸 매칭/`receiverWsAgeMs`/`ingressFrames` 체계(`paired-devices.md:175-196`)
     는 귀인 실패 시 "unattributed"로 남기는 신중함 — 정확하지만 복잡.

---

## §5 개선점 도출 — 다중 업체 에이전트 토론+코딩+산출물 작업장(TalkAbout) 적용

형식: **[파일:줄 → 좋은 점 → 우리 적용안(구체 계약/메커니즘)]**
(이 항목: 영향 / 비용 / 위험)

### 즉시(짧은 주기, 저비용, 스키마/게이트 추가)

1. **[replies.py:252-283 → 외부 피어 본문 인용/이스케이프 프레임]**
   - 좋은 점: 발신 본문을 봉투와 분리, 제어/양방향 문자 `\uXXXX` 중화,
     "Sender/session claims … unverified" 명시.
   - 적용안: 토론 메시지 스키마에 `payload_kind: peer_callout` + "본문은
     에이전트 입력 금지·표시용" 마커 필수, 서버가 신뢰 발신자(vendor+session)를
     별도 필드로 부여, 본문에서 파생 금지.
   - 영향: 턴 입력 오염·스푸핑 감소 / 비용 낮음 / 위험 낮음(확장 필드).
2. **[sending.py:22-66 + adapters.py:76-78 → 상태 3-분리 계약]**
   - 좋은 점: `submitted`/`queued`/`consumptionConfirmed`/`ack` 구분 고정,
     `retryAllowed=false` 고정.
   - 적용안: TalkAbout 실행 예약/영수증 응답을 `{status, submitted,
     consumption_confirmed, ack, retry_allowed, reason}` 열거로 고정, GUI/API 동일
     용어 사용, 미확인=unknown·재시도 금지.
   - 영향: 신뢰성 커뮤니케이션 ↑ / 비용 낮음 / 위험 낮음.
3. **[framing.py:6-10 → 프레임 바이트 예산 선검사]**
   - 좋은 점: 본문+봉투+지문 전체를 저널/네이티브 전에 제한, 초과 시 제출 전
     거부(부분 제출 금지).
   - 적용안: 산출물 게시 API에 UTF-8 상한(예: 본문 32KiB)+`size_budget` 필드,
     초과=제출 전 4xx.
   - 영향: 산출물 무결성·예측성 ↑ / 비용 낮음 / 위험 낮음.
4. **[identity.py:19,120-133 + app.py:204-206 → 전송 계층 지문 별도 필드]**
   - 좋은 점: 본문 클레임은 신뢰하지 않고 수신자가 검증한 인증 사실만 부여.
   - 적용안: 메시지에 옵트인 `sender_fingerprint`(인증 세션 파생) 추가,
     `from`/`vendor`는 표시용 선언.
   - 영향: 스푸핑·권한 오인 감소 / 비용 낮음 / 위험 낮음.
5. **[mcp.md:14-31,82-96 → 정책=권한 단일화]**
   - 좋은 점: 리스트 기본, send/진단/프리플라이트 분리 권한, "등록=권한 아님".
   - 적용안: 방 컨트롤: 입장=읽기, 발언/파일 편집/산출물 게시를 별도 capability
     토큰, opt-in 워크 프로필과 동일 수준 정책.
   - 영향: 과전권 방지 / 비용 낮음 / 위험 낮음.
6. **[claude.py:464-483 + codex.py:1392-1397 → 크기 한도 선언·플랫폼 예산]**
   - 좋은 점: 원격/Windows 한도를 효과 전 반영, CreateProcessW 단위 예산,
     생성 제한을 본문 검사와 같은 지점에서 수행.
   - 적용안: 메시지/산출물 API에 채널별 상한 명세(로컬/SSH/릴레이/파일) 문서화+
     거부 코드 고정.
   - 영향: 예측 가능한 실패 / 비용 낮음 / 위험 낮음.
7. **[handoff.py:56-59,436-461 → 의도 ledger+에폭+1회-커밋 펜스 → 이벤트 저장]**
   - 좋은 점: fsync된 의도 레코드+부팅 클록 연속성, 크래시 후 unknown 유지.
   - 적용안: 턴/산출물 커밋을 1회-의도 레코드(room_id, turn_id, payload_digest,
     epoch)로 저장, 실패=unknown 상태 보존·재시도 금지.
   - 영향: 감사성·중복 방지 / 비용 중간 / 위험 중간(저장 스키마).
8. **[handoff.py:814-868 수신자 수집기 + :1191-1196 ack → 상태/워치 계약]**
   - 좋은 점: 읽기 전용 상태·NDJSON 워치(스냅샷≠이벤트 스트림, 16KiB/프레임),
     "완료=성공 아님".
   - 적용안: 턴 처리 상태 엔드포인트+워치를 열거형 스냅샷으로, 클라이언트는
     종료 프레임 포함해야 성공 처리.
   - 영향: 진행 가시성·오해 감소 / 비용 중간 / 위험 낮음.
9. **[relay.py:22-57 프록시 신뢰 규칙 → 신뢰 헤더 규율]**
   - 좋은 점: X-Forwarded-For 미신뢰·공유 비밀 헤더 한정, 중복 헤더 예외 처리.
   - 적용안: 게이트웨이/엣지 뒤에서도 동일 원칙(소켓 IP+비밀 헤더) 적용 문서화.
   - 영향: IP 스푸핑 위험 감소 / 비용 중간 / 위험 낮음.
10. **[release 게이트(`ci.yml:321-339`, RELEASING.md:143-148) → 산출물 봉인]**
    - 좋은 점: 준비/공개 분리, 해시 고정·인증 어테스테이션, 미주장 원칙.
    - 적용안: change_proposal에 reviewer/vendor 서명+부모 리비전 필드 봉인,
      불변 게시, 게시 기록 분리 문서.
    - 영향: 산출물 신뢰·감사 ↑ / 비용 중간 / 위험 낮음.

### 중기(모듈/제품)

11. **[replies.py:322-351 Reply-To URI → 도메인 아티팩트 참조 URI]**
    - 좋은 점: 전송 독립·불변·버전화된 주소로 회신 목적지 캡슐화, 파싱 안전.
    - 적용안: 참조 토큰(vendor, room, turn, artifact_id, revision) URI로 확장,
      라우팅과 콘텐츠 분리.
    - 영향: 회신 안정성·확장성 ↑ / 비용 중간 / 위험 낮음.
12. **[antigravity.py:383-400 requestId 중복 억제 → 발행 ID 멱등성]**
    - 좋은 점: 같은 세대 내 ID+본문 중복 캐시, 변경 본문 거부.
    - 적용안: 게시 멱등 키(room+turn+producer)로 중복 게시 억제, 변경 시 충돌 거부.
    - 영향: 중복 산출물 방지 / 비용 중간 / 위험 낮음.
13. **[store.py:121-150, rotation.py → 키/복원 수명주기]**
    - 좋은 점: 퇴직/폐기/리커버리 툼스톤, 복원 DB 쿼런틴.
    - 적용안: 인증 토큰/워크 프로필 자격에 로테이션 버전+폐기 체계.
    - 영향: 자격 수명 관리 ↑ / 비용 중간 / 위험 중간.
14. **[wake_chain.md/`codex.py:1468` → 유한 웨이크 체인]**
    - 좋은 점: 깊이 상한(기본 3)·하향 전파 제한(상향 금지), 목표별 독립 예산.
    - 적용안: 에이전트 실행 체인에 최대 홉/왕복 제한+origin 고정+독립 예산.
    - 영향: 실행 폭주 방지 / 비용 중간 / 위험 낮음.
15. **[없음 → 방/브로드캐스트·순서·산출물 저장(신규 설계)]**
    - 좋은 점: 없음(1:1 전용, 이것이 빈틈).
    - 적용안: 발행 시퀀스 번호+방-스코프 게시로 확장, 소비 증명은 분리, 대역폭은
      참조 채널로 우회.
    - 영향: 토론 모델 성립·공동 코딩 기반 / 비용 높음 / 위험 중간.

### 종합 판단

- session-peer가 가장 많이 겪는 실패 패턴 = "전달 성공=처리 성공" 오인·재전송.
- TalkAbout은 ②번 3-상태 계약과 ⑦번 1회-의도 저장으로 선제 방어.
- ①⑪⑮로 메시지를 **데이터(참조)** 로 다루어 대역폭 함정(32KiB)을 회피.
- ⅰ) 단일 파일/숨은 플래그/4국어 문서 부담은 모방하지 않는다.

---

## §6 미확인·한계

- GitHub 이슈 21건의 실제 목록/상태는 API 조회 없이 **문서 속 언급**(#47, #69,
  #161, #164, #180, #181, #268, #296, #302, #304/#306/#307, #316, #317/#318,
  #343/#344, #349/#350/#353, #356-359/#361-364, #367, #370, #373)에서 유추함 —
  열림/닫힘·라벨은 미확인, 과장 금지.
- 네이티브 주입·네이티브 wake·실제 SSH/릴레이 전송은 **실행하지 않음**
  (수정·테스트·설치 금지 지침 준수) → 네이티브 경로 관측은 정적 판독 한정.
- 테스트 스위트 실행·CodeQL 결과·의존성 감사 결과는 재실행/검증 아님.
- `control/`(Node/React/BetterAuth)·배포(Cloudflare/Better Auth 운영) 상세는
  `src/server/*.ts`·`deploy/` 판독 일부만 수행, 전체 코드 감사 아님.
- "PASS" 성명(0.154.0 wake, RC4 캠페인, 존화 상세)은 저장소 기록 기준 재진술.
- 동기/시계 비교는 boot/utc 경계 경고에 의존(no cross-host clock) — 순서·지연
  정량 계측 아님.
- i18n 문서 4종과 코드 동기화 수준은 영문 표본 판독 기준,
  일본어/중국어 본문 상세 대조는 미수행.
- 21개 이슈 수치·다운로드 수·브랜치 보호 상태 등 외부 사실은 클론 내 증거만
  인용 — 실측 재확인 필요.
