# Initial owner setup / 최초 소유자 설정

LOCAL IMPLEMENTATION / NOT DEPLOYED — 2026-10-14

## 한국어

공통 기본 비밀번호는 없습니다. DB 준비 후 운영자가 비공개 interactive TTY에서 `talkabout setup-code` 또는 전용 one-off container의 `setup-code`를 실행합니다. 실제 코드는 argv·URL·문서·로그·채팅에 넣지 않습니다. stdin/stdout/stderr가 모두 TTY일 때만 코드가 표시됩니다. serve는 코드·계정·DB 비밀번호를 생성하지 않습니다.

코드는 설치별 32바이트 CSPRNG 난수입니다. DB에는 해시, 만료 시각, 실패 횟수만 저장합니다. TTL은 15분이며 코드 불일치 5회 뒤 잠깁니다. 형식·Origin·CSRF 오류는 코드 비교 전에 거절합니다. 만료/시도 제한은 재시작 후에도 유지됩니다. 운영자가 같은 전용 터미널 명령을 명시적으로 다시 실행하면 만료되거나 제한에 도달한 코드만 교체할 수 있습니다. 활성 코드는 교체하지 않으며, 소유자가 설정된 설치는 재발급/재설정할 수 없습니다.

사용자는 loopback/승인된 SSH 터널 또는 HTTPS로 KO/EN 초기 화면에 접속해 코드, workspace, 표시 이름, handle, 새 비밀번호와 확인을 직접 입력합니다. 비밀번호는 12~1024자이며 기존 PBKDF2 해시 형식을 사용합니다. 입력 뒤 코드/비밀번호 필드를 비우고, 완료 후 기존 로그인 화면으로 이동합니다. 코드는 관리자 세션이나 에이전트 토큰으로 사용할 수 없습니다. 자동 로그인하지 않습니다.

설정 전에는 `/`, 허용된 세 개 정적 파일, 최소 `/health`, 아래 설정 endpoint만 접근 가능합니다. `/ready`를 포함한 나머지 API·SSE·다운로드는 503 `setup_required`이며 scheduler는 작업을 진행하지 않습니다. 숨겨진 화면만으로 차단하지 않습니다. 준비 전 컨테이너의 `/ready` 기반 healthcheck는 실패하는 것이 정상이며, 자동 재시작 정책을 설정하지 않습니다. 설정 완료 후에만 운영 readiness를 확인합니다.

설치 단위 PostgreSQL advisory transaction lock과 singleton 상태 행으로 동시 제출을 직렬화합니다. 코드 소비·소유자·비밀번호 해시·완료 상태가 같은 트랜잭션에서 저장됩니다. 실패한 코드의 횟수는 오류 응답 전에 커밋합니다. 완료 시 코드 해시를 제거합니다. 기존 소유자가 있는 설치는 신규 테이블이 없어도 기존 인증과 scheduler가 유지되며 GUI setup은 거절됩니다. 기존 소유자와 비밀번호를 바꾸지 않습니다. `bootstrap-human`은 기존 수동 초기화 경로로 남지만 GUI용 코드가 발급되면 우회할 수 없습니다.

DB owner/runtime 비밀번호는 관리자 로그인 비밀번호와 별개입니다. 기본 DB 설치는 [자동 installer](../deploy/podman/OPERATING.md)가 설치마다 별도 비밀번호를 생성·보관합니다. `secret_setup.py --create`의 사용자 직접 입력은 명시적 수동 호환 경로입니다. DB schema 초기화/추가는 owner 전용 일회성 작업입니다. 새 `installation_setup` 테이블에도 runtime role의 SELECT/INSERT/UPDATE/DELETE만 부여합니다. runtime은 DDL·superuser·role 생성 권한이 필요하지 않으며, 앱에 Podman 소켓/owner secret을 주지 않습니다. 기존 DB 업그레이드는 승인된 백업 후 owner `init-db`, 새 테이블 권한 부여를 명시적으로 수행합니다. 신규 테이블이 없는 미설정 DB는 잠긴 상태로 실패합니다.

