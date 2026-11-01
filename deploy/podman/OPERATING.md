# Automatic installation / 자동 설치

LOCAL IMPLEMENTATION / OPERATING NOT DEPLOYED — 2026-10-14

## 한국어

기본 경로는 `install.py`이며 공통 기본 비밀번호를 사용하지 않습니다. Linux rootless Podman, 검증된 source SHA 이미지와 명시적으로 선택한 비어 있는 loopback 포트가 필요합니다. 인자 없는 실행은 NOT_EXECUTED이며 비밀을 생성하지 않습니다. 별도로 승인된 운영 기동 시에만 아래를 실행합니다.

```sh
python3 deploy/podman/install.py --install --app-image "localhost/talkabout:$SOURCE_SHA" --host-port "$PORT"
```

설치자는 DB 비밀번호를 입력하지 않습니다. owner/runtime 비밀번호를 독립적인 48바이트 CSPRNG 난수로 생성하고 rootless Podman secret bundle에 먼저 저장합니다. 이후 DB password·owner DSN·runtime DSN용 secret을 같은 bundle에서 파생합니다. 값은 protected stdin과 secret mount로만 전달하며 Git·이미지·argv·환경변수·출력·로그·채팅에 넣지 않습니다. 기존 값은 덮어쓰거나 회전하지 않습니다. 기본 file driver는 읽기 접근을 제한하지만 암호화를 보장하지 않으므로 host 저장소 암호화·백업·회수 정책은 별도 관리합니다.

사용자 private 디렉터리의 비밀 없는 journal과 OS 파일 잠금으로 동시 설치를 막습니다. journal은 mode 0600, 디렉터리는 0700이며 atomic replace/fsync로 진행 상태를 기록합니다. rootless·source SHA label·non-root 이미지와 자원 소유 label을 확인합니다. DB role·schema·설치 표식은 DB 트랜잭션으로 묶습니다. 기존 DB 인증은 실제 TCP 연결로 확인하고 비밀번호 불일치를 자동 reset으로 해결하지 않습니다. runtime은 NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS와 앱 테이블 DML만 사용하며 DDL 권한을 주지 않습니다. DB owner secret은 provisioning 일회성 container에만 전달합니다. 상시 app에는 runtime DSN만 제공하며 Podman socket을 제공하지 않습니다.

처음 설치는 전용 private network·새 volume·DB를 만든 뒤 schema/runtime role을 준비하고 loopback app을 기동합니다. host 포트를 자동 선택하거나 기존 listener를 종료하지 않습니다. 앱과 DB는 non-root, read-only root, cap-drop, no-new-privileges, CPU/메모리/PID 제한을 유지합니다. public bind·방화벽·TLS·auto-start·호스트 권한은 변경하지 않습니다. 설치 완료 응답은 DB 구성과 app liveness 확인을 의미합니다. 최초 소유자 설정 전 `/ready`와 일반 API/scheduler는 잠겨 있습니다.

재실행은 같은 journal/config/image를 사용해 bundle과 DB 인증을 확인하고 그대로 재사용합니다. 일부 파생 secret이 빠지면 bundle의 동일 값으로 복구합니다. 활성 app이 이미 mount한 동일 secret 값은 바뀌지 않습니다. credential/schema/image 변경은 이 설치 도구의 자동 upgrade 범위가 아닙니다. 일치하는 설치 표식의 기존 DB에는 role/schema 재생성이나 회전을 수행하지 않습니다.

