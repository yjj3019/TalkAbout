# TalkAbout deployment preparation

The [Podman package](../deploy/podman/README.md) defines a separate private-container contract: non-root app, mounted DB URL secret, fixed private DB alias, separated PostgreSQL persistence, and host loopback-only publish. It does not enable services or issue operating credentials. The original host CLI below remains loopback-only.

**LOCAL P0 PROTOTYPE / NOT DEPLOYED — Reviewed: 2026-10-13**

Git push/PR/merge are authorized. A merged repository is separate from a running service. The deployment target is selected privately; its unique existing connection, host key, dedicated path, runtime and DB must be verified before copying or starting anything. Do not use another application's directory/server/database as a fallback.

## 한국어

현재 패키지는 loopback text-only API·KO/EN GUI·pull worker·최소 stdio MCP입니다. 실제 AI/runtime, browser DOM/SSE, 제품 MCP client, MFA/복구/분산 rate limit, 운영 보존·archival·총 disk quota는 미검증 또는 미구현입니다. [기능표](FEATURE_MATRIX.md)를 배포 성공으로 바꾸지 않습니다. 프로젝트는 사용자 지정 MIT이며 제3자 라이선스와 고지는 보존합니다. 운영 human/agent credential과 실제 AI 호출은 이번 검증에서 만들거나 실행하지 않습니다.

필요한 추가 결정/승인은 다음과 같습니다.

1. TalkAbout 전용 절대 경로, 실행 사용자와 Python>=3.12 환경. 기존 앱 경로나 service account를 재사용한다고 가정하지 않습니다.
2. 서버 loopback·사설 접근·공개 domain/TLS 중 접근 범위. 현재 serve는 127.0.0.1:8080만 bind합니다. port 충돌 시 다른 process를 종료하지 않습니다.
3. 전용 PostgreSQL DB/schema, 백업/복구 책임과 secret 전달. DB 생성/권한 부여·영구 password·실제 human/agent credential은 별도 승인 전 실행하지 않습니다.
4. 공개 포트·방화벽·TLS·전역 패키지·새 서비스 계정·권한 확대·자동기동·지속 설정·새 비용은 정확한 변경과 위험을 보고해 승인받은 뒤 적용합니다. 이 runbook은 이를 자동 설치하지 않습니다.

## Packaging

wheel에는 schema.sql과 web assets가 포함됩니다. 빌드용 dependency는 pip의 격리 환경에 설치합니다. 전역 Python이나 다른 앱 venv는 변경하지 않습니다.

```sh
python -m pip wheel . --no-deps --wheel-dir <RELEASE_DIR>
```

대상 서버 OS/Python/경로 확인 후 승인된 전용 directory 안에서만 private venv를 준비합니다. requirements.lock.txt는 검증한 Windows 환경의 exact snapshot이며 Linux 설치 성공을 보장하는 cross-platform lock이 아닙니다. remote 환경의 dependency resolution/pip check와 aggregate는 별도 검증해야 합니다. source remote main SHA와 wheel SHA-256을 기록하고 credentials를 repository에 넣지 않습니다.

## Configuration and health

TALKABOUT_DATABASE_URL은 별도 환경 전달로 설정합니다. command argument, 문서, URL, 채팅, 로그에 넣지 않습니다. CLI는 명시적인 loopback host(localhost/127.0.0.1/::1), user, dbname과 유효 port를 요구하고 connect_timeout=5를 강제합니다. 오류는 DSN을 표시하지 않습니다. remote/multi-host DB와 외부 bind는 현재 지원 계약에 없습니다.

```sh
talkabout preflight
talkabout serve
```

preflight는 설정과 기존 DB/schema를 읽기 검사하며 table이나 계정을 만들지 않습니다. 준비되지 않으면 exit 1입니다. init-db/bootstrap-human은 별도 operator action이며 자동 startup에서 실행하지 않습니다. 운영 DB/계정 생성 승인 전에는 호출하지 않습니다.

serve는 foreground에서 127.0.0.1:8080에 bind하고 concurrency 16, graceful shutdown 10초 상한을 사용합니다. service manager·자동기동 설정은 없습니다. GET /health는 prototype과 process/scheduler 상태를 제공합니다. GET /ready는 scheduler가 running이고 필요한 PostgreSQL schema/columns가 확인된 경우에만 200이며 DB outage·불완전 schema·scheduler failure·MemoryStore fixture는 503입니다. 응답에는 DSN/토큰/계정 정보가 없습니다. readiness는 실제 AI/browser 품질이나 운영 안전성 전체를 보장하지 않습니다.

## Private verification and rollback

현재 검증은 합성 임시 DB/schema·임시 loopback port·합성 ephemeral credential만 사용합니다. 실제 HTTP /health·/ready·미인증 API 401을 확인한 뒤 process와 DB를 정지합니다. 이는 검증 실행이며 실제 사용자 서비스 배포 완료가 아닙니다. static demo로 실제 배포를 대체하지 않습니다.

후속 배포는 승인된 전용 경로의 release manifest와 이전 release를 보존합니다. rollback은 TalkAbout process만 정지해 이전 검증 release로 교체하고 DB migration을 역실행하거나 데이터를 삭제하지 않습니다. schema 변경에는 별도 backup/restore 검증이 필요합니다. 기존 앱·DB·서비스를 수정/재시작하지 않습니다. 새/변경 host key 경고는 우회하지 않습니다.

## English

This is deployment preparation for a private prototype. Verify the approved private connection and host key before use. The dedicated app path/runtime identity, access scope, PostgreSQL allocation/backup/secrets and persistent security settings require precise decisions. Public ports/firewall/TLS, global packages, service accounts, permanent credentials, auto-start and new costs require additional approval. Real AI calls are not implied by deploying code.

Build the wheel in isolation and use an app-private venv. Record source SHA and wheel hash. The Windows dependency snapshot is not a Linux compatibility claim. CLI validates loopback PostgreSQL settings with a bounded connect timeout; preflight is read-only and serve never initializes schema/accounts automatically. /ready checks scheduler and PostgreSQL schema; /health reports process state. Fixture readiness cannot be mistaken for a production DB.

Actual browser/SSE, external MCP product clients and the real-runtime PoC remain NOTRUN. No public address or running service is promised until target compatibility, allocation and required approvals are resolved. Repository merge, synthetic HTTP smoke tests and deployment are separate outcomes.
