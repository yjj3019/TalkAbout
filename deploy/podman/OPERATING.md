# Manual operating activation / 사용자 입력 후 활성화

**PREPARED / WAITING FOR USER CREDENTIAL INPUT AND FREE LOOPBACK PORT — 2026-10-13**

현재 상태는 배포 준비이며 서비스 배포 완료가 아닙니다. 승인된 source main 29a286b3cb0166d04aa0c721e6bdd31a72b18a91의 rootless image가 빌드됐고 격리 합성 readiness/outage/restart/persistence/logical restore/cleanup이 통과했습니다. dedicated internal network와 새 빈 named volume만 준비했습니다. 운영 container, DB/role, password secret, human/agent credential, 자동기동은 아직 없습니다. 승인된 loopback port가 이미 사용 중이면 다른 listener를 변경하지 않고 새 port 결정까지 멈춥니다.

## 1. 직접 입력하는 secret 준비

승인된 호스트의 rootless 사용자 interactive terminal에서 아래 도구를 실행합니다. SSH는 기존 신뢰된 key 검증을 유지하고 TTY를 배정합니다. 내부 host alias/address·사용자 경로는 이 문서에 기록하지 않습니다. 비밀번호를 채팅이나 command argument에 넣지 않습니다.

```sh
python3 deploy/podman/secret_setup.py
python3 deploy/podman/secret_setup.py --create
```

첫 명령은 상태만 표시하며 아무 값도 만들지 않습니다. 두 번째 명령은 사용자가 owner/runtime password를 직접 숨김 입력·확인한 경우에만 세 ref를 만듭니다. tty가 없거나 rootless가 아니거나 ref가 이미 있으면 멈춥니다. 출력에는 secret 이름만 있고 값은 없습니다. 일부 생성 후 실패해도 기존 값을 삭제/회전하지 않으므로 재실행으로 덮어쓰지 말고 정확한 상태를 점검합니다.

| Ref | 용도 | Container mount |
|---|---|---|
| talkabout-db-password | 신규 dedicated DB owner 초기 password | DB /run/secrets/pg-password, UID 999, mode 0400 |
| talkabout-db-init-url | schema 초기화용 owner DSN | 일회성 app /run/secrets/talkabout-db-url, UID 10001, mode 0400 |
| talkabout-db-url | 최소 권한 runtime DSN | app /run/secrets/talkabout-db-url, UID 10001, mode 0400 |

DSN은 private alias talkabout-db, port 5432, dbname talkabout와 지정된 owner/runtime role만 사용합니다. password 값은 사용자가 입력한 그대로 protected stdin으로 Podman secret create에 전달합니다. secret storage는 사용자 rootless store의 기본 file driver이며 read-protected이고 암호화 보장을 하지 않습니다. 저장/백업/회전 정책을 별도 관리합니다. 입력 도구는 DB나 관리자 계정을 만들지 않습니다.

## 2. DB·schema·runtime role

사용 가능한 승인 loopback PORT와 검증 IMAGE를 선택한 뒤 `plan.py --app-image "$IMAGE" --host-port "$PORT"`의 argv를 검토합니다. 준비된 network/volume은 project label과 internal 여부를 확인해 보존하고 다시 삭제하거나 교체하지 않습니다. named DB container가 이미 있으면 신규 DB 초기화를 반복하지 않습니다. 기존 volume이 있으면 데이터를 읽거나 없애는 초기화 명령을 임의 실행하지 않습니다.

세 secret 입력이 완료된 뒤 승인된 DB argv만 직접 실행합니다. DB health는 schema 준비와 다릅니다. owner init URL secret을 target=talkabout-db-url,uid=10001,gid=10001,mode=0400으로 연결한 일회성 app container에서 `init-db`를 직접 실행합니다. 해당 container에는 publish가 없고 plan의 non-root/read-only/limited tmpfs/cap/resource flags를 유지합니다. serve는 schema를 자동 생성하지 않습니다.

runtime role은 DB owner의 interactive psql에서 직접 설정합니다. password literal을 SQL/argv/history에 넣지 않고 `\password`의 숨김 prompt를 사용합니다. secret 입력 단계와 같은 runtime password를 직접 입력해야 합니다. mismatch이면 연결/preflight가 실패하며 자동 회전하지 않습니다.

```sh
podman exec -it --user 999:999 talkabout-db psql -U talkabout_owner -d talkabout
```

```sql
CREATE ROLE talkabout_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password talkabout_app
GRANT CONNECT ON DATABASE talkabout TO talkabout_app;
GRANT USAGE ON SCHEMA public TO talkabout_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO talkabout_app;
```