| 상황 | 처리 |
| --- | --- |
| bundle 저장 전 중단, 아직 전용 자원 없음 | 같은 journal에서 다시 생성 가능 |
| bundle 저장 후 중단 | 같은 bundle으로 파생 secret과 단계 재개 |
| DB 트랜잭션 후 응답 손실 | 설치 표식과 owner/runtime 인증 확인 후 재사용 |
| 성공 설치 재실행 | credential/volume 보존, 재생성·회전 없음 |
| 파생 secret 일부 누락 | 같은 bundle 값으로 복구; 다른 값이 있으면 중단 |
| bundle 또는 기존 persistence 누락 | 중단; DB 초기화·새 비밀번호로 대체하지 않음 |
| journal 없는 기존 자원/DB/volume | 중단; 내용 보존, 수동 검토 필요 |
| 설치 표식 불일치·미표식 기존 DB·DB 비밀번호 불일치 | 중단; 데이터·role·비밀번호 보존 |
| 다른 installer가 lock 보유 | 즉시 중단 |
| provisioning job 실행/일시정지/상태 불명 | 중단; 기존 작업을 종료하지 않음 |
| 소유된 일회성 job이 created/exited 상태로 남음 | 이름·설치 label·image/user/cmd·정확한 생성 argv 확인 후 불변 ID로 non-force 제거하고 같은 DB/secret으로 재개 |

이미 수동 설치했거나 기존 준비 자원이 있는 namespace에는 자동으로 덮어 설치하지 않습니다. 기존 자원을 보존하면서 새로운 설치 namespace가 필요하면 명시적으로 `--prefix talkabout-auto` 등 프로젝트 prefix를 선택합니다. 각 namespace는 별도의 private journal과 자원 이름을 사용합니다. namespace 변경으로 기존 데이터를 자동 이전하지 않습니다.

DB 준비 뒤 사용자가 비공개 TTY에서 아래를 실행하고, 출력된 일회용 코드를 승인된 loopback/SSH 터널 또는 HTTPS의 GUI에 직접 입력합니다. 관리자 비밀번호는 사용자가 새로 입력·확인하며 DB 비밀번호와 공유하지 않습니다. installer는 code·human·agent token을 생성하지 않습니다.

```sh
podman exec -it talkabout-app python -m talkabout.container setup-code
```

prefix를 선택했다면 container 이름도 해당 prefix로 바꿉니다. [최초 소유자 설정 계약](../../docs/INITIAL_SETUP.md)의 TTL·실패 제한·재시작 잠금·CSRF 복구가 적용됩니다. 실제 `/ready`, 재시작과 보존 확인은 GUI 설정 후 수행합니다. 백업·복원은 [Podman runbook](README.md)의 별도 운영 절차를 따릅니다.

수동 비밀번호 선택이 필요한 기존 설치자는 **명시적으로 수동 경로**를 선택해 `secret_setup.py --create`, owner `init-db`, psql `\password`와 [Podman plan](README.md)을 사용할 수 있습니다. 이것은 기본 자동 installer의 필수 단계가 아닙니다. 자동 installer는 기존 수동 secret을 조용히 채택하거나 회전하지 않습니다.

## English

Run `install.py --install` explicitly on a Linux rootless host with an exact source-SHA image and a chosen free loopback port. Without that flag it does nothing. The default flow generates independent owner/runtime passwords using a CSPRNG, saves one private Podman secret bundle first, and derives mounted password/DSN references from it. No credential is returned or placed in argv, environment, images, Git or logs. Local file-driver storage is access-restricted but does not guarantee encryption; host encryption, backup and revocation remain separate.

A private atomic journal and nonblocking OS lock serialize installation. Fresh network/volume/container resources are installation-labelled. Database role/schema/marker creation is one transaction; later runs authenticate the existing owner and runtime credentials instead of rotating them. Runtime has application-table DML with no DDL/admin capabilities. Owner credentials exist only in the one-off provisioning container; the long-running app receives runtime credentials only.

Partial failures preserve resources and resume from the same bundle. Missing derived references can be recreated with identical values. Missing master secrets/persistence, conflicting references, mismatched DB credentials, unknown existing volumes or an unmarked nonempty database fail closed without deletion, overwrite, rotation or reinitialization. A successful rerun reuses the same credentials and data. Image/config upgrades and data migration are separate procedures. Existing manual/prepared namespaces are preserved; an explicitly selected new project prefix may be used without adopting their data.

