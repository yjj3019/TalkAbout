# Remaining verification / 남은 검증 재현 절차

LOCAL TEST DOCUMENTATION ONLY / 로컬 검증 문서. Reviewed 2026-10-19. No publication, merge, deployment, actual model invocation or operating credential issuance is implied. Application code is unchanged from `9f68f602cc70b80224991c9c18c846b6ed8c434f`.

## PostgreSQL fixture

Existing PostgreSQL 18.6 test binaries can be reused without installation. A new disposable cluster, owned data directory and fresh loopback port are required; never reuse a running user service or an existing data directory. Verify `SHOW data_directory` equals the newly owned resolved path and `SHOW listen_addresses` is exactly `127.0.0.1` before executing tests. If startup exits or ownership differs, stop and report failure without trying another existing database.

`--postgres-config` is the existing pytest fixture interface, not an application API. Its private local JSON requires `ephemeral_fixture: true`, `host: "127.0.0.1"`, the fresh port, fixture user/password and owned `data_directory`. Values must describe the cluster created for that run; do not place operating DSNs or secrets in the repository or chat. Fixture roles and synthetic test accounts exist only inside that disposable cluster. They are not operating credentials or real external-agent registrations.

With installed Python test dependencies, this selection reproduces the PostgreSQL cases without repeating the whole Python/browser suite:

```text
python -m pytest --collect-only -q tests -k postgres
python -m pytest -q -ra tests -k postgres --postgres-config fixture.local.json --junitxml=postgres-results.xml
```

Review the collected selection first; on the tested revision it selected exactly 17 cases and deselected 509, exit 0. The exact seventeen node IDs from this run are in the [PG validation record](validation/remaining-postgres-review-2026-10-19.json). Use an account-file/process guard for Python fixtures, no provider executables or credentials, and loopback-only network access. Existing pytest fixtures check the server data path and use separate synthetic schemas. Preserve the JUnit, startup log, server log and ownership/exit record. Stop only the owned test/server process groups and confirm descendant exit. Keeping the stopped data directory for evidence is acceptable; never recursively delete a computed path without checking its resolved owner boundary.

신규 설치나 운영 DB 연결 없이 기존 테스트 바이너리와 새 임시 데이터 디렉터리만 사용합니다. readiness 검증도 임시 loopback API의 합성 smoke test이며 운영 배포를 뜻하지 않습니다. 전체 508-pass 실행과 이번 17개 선별 실행은 서로 다른 실행입니다. 합산 수치를 하나의 전체 실행 결과로 표시하지 않습니다.

## POSIX fail-closed case

The remaining case is `tests/test_agent_runtime.py::test_posix_real_launch_fails_closed`. Its expected outcome is refusal with `runtime_containment_unsupported`; it does not establish POSIX runtime support. Windows execution intentionally skips it. Changing `os.name` on Windows is not equivalent evidence.

Required before execution: an explicitly approved disposable POSIX test workspace, Python 3.12+ with the existing test dependencies available offline, an isolated copy of the tested source and no access to provider accounts, operating data or unrelated project directories. No host mount, remote access, package installation or personal WSL distribution startup is authorized by this procedure. WSL launchers/distribution names being present does not establish those prerequisites. Do not probe personal Linux files to fill the gap.

Once those prerequisites are independently satisfied, the narrow reproduction is:

```text
python -m pytest -q tests/test_agent_runtime.py::test_posix_real_launch_fails_closed -p no:cacheprovider
```

Run under a guard rejecting provider processes, account-file reads and external network connections. The test uses the test Python executable as its executable argument; no real Codex binary or login is needed. Record OS, Python version, tested commit, terminal exit and cleanup evidence. Until then report **NOTRUN**, with the missing dedicated POSIX fixture as the blocker.

## Release gates remain separate / 배포 전 경계

- Synthetic PostgreSQL concurrency/readiness and Chrome success do not validate the selected operating environment, backups/restoration, secrets/TLS, storage quotas or long-running operation. Deployment still requires its own approved isolated target and acceptance evidence.
- Verified stable provider account identity remains unavailable. Keep automatic conversation resume blocked; no email/home/account-label substitute or journal deletion bypass is acceptable.
- Actual registration → acceptance → connection test → first reply → follow-up → restart restoration and the multi-runtime PoC remain unverified. Their provider calls, billing boundary and actual credential issuance require separate authorization.
- Remote push/PR attempts previously rejected must not be retried or routed through another tool as part of local verification.

See [continuity readiness](CODEX_CONTINUITY_REVIEW.md), [deployment preparation](DEPLOYMENT_RUNBOOK.md) and [release gap matrix](RELEASE_GAP_MATRIX.md). Earlier NOTRUN records are historical evidence; later isolated runs do not rewrite them.
