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