이미 존재하는 role은 CREATE를 반복하지 않고 현재 권한·인증을 보존합니다. 이 문서의 SQL은 agent가 실행하지 않았습니다. owner URL은 init 작업에만 노출하고, runtime secret으로 preflight가 통과한 뒤 app argv를 직접 실행합니다.

## 3. 최초 소유자 GUI 설정·접속

DB/runtime 준비 후 사용자가 비공개 interactive TTY의 일회성 app container에서 `setup-code`를 실행합니다. runtime URL secret만 사용하며 owner secret이나 Podman socket을 앱에 전달하지 않습니다. 코드가 터미널에 한 번 표시되고 DB에는 해시만 저장됩니다. 코드 출력은 캡처·로그·README·Git·채팅으로 복사하지 않습니다. TTL 15분/코드 불일치 5회 제한은 DB에 영속화됩니다. 만료/소진한 코드만 같은 명령으로 명시적으로 재발급할 수 있으며 완료 후에는 재설정할 수 없습니다.

승인된 host loopback PORT로 app을 시작한 뒤 사용자가 KO/EN 초기 화면에서 코드·workspace·표시 이름·handle·새 비밀번호와 확인을 직접 입력합니다. 완료 후 일반 로그인합니다. 초기 설정 전에는 `/health`만 최소 liveness이고 `/ready`와 운영 API/scheduler는 잠겨 있으므로 ready 기반 healthcheck 실패가 예상됩니다. 자동 재시작 정책을 추가하지 않습니다. 자세한 계약은 [초기 설정 안내](../../docs/INITIAL_SETUP.md)를 확인하세요.

기존 소유자가 있는 설치는 기존 로그인을 유지하며 초기 설정으로 재설정하지 않습니다. 기존 DB에 새 기능을 설치할 때는 승인된 백업 후 owner 전용 `init-db`로 additive table을 준비하고 runtime role에 `GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.installation_setup TO talkabout_app;`만 추가합니다. runtime에 DDL을 주지 않습니다. 기존 수동 `bootstrap-human`은 호환 경로로 남지만 코드 발급 후에는 거절됩니다.

개인 PC 접속은 승인된 SSH forwarding을 사용하거나 HTTPS를 준비합니다. 공용 bind·화면 공유·실제 코드가 들어간 URL은 사용하지 않습니다. DB 비밀번호는 별도 사용자 직접 입력 절차를 유지하며 GUI 로그인 비밀번호와 공유하지 않습니다. 이번 코드 변경으로 운영 코드/비밀번호/토큰을 생성하거나 실제 서비스를 기동하지 않았습니다.

## 4. 활성화 확인·재시작·보존

```sh
podman healthcheck run talkabout-db
podman healthcheck run talkabout-app
curl --fail "http://127.0.0.1:$PORT/ready"
```

source label과 recorded image ID, non-root user, HEALTHCHECK, mount/port/labels를 확인합니다. 운영 readiness/restart/data 상태는 실제 사용자 입력 후 별도 확인해야 하며 합성 fixture 결과로 대신하지 않습니다. DB 복구 뒤 scheduler failure가 남으면 TalkAbout app만 수동 재시작합니다. 기존 listener나 다른 서비스는 재시작하지 않습니다.

backup/restore는 [Podman runbook](README.md)의 custom logical dump·private backup 경로·role/secret 별도 복구·새 volume restore drill 절차를 따릅니다. 운영 데이터를 repository/Library/채팅에 올리지 않고 rollback 중 volume/secret/schema를 삭제하지 않습니다. 자동기동/Quadlet enable/linger는 설정하지 않습니다.

## English

This is a manual credential-input stop point, not a completed service launch. The exact approved image and isolated synthetic smoke were verified; only dedicated non-secret network/empty persistence were prepared. Operating containers, roles, secrets and administrator credentials remain absent.

The user runs secret_setup.py --create in a trusted interactive rootless terminal and chooses passwords directly. No password is generated by the agent, printed, embedded in argv/env/image or written to Git. Existing refs are preserved. File-driver storage is read-protected and requires its own encryption/backup/rotation policy. The helper creates secret references only, not database roles or human accounts.

After a free approved loopback port is chosen, the user explicitly runs DB/schema setup, configures the runtime role through interactive psql \password, checks preflight, explicitly issues setup-code in a private TTY, starts the locked app and chooses/confirms their administrator password in the initial GUI. Existing owners retain login; owner-only additive migration grants runtime DML on installation_setup. The one-time code expires in 15 minutes and locks after five mismatches; only expired/exhausted pending codes can be reissued, never completed installations. Real operating health/restart/data verification follows these actions. Public networking, auto-start, actual AI and agent token issuance remain separate. Never overwrite existing data or stop another listener to resolve a port collision.
