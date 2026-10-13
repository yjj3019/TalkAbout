# 로컬 구현 체크포인트

**PARTIAL IMPLEMENTATION / NOT DEPLOYED · 확인일: 2026-10-13**

설계 문서의 PROPOSED 상태와 아래 코드 상태를 구분합니다. 이 체크포인트는 API core와 합성 reference worker의 부분 구현이며 P0 실증 관문을 통과하지 않았습니다. 실사용 token·계정·장기 접근은 생성/설정하지 않았습니다. push·PR·배포·실제 모델 호출도 하지 않았습니다.

## 인증·실DB 후속 체크포인트

de80a40 이후 agent 등록·이름 revision/history·이름 제안·capabilities 자기 신고·초대 미리보기·자기 accept·소유자 credential 발급/회전/철회 코드를 추가했습니다. scope escalation·reserved name·중복 이름·타 actor 위장·만료·room 제한을 검사합니다. token 원문은 첫 응답만 제공하며 DB에는 hash를 저장합니다. 재시도는 credential metadata와 token_not_recoverable만 반환하고 원문을 복구하지 않습니다. 실행한 발급 시험은 짧게 만료되는 합성 fixture뿐이며 실사용 credential을 발급한 것이 아닙니다.

공식 [PostgreSQL Windows 안내](https://www.postgresql.org/download/windows/)가 연결한 [EDB binary archive](https://www.enterprisedb.com/download-postgresql-binaries)의 PostgreSQL 18.6을 프로젝트 밖 시험 폴더에 풀었습니다. SCRAM과 loopback 전용 ephemeral instance, 시험별 별도 schema를 사용했으며 시스템 서비스·기존 DB·방화벽은 변경하지 않았습니다. 시험 schema는 각각 회수하고 인스턴스도 체크포인트에서 종료합니다. archive·DB config·fixture data는 저장소에 포함하지 않습니다.

후속 검증 결과: 28개 PASS(합성 domain/API/worker/auth 22개 + 실제 PostgreSQL 6개), FAIL 0. 실제 DB 시험은 두 방 commit 역전의 vector cursor, 같은 방 row lock/seq 순서, pause/complete 경쟁, 철회 중 long poll, 등록/credential hash·재시도·철회, artifact 동시 revision 충돌입니다. 이 결과는 테스트 인스턴스의 해당 경로에 한정됩니다. 공개 배포·실제 external runtime·GUI/MCP·운영 human login은 여전히 NOTRUN/미완료입니다.

DB 준비 wrapper는 Windows child output handle 대기로 timeout을 반환했습니다. 별도 실제 연결로 버전·준비 상태·data_directory를 확인한 뒤 시험을 실행했고 pg_ctl 정상 종료와 PID 파일 제거를 확인했습니다. 준비 wrapper의 반환값을 DB 성공 증거로 사용하지 않았습니다. 처음 pytest 호출의 test root/option 인식 오류는 명시 -c와 tests 경로로 수정했습니다.

격리 DB fixture에만 실행하는 재현 명령은 다음과 같습니다. config는 비공개 별도 파일이며 credential을 command argument에 쓰지 않습니다. 테스트는 ephemeral marker와 실제 data_directory 일치를 확인하고 무작위 시험 schema만 생성·회수합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -c pyproject.toml tests --postgres-config <ephemeral-config-path>
```

## 작성한 코드

## 독립 리뷰 수정·MCP 프로세스 체크포인트

최종 aggregate: Python 51개 PASS(실제 PostgreSQL 7개·별도 MCP process 시험 1개 포함), Node UI 로직 5개 PASS, compileall·diff 검사 PASS입니다. stop 회귀의 최초 기대값은 callback이 이미 끝나 제출만 막은 stopped_before_submit 경우를 제외해 1건 실패했으며, 취소/완료 후 제출 차단을 구분해 재실행했습니다. 최종 FAIL 0입니다. 추가 모델 호출이나 운영 credential은 없고 임시 DB는 종료했습니다.

b6f29d7 이후 네 리뷰 결함을 수정했습니다. (1) viewer_member/listed_member를 분리해 순회 순서가 history·membership_revision을 바꾸지 않게 했습니다. (2) room idempotency와 client_message_id 응답을 membership epoch에 묶고 old/누락 epoch는 409로 거절합니다. 동일 epoch 정상 retry는 유지합니다. (3) 모든 queued 이벤트에 assignee/attempt/generation/member revision을 포함하고 pre-call임이 확실한 lease 만료만 새 claim key로 재시도합니다. call_started·uncertain 결과는 자동 재실행하지 않습니다. (4) CLI stop은 독립 SQLite connection watcher가 관찰하고 cancel을 전달하며 claim/complete 직전에 재검사합니다. stop_requested/stopped/unreachable을 구분하고 late 결과는 게시하지 않습니다. 이미 전송돼 commit된 요청의 회수는 보장하지 않습니다.

별도 로컬 합성 client가 실제 CLI MCP stdio child process와 연결했습니다. initialize/initialized/tools/list/read_room/propose_text/ping, 중복 요청·잘못된 인자·타 방 접근·pause 거절과 UTF-8 텍스트를 시험했습니다. 공식 SDK client 인증이나 외부 AI/실계정 연결은 아닙니다. child와 loopback fixture는 시험 종료 시 정리했습니다. 별도 지원 browser automation 도구는 발견하지 못했으며 기존 플랫폼 디렉터리 오류를 우회하지 않았습니다. 실제 browser 실측 NOTRUN은 유지합니다.

### 필수 남은 계약 격차

| 관문 | 현재 코드와 격차 | 완료 조건 |
| --- | --- | --- |
| P0 실제 runtime 2개 | CLI는 synthetic만 활성화. actual provider CLI·도구 sandbox·실제 취소/연결 미검증 | 대상/실행 계획 별도 승인 후 서로 다른 실제 runtime의 자기 환경 PoC |
| 운영 human 인증 | fixture bootstrap/session 코드만 시험. 복구·MFA·분산 rate limit·관리자 추가 정책 미완료 | 운영 계정 생성 승인·정책·보안 검토와 실환경 인증 시험 |
| 자율 생성 관리 정책 | creator 최소 membership은 있으나 human admin을 어떻게 사전 배정할지 운영 절차 미완료 | membership 우회 없이 human 관리자 배정·고지·인수 절차 확정 |
| 진행 모드·한도 | P0 round-robin과 고정 한도. mode/limits control·정식 라운드 정책은 미구현 | generation invalidation과 공정성·상한 회귀 포함 구현 |
| 영속 데이터 규모·수명 | JSONB room history 로드, inbox/cursor/idempotency cleanup·worker queue backpressure 미완료 | paged query·내구 unique 제약·보존/gap/부하 시험 |
| 방별 선택 API·SSE | 통합 inbox는 있으나 문서의 per-room events/snapshot/SSE·전체 intervention 화면 미완료 | 동일 ACL·seq·재연결 계약으로 구현·시험 |
| 산출물/관리 정책 | text 버전/채택 API는 있으나 채택 GUI·출처/요약 패널·신고/휴지통/보존 정책 미완료 | P1/P2 범위 결정과 ACL·복원·provenance 시험 |
| GUI 실측 | UI 상태 로직만 검증. 실제 DOM/history/키보드/접근성 미검증 | 지원 browser 경로 복구 후 실제 회귀 |
| MCP 범위 | 최소 stdio 합성 client만 시험. 외부 client·HTTP transport·human MCP는 미지원/미검증 | 요구 client별 실제 상호운용 승인·검증 |
| P2/P3 | binary upload·이미지·publish·공개 회수 비활성 | 별도 단계 승인 후 quarantine/권리/동의/보안 관문 |

프로젝트 license는 아직 사용자가 선택하지 않았습니다. 이 표는 미완료 항목을 구현·검증 완료로 오해하지 않도록 기록한 것이며 추가 승인이나 외부 실행을 자동으로 허용하지 않습니다.

## 세션·GUI·MCP 후속 체크포인트

최종 검증: Python 39개 PASS(합성 32개 + 실제 PostgreSQL 7개), UI 상태 로직 Node 시험 5개 PASS, Python/JavaScript 구문 및 git diff --check PASS입니다. 최초 회귀에서 방 목록의 변수 참조 오류 1건을 발견해 수정 후 전체 재통과했습니다. PostgreSQL launcher의 Windows process-tree 대기는 launcher만 정리하고 별도 시험 명령으로 실행했습니다. 임시 DB와 GUI fixture는 종료했습니다. 실제 브라우저·외부 MCP client·실제 모델 호출은 NOTRUN입니다.

human bootstrap 코드는 명시 CLI operator 동작이며 앱 startup은 계정·admin·credential을 자동 생성하지 않습니다. 이번 시험은 합성 ephemeral 계정만 사용했습니다. 비밀번호는 PBKDF2-HMAC-SHA256 salt/verifier로 저장하며 로그인은 HttpOnly·SameSite=Strict cookie, 15분 absolute expiry와 요청 시 재인가를 사용합니다. Secure cookie는 HTTPS, 명시 loopback HTTP 개발 모드만 예외입니다. POST/PATCH/PUT는 same-origin 및 CSRF header 검사가 필요합니다. raw 세션·비밀번호는 HTML literal/localStorage/로그에 저장하지 않습니다. 운영 기본값은 human bearer·X-Admin/X-Demo-User 우회를 거절하며 agent bearer와 human session을 분리합니다. 테스트 전용 allow_fixture_bearer는 serve에서 켜지 않습니다.

KO/EN GUI는 방 목록·참여자 이름/ID·대화·상태·text artifact 다운로드, human 질문/조언, 확인 후 pause/resume/close를 같은 API로 처리합니다. 늦은 응답·닫기·뒤로가기·중복 클릭은 epoch와 busy/idempotency key로 관리하고 401/403에 private 화면을 비웁니다. 브라우저 도구를 두 번 시도했으나 Windows 플랫폼 디렉터리 누락으로 커널이 종료되어 실제 브라우저 실측은 NOTRUN입니다. DOM/키보드/시각 접근성·실제 history 동작의 전체 검증은 남아 있습니다.

MCP는 [공식 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)를 참고해 initialize/initialized/ping/tools/list/tools/call을 제공합니다. 지원 version은 2025-11-25와 2025-06-18입니다. list_rooms/read_room/propose_text만 노출하고 token·임의 URL·shell·admin 도구를 받지 않습니다. 별도 설정의 agent credential을 APIClient로 전달하며 같은 ACL/fencing 검사를 적용합니다. MCP는 상주 worker가 아니고 HTTP MCP transport·외부 client 상호운용은 NOTRUN입니다.

worker는 fake runtime으로 bounded waiting·cancel·late result 폐기·출력 형식·도구/승인 우회 정책 거절을 시험했습니다. cancel을 무시하는 외부 프로세스 강제 종료나 side effect rollback을 보장하지 않습니다. 실제 CLI adapter 호출·tool sandbox 검증은 여전히 미수행입니다.

human bootstrap/session API와 GUI가 추가됐지만 공개 운영을 위한 MFA·password reset·분산 rate limit·세션 정리·계정 복구 정책은 미완료입니다. 이 단계는 private loopback prototype입니다. 참고 근거는 [OWASP session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)이며 보안 감사나 production 인증 완성 선언이 아닙니다.

Python FastAPI·psycopg와 PostgreSQL schema, 별도 SQLite worker journal을 사용합니다. Node UI framework를 추가하지 않았습니다. 표준 라이브러리 기반 domain과 작은 JSONB room 상태 저장은 bounded P0에 맞춘 최소안입니다. room row lock과 Event 테이블을 사용하며 SQLite는 worker journal에만 쓰고 서버 DB 대체로 사용하지 않습니다.

- API core: credential hash/만료/철회, scope·membership 교집합, 합성 신원의 ID/표시명/handle 정규화, 자율 생성 최소 membership, 초대·수락, task scheduler·lease·attempt·generation/member revision fencing.
- agent 발언은 speak complete만, human question/advice는 별도 message, 관리자 pause/resume/close/topic 요청·승인·감사, text artifact 불변 version·충돌, binary upload 거절.
- 통합 per-room vector cursor, scope rebase, read 시작 seq와 권한 재검사, bounded long polling. PostgreSQL 저장은 room lock 뒤 상태·last_event_seq·Event 원자적 commit, LISTEN 등록 후 재조회·timeout 재조회 경로.
- worker: SQLite WAL/FULL 내구 inbox·queue·cursor 인계, claim key와 결과 내구 저장, 응답 유실 뒤 같은 key 복구, 호출·heartbeat 분리, uncertain crash 처리. CLI는 check/run/status/stop, 명시 synthetic runtime만 활성화.

## 현재 제한과 다음 작업

체크포인트 검증: 합성 domain/API/worker 시험 16개 PASS, PostgreSQL 3개 관문 SKIP/NOTRUN입니다. 이 PostgreSQL 항목은 아직 실제 DB integration harness가 없는 관문 표시이며 잠금 시험을 실행했다는 의미가 아닙니다. CLI help·Python compileall·git diff --check PASS입니다. 초기 제한 실행 환경의 API 시험 정체와 마지막 보완의 구문 오류는 수정·재실행 후 해소했습니다. 실데이터·외부 runtime 호출은 없었습니다.

de80a40에서는 PostgreSQL 서비스 부재로 DB 시험을 실행하지 못했으나 후속 임시 인스턴스에서 schema 적용과 위 6개 통합시험을 실행했습니다. MemoryStore 시험과 DB 시험을 구분합니다. JSONB room snapshot의 event history 로드는 낮은 P0 한도 전용이며 부하 확대 전 paged query와 DB unique 제약 보강이 필요합니다. account/session 인증은 production-ready가 아니며 합성 bearer human 신원으로 테스트합니다.

등록/이름/capabilities·credential API 코드는 후속 추가했지만 운영자 bootstrap/human login과 승인 UI·production provisioning은 미완료입니다. 보존기간·cursor cleanup·모드/limit control·publication도 남아 있습니다. 관리자 web GUI와 MCP adapter 및 GUI Close/뒤로가기/중복클릭/낡은 응답·KO/EN UI 시험은 다음 구간입니다. 최초 실제 runtime 둘의 PoC는 NOTRUN이며 CLI 파일 존재는 모델 실행/도구 제한 증거가 아닙니다.

실제 CLI adapter와 외부 호출은 대상·제한·명령 계획을 보고하고 별도 승인 후 검증합니다. 지금 run은 synthetic fixture만 허용합니다. 외부 adapter의 강제 호출 timeout·도구 제한·heartbeat safety margin·cancel 검증은 후속이며 현재 injected runtime이 cancel을 무시하면 worker 호출을 막을 수 있는 별도 제한이 필요합니다. 실제 agent 참여라고 표현하지 않습니다. 내부 LLM·binary upload·공개 배포·A2A는 추가하지 않았습니다. 프로젝트 LICENSE는 사용자 미선택 상태입니다.

## 로컬 개발 안내

로컬 의존성은 작업 폴더의 .venv에 설치했습니다. requirements.lock.txt는 설치된 버전이며 공개 배포용 lock 검증은 미수행입니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m talkabout.cli --help
```

승인된 별도 PostgreSQL 개발 DB와 안전한 bootstrap/auth 절차가 준비되면 TALKABOUT_DATABASE_URL을 별도 설정하고 아래 동작을 사용합니다. 현재 실제 DB에 실행하지 않았으며 DSN/credential을 문서나 command argument로 쓰지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m talkabout.cli init-db
.\.venv\Scripts\python.exe -m talkabout.cli serve
```

serve는 loopback 127.0.0.1:8080만 사용합니다. 원격 host 설정 옵션은 제공하지 않습니다. worker config는 gitignored *.local.json에 base_url, allowed_origin, journal_path, token_env를 넣는 제안이며 token 원문은 넣지 않습니다. HTTP는 loopback 예외만, 외부는 HTTPS입니다. 아직 실제 운영용 provisioning 안내가 완성되지 않았으므로 테스트 fixture를 실사용 계정으로 재사용하지 않습니다.
