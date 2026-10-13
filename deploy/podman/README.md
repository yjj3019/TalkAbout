# Podman package / 배포 패키지

**PACKAGING PREPARED / IMAGE BUILD AND DEPLOYMENT NOTRUN — 2026-10-13**

이 패키지는 두 개의 rootless container와 전용 internal network를 제안합니다. app은 UID/GID 10001, PostgreSQL은 공식 Bookworm image의 postgres UID/GID 999로 실행합니다. host에는 app의 127.0.0.1:8080만 publish하고 DB port는 publish하지 않습니다. image 내부 app bind는 0.0.0.0이며 host namespace/공개 bind가 아닙니다. 앱 non-root·read-only filesystem, cap-drop all, no-new-privileges, PID/CPU/memory/tmpfs/log 상한을 적용합니다. rootless cgroup v2 지원을 실제 target에서 확인해야 하며 제한이 거절되면 flag를 제거하지 말고 중단합니다. 전역 설치/기존 container·volume·network 변경은 하지 않습니다.

## Verified metadata and NOTRUN

[images.lock.json](images.lock.json)은 공식 registry manifest bytes와 Docker-Content-Digest를 SHA-256으로 대조한 결과입니다. Python 3.14.8-slim-bookworm과 PostgreSQL 18.6-bookworm의 multi-platform index digest를 기록했습니다. 이는 image pull/build/run 성공이나 vulnerability 검사를 뜻하지 않습니다. source/reference는 [Python Official Image](https://hub.docker.com/_/python), [Postgres Official Image](https://hub.docker.com/_/postgres), [Podman run](https://docs.podman.io/en/latest/markdown/podman-run.1.html), [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)입니다. tag 대신 기록된 digest를 유지하고 아키텍처를 target와 대조합니다.

현재 local Podman은 미설치입니다. 승인된 remote 신뢰 경로의 읽기 점검은 가능해졌지만, 프로젝트 전용 build/runtime 준비는 아직 실행하지 않았습니다. image build, Linux dependency install, rootless resource limits, volume ownership, image healthcheck, 실제 백업/복원, Quadlet generation은 **NOTRUN**입니다. Python secret/config 계약·static argv/Containerfile/Quadlet 검사와 별도의 합성 local PostgreSQL 시험만 실제 시행합니다. 실제 browser/AI/product MCP도 NOTRUN입니다. 운영 credential은 만들지 않았습니다.

## Before deployment

승인된 사용자 경로로 신뢰된 SSH host key가 검증된 뒤 OS/architecture, Podman version/rootless/cgroup v2, 8080 충돌, TalkAbout 전용 경로와 resources를 읽기 확인합니다. 사용자 예전 수락 로그만으로 신규/변경 key를 자동 수락하지 않습니다. Podman이 없으면 전역 설치를 임의 실행하지 않습니다. dedicated path, DB/계정/password/secret 보존, backup 책임을 정확히 승인받기 전 운영 컨테이너를 시작하지 않습니다. 공개 네트워크/TLS/방화벽/새 사용자/권한 확대/자동기동/linger는 별도 승인 사항입니다.

```sh
podman --version
podman info --format '{{.Host.Security.Rootless}} {{.Host.CgroupsVersion}}'
```

운영자는 대상에서 rootless=true와 cgroup v2를 확인해야 합니다. 기존 자원명을 덮어쓰지 않습니다. 아래 각 exists가 true이면 fresh install을 중단합니다. 재사용하려면 해당 프로젝트 label과 owner를 검증한 명시적 upgrade 계획이 필요합니다. prune/reset/replace는 제공하지 않습니다.

```sh
podman container exists talkabout-app
podman container exists talkabout-db
podman network exists talkabout-private
podman volume exists talkabout-pg18-data
```

## Build and inspect

검증된 source commit을 SOURCE_SHA로 기록합니다. 공식 pinned base와 runtime requirements snapshot을 사용합니다. 테스트·Git·문서·secret·로컬 환경은 deny-by-default .containerignore와 명시 COPY로 context에서 제외합니다. image에는 credentials/ARG secret이 없으며 runtime secret은 file mount로만 받습니다.

```sh
podman build --file Containerfile --tag "localhost/talkabout:$SOURCE_SHA" .
podman image inspect "localhost/talkabout:$SOURCE_SHA"
python deploy/podman/plan.py --app-image "localhost/talkabout:$SOURCE_SHA"
```

plan.py는 JSON argv를 출력할 뿐 Podman을 호출하거나 자원을 만들지 않습니다. build 이후 app image ID/digest·base digests·source SHA·dependency 결과를 release manifest에 별도 기록합니다. Linux dependency compatibility와 포함 schema/web resources 확인 후에만 run합니다. 실패 시 snapshot을 임의 느슨하게 풀거나 설치 성공으로 표시하지 않습니다.

기본 host port가 이미 사용 중이면 중단하고 승인된 빈 unprivileged port를 --host-port로 지정합니다. 이 옵션은 127.0.0.1 bind를 유지하며 1024..65535만 허용합니다. Quadlet PublishPort와 사용자가 접속하는 localhost origin/port도 같은 값으로 검토합니다. 기존 listener를 종료하거나 교체하지 않습니다.

## Secrets, database allocation and bootstrap

Podman secret은 image/env/argv의 password 값 대신 file을 mount합니다. 값이 없는 reference names만 저장소에 포함됩니다. 별도 승인 후 사용자가 외부 private file/stdin으로 `podman secret create`를 직접 수행합니다. 실제 값을 command, shell history, repo, 채팅 또는 로그에 쓰지 않습니다. Podman secret store의 보존/백업/암호화 보장은 host 정책으로 별도 검토합니다.

필요한 secret refs는 `talkabout-db-password`(DB owner 초기 password), `talkabout-db-init-url`(schema 초기화에만 쓰는 owner DSN), `talkabout-db-url`(runtime 최소 권한 DSN)입니다. URL secret의 host는 정확히 talkabout-db, port 5432, dbname talkabout입니다. init URL user는 talkabout_owner, runtime URL user는 talkabout_app입니다. 값 자체는 예제로 하드코딩하지 않습니다. file 내용은 4096 bytes 이하이며 잘못된 내용·경로·host·port·role은 비밀 없는 오류로 거절합니다. 일반 CLI의 loopback-only DB 계약은 바뀌지 않습니다.

plan의 named volume은 PostgreSQL 18 공식 mount 위치 /var/lib/postgresql에만 연결합니다. :U는 그 **전용 named volume**의 rootless UID ownership을 조정하며 기존 공유 경로나 데이터에 쓰면 안 됩니다. PGDATA는 공식 image의 /var/lib/postgresql/18/docker를 따릅니다. DB writable 범위는 그 volume 및 제한된 /tmp와 /var/run/postgresql tmpfs입니다. server startup은 기존 volume의 password/role을 재설정하지 않습니다.

새 DB container는 공식 image가 승인된 새 volume에 DB owner를 초기화합니다. 이는 지속 운영 변경이므로 실제로 실행하지 않았습니다. DB healthy 이후 app image의 `init-db`를 **일회성**으로 실행하되 owner URL secret을 target=talkabout-db-url,uid=10001,gid=10001,mode=0400으로 mount합니다. 이 명령은 schema만 초기화하며 app serve는 이를 자동 실행하지 않습니다. 임시 container의 network/read-only/non-root/resource flags는 plan의 app와 같고 publish/자동기동은 없습니다.

그 다음 승인된 DB owner 세션에서 runtime role을 만들고 interactive `\password talkabout_app`으로 password를 사용자가 설정합니다. 다음 SQL은 password 값이 없으며 이 저장소에서는 실행하지 않습니다.

```sql
CREATE ROLE talkabout_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE talkabout TO talkabout_app;
GRANT USAGE ON SCHEMA public TO talkabout_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO talkabout_app;
```

이미 존재하는 role이면 CREATE를 반복하지 않습니다. schema 추가/변경에는 owner만 사용하고 runtime DML grant를 다시 검토합니다. approved runtime URL secret을 mount한 `preflight`가 통과한 뒤 app를 실행합니다. human account 초기화는 별도 승인 후 사용자가 동일 app image의 `bootstrap-human --workspace ... --name ... --handle ...`을 interactive TTY로 직접 실행합니다. 비밀번호는 getpass로만 받고 agent token은 여기서 발급하지 않습니다. 초기화 후 불필요한 owner init URL secret 노출을 제거하는 작업은 별도 승인된 비밀 보존 정책에 따릅니다.

## Run, health and shutdown

전용 network/volume과 DB/app argv는 plan.json 출력에서 검토합니다. 자동 실행 기능은 없습니다. DB health(pg_isready)는 schema 준비를 보장하지 않으며 app preflight/readiness를 추가 확인합니다. healthcheck는 /ready만 probe하고 proxy env를 무시합니다. localhost HTTP health·ready·미인증 API 401을 확인하고 실제 app authentication/browser 동작은 별도 테스트합니다. 첫 성공을 실제 AI 연결 성공으로 표시하지 않습니다.

```sh
podman healthcheck run talkabout-db
podman healthcheck run talkabout-app
curl --fail http://127.0.0.1:8080/ready
podman stop --time 10 talkabout-app talkabout-db
```

외부 접근을 위해 bind를 바꾸거나 host network/privileged/socket mount를 추가하지 않습니다. stop은 TalkAbout 전용 container만 지정하며 volume/secret/network를 삭제하지 않습니다.

## Backup, restore and upgrade

승인된 private backup directory에서 umask 077로 logical custom-format dump를 저장하고 SHA-256·PG major·source SHA·schema revision·timestamp를 기록합니다. app을 pause/stop하여 일관된 인계 시점을 정의하고 running PGDATA를 파일 복사하지 않습니다. 모든 backup은 운영 데이터를 포함할 수 있어 repo/Library/채팅에 올리지 않습니다.

```sh
umask 077
podman exec --user 999:999 talkabout-db pg_dump -U talkabout_owner -d talkabout -Fc > "$BACKUP_FILE"
sha256sum "$BACKUP_FILE"
```

이 dump는 role/password를 포함하는 globals backup이 아니므로 owner/runtime role·grants·secrets를 별도 승인 절차로 복구해야 합니다. restore 시험은 새 TalkAbout 전용 이름·새 volume과 승인된 임시 credentials에서 `pg_restore --exit-on-error --no-owner`를 사용하고 원본 volume을 덮어쓰지 않습니다. DB major는 18로 유지하며 major upgrade에 기존 volume을 바로 연결하지 않습니다. restore 성공·ACL/readiness·데이터 검증 후에만 cutover합니다. 현재 백업/복원은 NOTRUN입니다.

upgrade는 이전 app image/manifest와 검증된 backup을 보존하고 TalkAbout app만 교체합니다. rollback은 이전 immutable app image를 같은 승인 DB 계약에 다시 실행하며 데이터·schema를 역삭제하지 않습니다. schema migration rollback 가능성이 없으면 사전 restore/cutover 계획이 필요합니다. 이전 image/container/volume cleanup은 자동화하지 않습니다.

## Quadlet / English scope

[quadlet](quadlet) files end in .example and do not activate automatically. They contain no Install/WantedBy section and use Restart=no. Before any copy to the user's Quadlet directory, approve persistent configuration, verify source-tag substitution, installed Podman/Quadlet syntax, labels, secrets and resource names, then dry-run the target generator. Do not enable services or login linger without approval. Dependencies order startup only; they do not prove database schema health.

This package provides pinned official base metadata, a non-root secret-file entrypoint, separate app/PostgreSQL resources, private networking, named persistence, resource limits and manual bootstrap/backup/rollback contracts. It does not perform deployment, credential issuance, auto-start or actual AI calls. Image build/run, Linux dependency resolution, rootless/SELinux/volume behavior, backup restore and Quadlet generation remain NOTRUN until a trusted target/runtime is available. See [deployment runbook](../../docs/DEPLOYMENT_RUNBOOK.md) and [feature matrix](../../docs/FEATURE_MATRIX.md).