The installer starts only its dedicated DB/app; publish is loopback and explicitly selected. It retains rootless non-root/read-only/cap/resource restrictions and changes no existing listener, firewall, public network, TLS, auto-start or host permissions. Installation success means DB configuration and app liveness, not product readiness. Normal APIs and scheduling remain locked until the user runs `setup-code` in a private TTY and chooses their administrator password in the GUI. No setup code, administrator or agent credential is created by installation. Manual `secret_setup.py --create` remains an explicit compatibility option, not a required automatic-install step.

## Sources and verification scope

### First-visit instructions / 최초 접속 안내

The pending KO/EN setup page explains each required field and the operator's private-terminal `setup-code` command. New automatic installs pass their chosen app name through the non-secret `TALKABOUT_APP_CONTAINER` environment variable. Independently configured containers can set the same variable at their next separately authorized deployment; without it the page explicitly asks the operator to replace `APP_CONTAINER`. Never put a server address, code or password in that variable. The server validates the name and returns it only while setup is pending. No browser command execution or code issuance is added. See [initial setup](../../docs/INITIAL_SETUP.md). Existing containers are not changed by this source patch; do not rerun installation to apply a UI update.

최초 설정 전 화면에서 필수 입력값의 설명·예시와 서버 비공개 터미널 명령을 제공합니다. 서버의 설정 완료 상태에 따라 안내를 숨기며, 방문 여부를 브라우저에 저장하지 않습니다. 이 변경은 실제 운영 컨테이너에 아직 배포되지 않았습니다. 기존 코드 기반 최초 설정 정책은 유지합니다.