운영자 코드 분실/만료/시도 소진은 비공개 터미널 재발급으로 처리합니다. 이미 설정한 소유자 비밀번호의 분실 복구·변경 기능은 이 초기 설정의 범위가 아닙니다. DB row 삭제나 기존 볼륨 초기화로 재설정하지 않습니다. 코드의 악의적인 실패 제출에 따른 잠금은 운영자 재발급이 필요할 수 있습니다.

## API contract

| Method/path | Access and result |
| --- | --- |
| GET `/api/v1/setup/status` | Only `setup_required` boolean; no code, identity or DB metadata |
| GET `/api/v1/setup/csrf` | Before setup; random nonce in JSON plus host-only HttpOnly SameSite=Strict cookie, 15 minutes; Secure on HTTPS |
| POST `/api/v1/setup/complete` | Same Origin + matching `X-CSRF-Token`/cookie; JSON fields `code`, `workspace_id`, `display_name`, `username`, `password`, `confirmation` |

Success returns `status=complete`, `login_required=true` and removes the setup CSRF cookie. No login session or agent credential is issued. Invalid code: 403; expired/unissued: 403; exhausted: 429; completed: 409; ordinary endpoints while locked: 503. Never supply the code in query strings. Existing Host/Origin validation, CSP and no-store headers apply. Deployment must keep access logs and request-body logging disabled.

## English

There is no shared default password. After the database/schema and least-privileged runtime role are prepared, the operator explicitly runs `setup-code` in a private interactive terminal. Only a hash of the installation-specific random code is persisted; the code is shown exclusively to that terminal. It expires in 15 minutes and locks after five incorrect code submissions. Expiry and attempts survive restart. Only an expired/exhausted pending code can be reissued by the operator; completed installations cannot be reset.

The user enters the code and chooses/confirms their administrator password in the KO/EN GUI through loopback, an approved SSH tunnel or HTTPS. Successful setup returns to normal login, preserving existing session/CSRF behavior and subsequent authenticated agent registration/invitation flows. The installation code grants no session, room membership or agent credential. Origin checking and a setup CSRF cookie/header protect submission; fields are cleared after submission and no credential is placed in a URL.

Before completion the server blocks ordinary APIs, SSE, downloads and scheduling; only initial UI/assets, minimal liveness and the setup endpoints above remain available. `/ready` is deliberately unavailable until setup completes. Existing owned installations retain authentication even before the additive schema migration, and cannot use setup to reset their owner. A PostgreSQL installation-wide lock and one transaction enforce single consumption with atomic owner/password/completion storage. Failure counters commit separately from the returned error.

Database credentials remain independent: the default installer generates/stores them per installation; the existing user-input secret helper is an explicit manual compatibility path. Owner-only schema initialization/migration precedes runtime DML grants on the new table. No DDL or host/container-management privilege is added to the application. Password recovery after setup is a separate feature; never erase data to reopen bootstrap.

## Verification scope

The GUI refreshes setup status and CSRF immediately before each submission. A failed POST rechecks state/CSRF without automatically resubmitting. Expired cookies, operator code reissue and another tab changing the cookie therefore require no page reload to obtain a fresh nonce. Code/password fields and the temporary submitted values are cleared; the user explicitly re-enters them after failure. If another request completed setup, the form closes and normal login remains available. UI logic tests cover expired/replaced cookies, a cookie race and completion before/during submission; actual browser DOM verification remains separate.

GUI는 매 제출 직전 설정 상태와 CSRF를 갱신합니다. 실패한 POST는 자동 재전송하지 않고 상태/CSRF만 다시 확인합니다. 코드·비밀번호 입력과 임시 제출 값을 비우고 사용자 직접 재입력을 안내합니다. 만료 후 코드 재발급·다중 탭 쿠키 교체에도 새 nonce를 받으며, 다른 요청이 설정을 완료했다면 설정 화면을 닫고 기존 로그인을 제공합니다.

Synthetic tests cover API lock/Origin/CSRF, expiry/reissue, terminal-only issuance, scheduler pause, existing authentication, durable attempts across store recreation, simultaneous setup, transaction rollback and a runtime role denied DDL. These are local/isolated database tests, not operating deployment or browser DOM verification. No actual operating code, password or agent credential was generated.