Podman secret data can be retrieved only by an explicitly requested secret inspection; the installer captures it internally and never forwards it: [Podman secret inspect](https://docs.podman.io/en/latest/markdown/podman-secret-inspect.1.html). Provisioning uses libpq client-side SCRAM password encryption and session-scoped logging suppression: [libpq password encryption](https://www.postgresql.org/docs/current/libpq-misc.html), [PostgreSQL logging](https://www.postgresql.org/docs/current/runtime-config-logging.html). These are implementation choices, not a claim of encrypted Podman file storage.

Tests cover first install, retry without rotation, interruption before/after DB commit, missing secrets/persistence, unknown existing data, mismatched credentials, output masking and concurrent locking. Rootless execution evidence and source/image identifiers are recorded separately in the PR. No actual operating credentials or user setup codes were generated or read during development.

### One-shot interruption recovery / 일회성 작업 중단 복구

컨테이너가 영속화된 뒤 실제 시작 전에 프로세스 중단/재부팅으로 created job이 남을 수 있습니다. installer는 예상 이름·설치 label·image/user/cmd·생성 argv와 config fingerprint를 확인합니다. 기존 버전의 fingerprint 없는 job은 정확한 기존 생성 argv가 일치할 때만 허용합니다. 실행/일시정지 중인 job은 건드리지 않습니다. 확인된 비실행 일회성 job만 immutable container ID로 `container rm`하며 force·volume 삭제 옵션을 사용하지 않습니다. 검사 이후 누군가 job을 시작하면 Podman이 제거를 거절해 설치를 중단합니다. 이름 교체로 다른 컨테이너를 삭제할 수 없고 DB/volume/secret을 삭제하지 않습니다. 확인 실패는 수동 검토를 요구하며 임의 정리하지 않습니다.

A crash between persistent container creation and startup can leave a dormant one-shot job. The installer verifies expected name, installation labels, image/user/cmd and exact create argv/config fingerprint. Legacy jobs without the fingerprint require exact legacy argv. Running/paused/unknown states remain untouched. Only a matching nonrunning job is removed by immutable ID, with no force or volume-removal flag, then provisioning resumes with the same DB and credentials. A concurrent start makes Podman refuse removal; a replaced name cannot redirect removal to another container. Database containers and volumes are never selected for this cleanup.


## 운영 인스턴스(mcp-box) — 재시작·백업·로그·정리

이 절은 저장소의 일반 installer가 아니라 **이미 배포된 프로토타입 인스턴스**(rootless Podman, loopback publish)를 관리하는 절차입니다. 실제 호스트 이름·주소·자격증명은 이 문서에 쓰지 않습니다.

2026-11-01에 이 인스턴스에 `--restart=unless-stopped`·`--log-opt max-size=10m`로 앱/DB를 재생성하고, `podman-restart.service`와 `talkabout-backup.timer`(매일 03:30)·`talkabout-health.timer`(5분)를 설치했습니다. 백업 1회와 프로세스 SIGKILL 자동 재시작을 실제로 확인했습니다.

### 재시작과 감독

- 컨테이너는 `--restart=unless-stopped`로 생성합니다. 수동 `podman stop`은 재시작 정책을 무시하고 그대로 정지합니다.
- rootless 재부팅 자동 복구는 사용자 linger와 `podman-restart.service`가 필요합니다.
  `loginctl show-user "$USER" --property=Linger` → `yes`, `systemctl --user enable --now podman-restart.service`.
- 상태 확인은 `curl -fsS http://127.0.0.1:18081/ready`입니다. `healthcheck.sh`는 5분 타이머로 결과만 기록하며 **자동 재시작하지 않습니다**(DB 단절 뒤 무한 재시작을 피하기 위해 운영자가 판단).

### 백업

- `backup.sh`는 `podman exec <db> pg_dump -Fc`로 덤프하고 sha256을 남기며, 기본 14개를 보존합니다. 암호는 컨테이너 안에서만 쓰고 호스트 인자·환경에 넣지 않습니다.
- 설치: 스크립트를 `%h/bin/`에 두고 `systemd/`의 유닛을 `~/.config/systemd/user/`에 복사한 뒤 `systemctl --user daemon-reload && systemctl --user enable --now talkabout-backup.timer talkabout-health.timer`.
- 복원 리허설은 별도 일회용 PostgreSQL에 `pg_restore`로 수행하고, 실제 운영 DB에 바로 덮어쓰지 않습니다. 리허설은 아직 NOTRUN입니다.

### 로그·정리

- 컨테이너 로그는 `--log-opt max-size=10m --log-opt max-file=3`으로 상한을 둡니다(k8s-file에서 허용됨).
- 이미지/컨테이너 정리는 **TalkAbout 이름·label로 한정**합니다. 롤백용 중지 컨테이너는 최근 1~2개만 남기고, `podman image prune -f`는 dangling만 제거합니다. 다른 프로젝트 자원은 건드리지 않습니다.

### 2026-11-01 사고 교훈(필수)

- `/run/user/1000` tmpfs의 inode가 100%가 되면 이미지 빌드가 `no space left on device`로 실패합니다. 원인은 `libpod/tmp` 아래 누적된 빈 `rootlessport*` 디렉터리였습니다.
- **실행 중 컨테이너가 있을 때 `/run/user/1000/libpod/tmp`를 통째로 삭제하지 마세요.** Podman이 실행 중 컨테이너 추적을 잃고, 컨테이너가 계속 살아 있어도 `podman start/exec`가 실패하며 네트워크 이름 해석도 깨질 수 있습니다.
- 복구 순서: ① `podman inspect`로 대상 컨테이너 설정(이미지 digest·env·secret·volume·cap)을 확보 ② 고아 프로세스를 SIGTERM으로 정상 종료 ③ 같은 named volume·secret으로 DB 컨테이너 재생성 ④ 앱 컨테이너 재생성 ⑤ `/ready`와 행 수 확인. 백업이 최신인지 먼저 확인합니다.
- 컨테이너를 멈추고 런타임 상태를 정리해야 할 때는 `podman stop`으로 정상 중지한 뒤, 실행 중 컨테이너가 없음을 확인하고 정리합니다.
