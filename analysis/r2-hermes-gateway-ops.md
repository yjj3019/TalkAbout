# r2 — hermes-agent 게이트웨이·운영·비밀관리 분석

**대상**: `hermes-agent/` (NousResearch, HEAD `930e5ed`, 2026-10-25) · **MIT** (`hermes-agent/LICENSE:1-2`, "Copyright (c) 2025 Nous Research") · 21,220파일
**범위**: `SECURITY.md`, `AGENTS.md`(루트), `hermes_dir_acl.py`, `hermes_cli/_secrets_common.py`, `gateway/authz_mixin.py`, `gateway/bot_loop_guard.py`, `gateway/code_skew.py`, `gateway/control_socket.py`, `tools/environments/local.py`+`local_env_policy.py`, `agent/vault_store.py`, `plugins/plugin_loader.py`, `plugin-catalog/README.md`, `Dockerfile`, `docker-compose.yml`, `docker/stage2-hook.sh`, `docker/main-wrapper.sh`, `locales/`, 각 디렉터리 `AGENTS.md`
**대응**: `talkabout/{auth,identity,workers}.py`, `talkabout/adapters.py`, `talkabout/processes.py`, `docs/DEPLOYMENT_RUNBOOK.md`, `docs/PERMISSIONS.md`, `deploy/podman/*`, `web/state.mjs`

표기: `[채택]` 바로 쓸 수 있음 / `[차용]` 개념만 / `[대조]` TalkAbout이 이미 우위 / `[제외]` 맞지 않음(이유 필수).
비용: **S**(≤1일) / **M**(2~5일) / **L**(1주+). **최적 저장소**는 hermes/orca/opencodex 중 택1.
"미확인"은 읽지 않았거나 확인하지 못한 것이다. 추정으로 메우지 않았다.

---

## 0. 결론 요약 — 비밀관리 판정

**판정: hermes의 권한 모델은 TalkAbout보다 약하고, 파일·프로세스 경계는 TalkAbout보다 낫다. 한 축으로 "상위호환"이라 부를 수 없다.**

1. **권한은 `[대조]`** — hermes는 단일 운영자·단일 테넌트 전제이며, 인증된 집합 **내부**는 전부 동등하다고 명시한다: `SECURITY.md:214-217` "Within the authorized set, all callers are equally trusted. Hermes Agent does not model per-caller capabilities inside a single adapter. Operators who need capability separation should run separate agent instances with separate allowlists." TalkAbout의 `credential scope ∩ membership ∩ account scope` 3중 교집합과 **범주가 다르다**. per-caller capability 부재를 hermes가 스스로 인정한 셈이다.
2. **비밀 저장은 `[채택]` 3건** — ① Windows 상승 프로세스 DACL 함정(`hermes_dir_acl.py`, 462줄) ② 자식 프로세스 env 2단 scrub + fail-closed(`tools/environments/local_env_policy.py:15-60`, `local.py:262-411`) ③ 모델에 값을 주지 않는 **opaque handle vault**(`agent/vault_store.py:1-17`). 셋 다 TalkAbout이 0~부분만 가진 것이다.
3. **비밀 마스킹(로그)은 `[대조]`** — hermes는 출력 redaction을 **경계가 아닌 휴리스틱**으로 규정한다(`SECURITY.md:147-148` "Output redaction strips secret-like patterns from display. A motivated output producer will defeat it"). 이는 1라운드의 "Orca는 프롬프트 마스킹을 하지 않는다" 판정을 **모순 없이 강화**한다. 그리고 TalkAbout의 stderr 허용목록(`agent_runtime.py:62-97`)은 이 기준에서도 hermes보다 엄격하다.
4. **운영 배포는 `[차용]`이 본체** — Dockerfile/Dockerfile의 컨테이너 하드닝은 TalkAbout `deploy/podman/`이 이미 같은 수준(rootless·read-only fs·cap-drop·digest pin)이므로 `[대조]`. 실제 이식 가치는 **함정 카탈로그**(§7)와 **백업 선택 규칙**(`hermes_cli/backup.py`)이다.
5. **다채널 게이트웨이는 `[제외]`** — Telegram/Discord/Slack/WhatsApp/Signal 어댑터는 TalkAbout 도메인이 아니다. 인가 구조(`authz_mixin.py` 758줄) 중 이식 가능한 것은 **규칙 3개**뿐이다(§5).

### 이번 라운드에서 TalkAbout이 실제로 얻는 것 (순위)

| 순위 | 항목 | 태그 | 비용 | 최적 저장소 |
| --- | --- | --- | --- | --- |
| 1 | SQLite WAL-reset 버그 확인 + journal 백업 규칙 | `[채택]` | S | **hermes** |
| 2 | Windows 디렉터리 ACL 함정(`hermes_dir_acl.py`) | `[채택]` | M | **hermes** |
| 3 | 자식 프로세스 env 2단 scrub + fail-closed | `[채택]` | M | **hermes** |
| 4 | 운영 함정 카탈로그 → `DEPLOYMENT_RUNBOOK.md` | `[채택]` | S | **hermes** |
| 5 | 도구/MCP 카탈로그 신뢰 규칙(SHA pin·provenance 비신뢰·credential 경유) | `[차용]` | M | **hermes** |
| 6 | 백업 선택·잠금·원자 게시·복원 epoch | `[차용]` | M | **hermes** |
| 7 | 검증 후 저장(rotate_token) | `[채택]` | S | **hermes** |
| 8 | 코드 스큐 감지 | `[차용]` | S | **hermes** |
| 9 | locale key manifest + parity | `[대조]` | — | hermes |
| 10 | 다채널 인가 / MFA / 분산 rate limit | `[제외]` | — | 없음 |

---

## 1. 운영 배포 (질문 1)

### 1.1 컨테이너 하드닝 — TalkAbout이 이미 동등 `[대조]`

| 항목 | hermes 근거 | TalkAbout 현황 |
| --- | --- | --- |
| base digest pin | `Dockerfile:8` `FROM debian:trixie-20261020@sha256:4c25…` + `:5-6` 주석 "a tag alone would let the base drift under them" | `deploy/podman/images.lock.json` + `README.md:9` "image pull/build/run 성공이나 vulnerability 검사를 뜻하지 않음" |
| non-root | `Dockerfile:176` `useradd -u 10000 -m -d /opt/data hermes` | `deploy/podman/README.md:5` "app은 UID/GID 10001 … non-root·read-only filesystem, cap-drop all" |
| 앱 트리 불변 / 상태 분리 | `Dockerfile:50-53` "Do not write .pyc files at runtime: `/opt/hermes` is immutable" + `PYTHONDONTWRITEBYTECODE=1`, `/opt/data` 볼륨 | `README.md:5` read-only filesystem |
| checksum 검증 | `Dockerfile:22` `sha256sum -c /tmp/sqlite.sha256`, `:134-157` s6-overlay 3종 tarball sha256 | `images.lock.json` manifest digest |

→ 하드닝 자체를 hermes에서 가져올 이유가 없다. **다른 것은 "왜 그렇게 했는가"의 기록 밀도**다(§7).

### 1.2 hermes가 실제로 자동화하는 것

- **PID 1 = s6-overlay** (`Dockerfile:119-121`, `:544` `ENTRYPOINT ["/opt/hermes/docker/entrypoint-dispatch.sh"]`). 이유가 명시적이다: `Dockerfile:65-70` 주석 "tini was previously PID 1 to reap orphaned zombie processes (**MCP stdio subprocesses**, git, bun, etc.)". → **MCP stdio 자식이 좀비로 쌓이는 문제**는 TalkAbout이 P2에서 MCP 자식 프로세스를 다룰 때 같은 형태로 맞닥뜨린다.
- **UID/GID 재매핑**으로 컨테이너가 만든 파일을 호스트 사용자가 읽게 한다(`docker-compose.yml:4-9`, `docker/stage2-hook.sh:97-117`). 그 대가로 `docker run --user <임의 UID>`를 **명시적으로 거부**한다(`docker/main-wrapper.sh:31-40`, `docker/stage2-hook.sh:26-74`).
- **api_server 키 자동 생성/강제** (`docker/stage2-hook.sh:448-521`): 키가 없으면 32바이트 난수를 생성해 `$HERMES_HOME/.env`에 append(`:518`), 컨테이너 env와 `.env`가 **둘 다** 있으면 어느 쪽이 이기는지 경고(`:474-475`), 16자 미만이면 기동이 거부된다고 경고(`:484-485`).
- **첫 부팅에만 config seed** (`:432`), `.env`에 남은 빈 `API_SERVER_KEY=` 줄 제거(`:493`).

### 1.3 TLS — hermes도 **종단하지 않는다** `[제외]`

TLS 종단 구현은 찾지 못했다. `gateway/*.py`, `hermes_cli/*.py`에서 `ssl_context`/`certfile` 계열은 **outbound 검증**뿐이다(`hermes_cli/auth_device_flow.py:215-229`, `hermes_cli/models.py:130-141` → `agent/ssl_verify.platform_ssl_context`). 인바운드는 문서로 위임한다:

- `docker-compose.yml:14-17` "The dashboard service binds to 127.0.0.1 by default. It stores API keys; exposing it on LAN without auth is unsafe. If you want remote access, use an SSH tunnel or put it behind a reverse proxy that adds authentication — do NOT pass `--insecure --host 0.0.0.0`."
- `SECURITY.md:313-315` "Do not expose the gateway or API to the public internet without VPN, Tailscale, or firewall protection."

→ **TLS 관문에 대해 hermes가 주는 것은 구현이 아니라 결정 기록**이다: ① loopback 기본값 ② 비-loopback은 명시적 break-glass ③ 인증 계층은 외부(reverse proxy) 책임. TalkAbout `docs/DEPLOYMENT_RUNBOOK.md:16`이 "loopback·사설 접근·공개 domain/TLS 중 접근 범위"를 미결정으로 남긴 상태이므로, 이 3분기를 그대로 문서 형식으로 채택할 수 있다. 비용 **S**, 최적 **hermes**.

### 1.4 systemd/launchd/Windows 서비스 — 코드는 `미확인`, 계약은 문서로 확보

`hermes_cli/gateway.py`(271KB) 안의 `generate_systemd_unit(system=, run_as_user=)` 계약은 `hermes_cli/AGENTS.md` § Profies/multi-instance에 상세히 기술되어 있으나 **해당 함수 본문은 읽지 않았다**(미확인). 문서가 명시하는 함정 중 이식 가치가 있는 것:

- "A supervisor starts with an empty environment, so the env override that makes `-p` work interactively does not exist there" → **서비스 유닛은 `HERMES_HOME`/`HOME`을 명시적으로 실어야 한다.**
- "an unresolvable `User=` is a blocker, never a dir-owner fallback"
- "Process liveness is `(pid, start_time)` or the canonical matchers, **never bare PID existence**"

→ TalkAbout은 service manager 자체가 없다(`DEPLOYMENT_RUNBOOK.md:41` "service manager·자동기동 설정은 없음"). 위 3줄은 systemd/Quadlet 도입 시 바로 필요한 계약이다. `[차용]`, **S**, 최적 **hermes**.

### 1.5 배포 함정을 코드로 막는 방식 — `refuse_symlinked_path`

`docker/stage2-hook.sh:204-213`의 `refuse_symlinked_path`는 chown/chmod/seed 대상이 심링크면 **경고 후 계속**(중단 아님)한다. 이유는 데이터 볼륨이 사용자 소유이기 때문이다. 그리고 `:387-406`에서 `XDG_RUNTIME_DIR`을 **보안 경계로 규정**한다: "holds the display-allocation lock, so it is a security boundary: refuse a [symlink]" + "owned by uid $xdg_owner (not root or hermes) — refusing to adopt it".

→ 원리는 **"내가 만든 게 아닌 경로에는 소유권을 주장하지 않는다"**. TalkAbout `deploy/podman/OPERATING.md:29-30`이 이미 같은 사상("journal 없는 기존 자원/DB/volume → 중단; 내용 보존")을 갖고 있으므로 **동등**이지만, *심링크를 통한 chown*이라는 구체 공격면은 TalkAbout 문서에 없다. `[차용]`, **S**.
---

## 2. rate limit / 남용 방지 (질문 2)

### 2.1 `bot_loop_guard.py` — 대화 단위 슬라이딩 윈도 + 쿨다운

`gateway/bot_loop_guard.py` (141줄, 전체를 읽음)

- 설정 기본값: `:22-27` `enabled=True, max_events=20, window_seconds=300, cooldown_seconds=600`.
- **핵심 설계 결정**: `:4-5` "``{PLATFORM}_ALLOW_BOTS`` only decides admission, so two Hermes profiles replying to each other never stop." 즉 allowlist가 봇을 허용하면 두 봇이 서로에게 무한 응답한다 → 별도 예산이 필요하다.
- `:612-615`(`authz_mixin.py`) "One budget per conversation, **not per sender pair**: a per-pair key would hand N bots N budgets." → 키는 `(profile, platform, chat_id)`.
- 읽기/쓰기 분리: `blocked()`(`:103-108`)는 순수 읽기라 "callers may ask as often as they like"; `admit()`(`:110-130`)만 카운트. `authz_mixin.py:617-629`가 메시지당 **1회** admit하고, `_is_user_authorized`(`:631-644`)는 peek만 한다 — 이 분리가 없으면 권한 검사가 여러 번 불리며 예산이 새어나간다.
- `:125-128` 트립 시 `cooldown_until` 설정 + `events.clear()`; `:132-141` `_sweep`으로 유휴 대화를 window당 1회 정리(메모리 상한).
- `:84` "Settings are re-read on every call so a config.yaml edit takes effect without a restart."

`[대조]` **분산 rate limit이 아니다.** 상태가 `self._events`/`self._cooldown_until` 프로세스 메모리에 있고(`:94-95`), 락은 `threading.Lock`(`:93`)이다. TalkAbout의 관문은 "**분산** rate limit"(STATUS.md §4)이므로 hermes가 정답을 주지 않는다. Orca도 1라운드에서 같은 한계(프로세스 메모리)였다. → 이 관문은 **어느 저장소에서도 답이 없다**. `[제외]`, 사유: 다중 프로세스/다중 노드 상태 공유 없음.

`[채택]` 다만 **구조**는 이식 가능하다(비용 **S**): TalkAbout `talkabout/auth.py:51-57`의 로그인 실패 카운터를 Postgres로 옮길 때 (a) 순수 읽기 판정과 카운트 쓰기 분리 (b) 키를 **대화/방 단위**로 (c) 쿨다운 후 카운터 clear (d) 상한 있는 sweep. 1라운드 §8-⑤ 제안(Postgres 이관)의 스키마를 이 파일이 사실상 지정해 준다.

### 2.2 api_server 인증 — "키 없으면 기동 거부, 폴백 없음"

`gateway/platforms/api_server.py`
- `:155` `DEFAULT_HOST = "127.0.0.1"`.
- `:1060` `self._api_key = extra.get("key", _get_scoped_secret("API_SERVER_KEY", ""))` — profile scope를 경유한다.
- `:1399-1411` 키를 `get_secret("API_SERVER_KEY","")`로 해석, 실패 시 `logger`에 원인을 남기고 `{"error": {"message": "Invalid gateway API key (API_SERVER_KEY)", "type": "gateway_auth_error"}}`.
- `:1417` "refuses to start without API_SERVER_KEY".

`[채택]` 규칙 2개, 비용 **S**, 최적 **hermes**:
1. **인증 비밀은 scope-aware 리더로만 읽고, 없으면 default로 폴백하지 않는다.** `hermes_cli/AGENTS.md` § Profile scope의 표현이 정확하다: "a scoped miss returns the **default**, NEVER `os.environ` (a leaked allowlist skips the allow-all check and silently rejects every secondary-profile sender, #86905)".
2. **인증 키가 없으면 기동을 거부**한다(약한 기본값으로 뜨지 않음). TalkAbout `serve`는 loopback bind만 하고 API 인증은 세션 쿠키/agent token이므로 직접 대응은 없지만, `/health`·`/ready`가 무인증인 현 상태를 문서에 명시할 근거가 된다.

### 2.3 MFA — hermes에도 **없다** `[제외]`

`totp|mfa|two_factor|2fa` 전수 grep에서 나온 것은 (a) `agent/vault_store.py`가 **사용자의 다른 서비스** TOTP secret을 보관하는 기능(`hermes_cli/vault.py:75` "Authenticator key (optional, hidden; the 2FA "setup key" or otpauth:// link"), (b) `gateway/platforms/base.py`류의 "a human can watch, take over (logins, 2FA, CAPTCHAs)" 주석뿐이다. **hermes 운영자 계정용 MFA 구현은 찾지 못했다.**

→ TalkAbout `STATUS.md` §4의 "운영 계정·MFA" 관문에 대해 **hermes는 아무것도 주지 않는다**. 1라운드 Orca도 없었다. `[제외]`, 사유: 구현 부재. 이 항목은 자체 설계가 필요하다는 판정만 남긴다(모순 아님 — 1라운드에서도 동일).

---

## 3. 비밀 관리 (질문 3)

### 3.1 `hermes_dir_acl.py` — 이번 라운드 최고의 단일 자산 `[채택]` M

TalkAbout은 **Windows Job Object만** 격리 근거로 쓰고 POSIX 실행을 차단한 상태(`STATUS.md` §4, `processes.py`)이므로, Windows 파일 권한 함정이 **활성 위험**이다.

모듈 docstring(`hermes_dir_acl.py:1-27`)이 문제를 정확히 규정한다:

> "Since CPython 3.13, `os.mkdir(path, 0o700)` on Windows applies a protected DACL of exactly SYSTEM, Administrators and OWNER RIGHTS. An elevated process's default owner is `BUILTIN\Administrators`, so OWNER RIGHTS resolves to Administrators too, and the user's own non-elevated token (where Administrators is deny-only) gets `WinError 5` on the directory and on everything created inside it"

즉 **관리자 권한으로 한 번 실행하면, 그 뒤 비관리자 실행이 자기 상태 디렉터리에 접근하지 못한다**. 발현 조건이 좁아 재현이 어렵고, TalkAbout이 설치 스크립트(`deploy/podman/install.py`)나 상승 권한 실행을 한 번이라도 하면 걸린다.

이식 가능한 규칙(모두 코드로 확인):
- `:11-18` 단일 진입점 `make_user_dir`: 비-Windows/비상승이면 그냥 `Path.mkdir`, 상승이면 사용자 SID에 상속 full-control ACE 추가.
- `:20-26` **junction/symlink/hard link는 절대 따라가지 않는다**. 근거: "same-user medium-integrity code can swap any entry under the home for a junction to e.g. `C:\Program Files\X`, and a grant that followed it would hand the user full control of the target across the UAC boundary". 방어는 3중 — `lstat` 선검사(`:59-69`), `FILE_FLAG_OPEN_REPARSE_POINT`로 핸들 열기(`:139-140`), 핸들 최종 경로 재확인(`:142-146`), icacls 폴백은 `/L`(`:211`).
- `:151-152` 하드 링크 거부(다른 이름이 홈 밖에 있을 수 있음).
- `:157-163` **반복 호출 시 DACL을 키우지 않는다**(이미 있으면 `return False`).
- `:23-26` `iter_tree_without_reparse_points`: "`os.walk` is not enough: it follows directory junctions (`os.path.islink` is False for one)".
- `:457-462` `unusable_dir_remedy`: 실패 시 **한 줄 복구 명령**(`icacls "…" /reset /t` 또는 `sudo chown -R "$(id -un)" …`).

`[채택]` 부착: `talkabout/processes.py` 옆 신규 `talkabout/state_dirs.py`, 그리고 `deploy/podman/install.py`의 데이터 디렉터리 생성 경로. **TalkAbout의 모든 상태 디렉터리 생성은 이 한 함수를 통과**해야 한다(hermes는 `agent/vault_store.py:34` `from hermes_dir_acl import make_user_dir`로 실제 사용한다). 비용 **M**(Win32 ACL 코드) — 다만 TalkAbout이 이미 `processes.py`에서 ctypes/Job Object를 쓰므로 선례가 있다.

### 3.2 자식 프로세스 env — 2단 scrub + fail-closed `[채택]` M

TalkAbout `talkabout/adapters.py:19-27`의 `child_environment()`는 **허용 목록**이다(좋다). hermes는 그 위에 3가지를 더한다.

`tools/environments/local_env_policy.py:19-53` (정책 데이터)
- `_STATIC_PROVIDER_ENV_BLOCKLIST`: provider 키 + `TELEGRAM_ALLOWED_USERS`·`GATEWAY_ALLOWED_USERS` 같은 **인가 변수까지** 차단 목록에 넣는다. 이유가 주석에 있다(`:13-18`): "env_passthrough can never re-allow a blocklisted name (GHSA-rhgp-j443-p4rf), so blocking it would be unrecoverable" — 즉 **한 번 차단하면 복구 불가**임을 인지하고 AWS 일반 체인은 의도적으로 허용한다.
- `:55-60` `_left_core_env`: 코어에서 플러그인으로 이전한 기능의 env도 "코어에 있을 때와 **정확히 같게**" 제거한다.

`tools/environments/local.py` (적용)
- `:262-293` `_filter_secret_env`: `_HERMES_FORCE_<NAME>` 접두사는 **호출자가 명시한 extra env에서만** unwrap하고, 내부 secret이거나 plugin strip 목록이면 버린다. `:288` blocklist는 `passthrough`/`first_party` 등록이 있을 때만 통과.
- `:362-375` `_scrub_credentials`: **2 tier** — 항상 지우는 `_ALWAYS_STRIP_FOLDED`, 그리고 `inherit_credentials=False`면 provider/tool 자격증명까지. `:364-365` 주석이 Windows 함정을 짚는다: "on Windows the env block itself is case-insensitive, so a lowercase-stored `gh_token` IS GH_TOKEN" → 비교는 **uppercase fold**.
- `:418-430` `served_profile_child_env`: 라우팅된 프로필용 자식은 "the launch `.env` residue and bridged `TERMINAL_*` are dropped AND **every** provider/tool credential is scrubbed from the base **regardless of provenance**: a key systemd / Compose / the shell injected into the launch process was never recorded in `.env` … so a name-based strip cannot see it". → **이름 기반 strip의 한계를 명시**하고 base를 통째로 scrub.
- `:315-320` "Unguarded on purpose: a scope/config failure here must be **loud**, not silently drop the declared secret again (#114209)" — 실패를 조용히 넘기지 않는다.
- `:243-259` session ContextVar가 bound면 **unset 변수를 상속하지 않고 strip**한다: "a bound value (incl. "") wins and an _UNSET var is STRIPPED, not inherited".

`[대조]` TalkAbout의 허용 목록은 이미 강하다(1라운드 §8-⑮). 그러나 **TalkAbout에 없는 것**:
1. **blocklist 상한의 불가역성 인지** — 한 번 막으면 plugin/스킬이 되살릴 수 없으므로 "무엇을 막지 않을지"를 먼저 결정해야 한다.
2. **case-insensitive fold**(Windows) — TalkAbout은 `key.upper()`를 쓰므로 이미 맞다(`adapters.py:24-25`). `[대조]`.
3. **이름 기반 strip으로 못 잡는 provenance** — 서비스 매니저/컨테이너가 주입한 키. TalkAbout은 자식에게 env를 **허용 목록**으로만 주므로 구조적으로 안전하다. `[대조]`.
4. **scope miss 시 fail-closed** — 다중 프로필 개념이 TalkAbout에 없으므로 `[제외]`.

→ 순수 이식분은 **"불가역 차단 결정 기록"** 하나다. 비용 **S**, 최적 **hermes**. 나머지는 `[대조]`.

### 3.3 opaque handle vault — "모델에게 값을 주지 않는다" `[채택]` M

`agent/vault_store.py:1-17` docstring:
> "Profile-scoped, **model-blind** credential store. Metadata (kind, label, origin, timestamps) lives alongside an encrypted secret payload … The model only ever sees **opaque handles + metadata** — secret values are resolved server-side by the browser fill path and **never enter tool results, logs, or the session DB**."

- 키 관리: `:10-13` "the key file and vault file are created **0600** under `<HERMES_HOME>/vault/`. `vault.key_storage: keychain` (opt-in) keeps the key in the OS keychain instead (`agent/vault_keychain.py`); `hermes vault key migrate` moves it."
- 암호화: Fernet(대칭), 로컬 생성 키.
- 계보 표기: `:14-15` "Ported design (opaque-handle vault fill) from Merit-Systems/OpenInstinct (MIT)".

`[채택]` **이것이 1라운드 질문("프롬프트 마스킹")의 진짜 답**이다: 마스킹은 손실적이고 우회 가능하지만, **애초에 모델의 도달 범위에 값을 넣지 않는 것**은 우회 대상이 아니다. TalkAbout 부착: `talkabout/mcp.py`가 반환하는 도구 결과와 `talkabout/attachments.py`의 provider 전달(`prepare_provider_input:321-335`)에서 **비밀 대신 handle**을 넘기는 계약. 다만 TalkAbout은 플랫폼이 credential을 갖지 않으므로 대상은 참가자 자신의 비밀(첨부에 포함된 키)이 된다. 비용 **M**, 최적 **hermes**.

**Orca와 비교**(1라운드 근거 인용): Orca는 `~/.orca/*.enc` + Electron safeStorage이며 **파일 키 vs OS keychain 선택이 provider마다 갈린다**(1라운드 §1.2, `encrypted-api-key-file-store.ts:120-137` 평문 fallback vs `plugin-secrets-store.ts:11` "never fall back to plaintext"). hermes는 **파일 0600 기본 + keychain opt-in + migrate 명령**으로 하나의 정책을 유지한다. → **일관성 축에서 hermes > Orca.** `[채택]` 최적 **hermes**.

### 3.4 검증 후 저장 — `rotate_token` `[채택]` S

`hermes_cli/_secrets_common.py:119-153`:
- `:136-137` TTY가 아니면 flag로 받으라고 **거부**(파이프로 비밀 받지 않음), `:140` `masked_secret_prompt`.
- `:144-145` **`verify(token)` 실패면 `.env`를 건드리지 않고 return 1**. docstring `:127-128`: "``verify(token)`` prints its own diagnostics and returns False to abort without touching .env — so **a bad paste never bricks the working token**".
- `:146-148` 저장 → `os.environ` 반영 → **캐시 무효화**(이전 토큰 fingerprint로 캐시된 pull 제거).

`[채택]` TalkAbout 부착: `talkabout/identity.py:206-234`의 credential 생성 경로. 현재는 (a) raw token 생성 → DB 저장 → **그 다음에** runtime_check(`:62-131`)로 검증. 즉 검증 전에 이미 유효한 자격증명이 존재한다. hermes 규칙대로면 **검증 전 상태를 `pending`으로 두고 검증 실패 시 자동 폐기**해야 한다. 실제로 TalkAbout은 `:128`에서 `check` 만료를 `min(now+CHECK_SECONDS, credential["expires_at"])`로 묶고 `:122` 실패를 `failed`로 기록하므로 **부분 채택**되어 있다. 남은 것: **실패한 credential을 목록에서 어떻게 정리하는지**가 `revoke`(수동, `:211-214`)뿐 → TTL 만료(`:221` 1~3600초)에 의존. `[채택]` "검증 실패 credential 즉시 폐기" 를 추가. 비용 **S**, 최적 **hermes**.

### 3.5 `_secrets_common.py`의 나머지 — `[대조]`

`secret_cli_env()`(`:106-116`)는 Bitwarden/1Password CLI 자식에게 **토큰을 일부러 넘긴다**: "Intentionally receives tokens — no scrub, no HOME rewrite (both CLIs store state under the real user home)." 이건 scrub 정책의 **명시적 예외**이며 docstring으로 남긴다. TalkAbout `adapters.py:19-27`에 이런 예외가 없으므로 지금은 문제 없지만, 예외가 생길 때 **왜 넘겼는지**를 코드에 남기는 규율로 참고. `[차용]` S.

---

## 4. 플러그인 신뢰 (질문 4)

### 4.1 신뢰 경계의 정의 — 코드가 아니라 **문서가 정본**

`SECURITY.md:155-169` (Plugin Trust Model):
> "Plugins load into the agent process and run with **full agent privileges**: they can read the same credentials, call the same tools, register the same hooks … The boundary for third-party plugins is **operator review before install**."

`SECURITY.md:58-65`가 더 근본적이다:
> "**The only security boundary against an adversarial LLM is the operating system.** Nothing inside the agent process constitutes containment — not the approval gate, not output redaction, not any pattern scanner, not any tool allowlist."

`[채택]` 이 문장은 TalkAbout `docs/PERMISSIONS.md`가 취해야 할 태도의 정본으로 쓸 수 있다. TalkAbout의 `tool_broker.py`는 이미 fakestore로 실 어댑터를 타입 금지(`:97`)하지만, **문서가 그 주장의 범위를 정하지 않으면** 독자가 "broker가 경계"라고 오해한다. 비용 **S**, 최적 **hermes**.

### 4.2 `plugin_loader.py` — 로더 자체의 방어

`plugins/plugin_loader.py` (278줄 중 1-120 + grep 확인)
- `:22-31` 모듈 로드 락 + `_LOAD_OWNERS`/`_LOAD_WAITERS` 스레드 대기 그래프.
- `:42-53` `_load_would_deadlock`: "importlib cannot see the loader lock in its own deadlock check, so **walk both kinds here**" — 로더 락과 importlib 모듈 락을 함께 그래프로 걸어 순환을 판정.
- `:56-64` `bounded_load_wait`: per-turn 호출자는 다른 스레드의 **hung import**에 무한 대기하지 않고 `_CONCURRENT_LOAD_WAIT_SECS = 10.0`(`:27`) 후 포기. `:24-26` "Past it the module is marked stalled (a hung import) and later bounded callers refuse at once".
- `:144-153` `load_plugin_module(..., synthetic_namespace=...)`: "user code: never imported in-process under `plugins.isolation: host`" → **경계 모드에서 사용자 코드 in-process import를 거부**.
- `:99-104` `iter_plugin_dirs`: "one mode-000 / ACL-denied child must not abort the listing".

`[차용]` **서드파티 플러그인의 hung import가 서버 전체를 멈추게 두지 않는다**는 규율. TalkAbout은 서드파티 도구 확장이 아직 없지만(P2 MCP 후보), MCP 서버 프로세스가 hang하는 경우가 동형이다. `talkabout/tool_broker.py`의 adapter 호출에 **유한 대기 + stalled 표시**를 두는 근거. 비용 **S**, 최적 **hermes**.

### 4.3 `plugin-catalog/` — 18개 입학 규칙 중 이식 가능한 4개 `[차용]` M

`plugin-catalog/README.md:13-14` "Presence in this directory **is** the trust signal."

| 규칙 | 라인 | TalkAbout에 옮길 내용 |
| --- | --- | --- |
| 2 | `:17-19` | "Every entry pins a full 40-character commit SHA. Branches, tags, and short SHAs are rejected by the loader." |
| 3 | `:20-26` | "**No self-updating code.** … The exact SHA pin *is* the trust model; a self-updater lets an installed copy move to a commit nobody reviewed." |
| 6 | `:39-42` | "The `capabilities:` block … must match what the plugin actually registers at the pinned commit … **undeclared capability creep is treated as a security issue**." |
| 11 | `:87-103` | "**Credentials come through Hermes, never from files.** … A plugin never reads `.env` files, Hermes's `auth.json`, another tool's login (a vendor CLI's token file, a browser profile, a keychain) … Its `authorize_url`, `token_url` and `hosts` are `https` only, and its `hosts` belong to the API the scopes are for." |
| 17 | `:138-156` | 스캔 불가 실행 페이로드 금지(ELF/Mach-O/PE/wasm, `.pyc`), 필요하면 GitHub build attestation을 pin된 commit에 대해 검증. |

그리고 **provenance 비신뢰** 원칙(6라운드 무관, hermes 고유): `plugins/AGENTS.md` § Plugin catalog — "Catalog provenance lives on the installer-owned `.install-metadata.json` record … **NEVER in the tree**: the in-tree `.hermes-catalog.json` is a convenience copy the Desktop reads … **Python never trusts it (a repo can ship a forged one)**."

`[차용]` **설치 메타데이터는 설치자 소유 기록에만 두고, 설치 대상 트리 안의 자기신고는 신뢰하지 않는다.** TalkAbout이 MCP 도구 카탈로그나 스킬 배포를 도입할 때 그대로 필요한 규칙이다. 부착: `talkabout/tool_broker.py`(도구 등록) + 신규 카탈로그 테이블. 비용 **M**, 최적 **hermes**.

**제외**: `plugins.isolation: host`(별도 프로세스 + framed JSON + `HOST_UNSUPPORTED_CTX_METHODS` 표)는 `[제외]` — TalkAbout은 도구를 broker가 소유하므로 프로세스 분리 요구가 없고, 도입 비용이 L인데 얻는 것이 "서드파티 파이썬 플러그인 격리"로 TalkAbout의 확장 모델(TalkAbout 토큰을 가진 agent)과 맞지 않는다.

### 4.4 TalkAbout이 이미 더 엄격한 부분 `[대조]`

- `talkabout/tool_broker.py:232-239`: 호출 실패 시 write는 `unknown` 상태 + `intent_digest`를 `_unknown`에 넣어(`:237`) 같은 의도의 재시도를 거부(`:153`). hermes의 approval gate는 **셸 문자열 denylist**이며 `SECURITY.md:142-146`이 "structurally incomplete"라고 자인한다. → 실행 원자성·불확실성 회계에서 **TalkAbout 우위**.
- `talkabout/tool_broker.py:97` `require(type(self.adapter) is FakeToolAdapter, 'real_adapters_disabled')` — 어댑터 계층을 **타입으로** 고정. hermes는 플러그인이 같은 프로세스에서 임의 import가 가능하다(`SECURITY.md:157-159`). → **TalkAbout 우위**.

---

## 5. 게이트웨이 다채널 인가 (질문 5) — 대부분 `[제외]`

### 5.1 구조

`gateway/authz_mixin.py` (758줄), `_is_user_authorized`(`:631-644`)의 순서가 docstring에 그대로 적혀 있다(`:634-637`): trusted-upstream delegation → chat-scoped group allowlists → `{PLATFORM}_ALLOW_BOTS` → per-platform allow-all → adapter role auth → pairing store → env/config allowlists → `GATEWAY_ALLOW_ALL_USERS` → **default deny**.

핵심 규칙:
- **no allowlist ⇒ fail closed**: `:686-694` — allowlist가 하나도 없으면 adapter 자체 정책(`enforces_own_access_policy`)이나 `GATEWAY_ALLOW_ALL_USERS`만 남고, 없으면 `False`. `SECURITY.md:205-209` "Adapters must **refuse** … until an allowlist is set. Code paths that **fail open** when no allowlist is configured are code bugs in scope."
- **세션 ID는 라우팅 핸들**: `SECURITY.md:210-213` "Knowing another caller's session ID does not grant access to their approvals or output; authorization is always re-checked against the allowlist."
- **allowlist가 있으면 pairing을 끈다**: `_get_unauthorized_dm_behavior`(`:716-758`) docstring `:726-729` "When an allowlist … is configured, default to `"ignore"` — the allowlist signals that the owner has deliberately restricted access; spamming unknown contacts with pairing codes is both noisy and **a potential info-leak**." Email은 무조건 `ignore`(`:737-738` "inboxes hold arbitrary mail").
- **봇 루프**: allowlist가 봇을 통과시켜도 쿨다운이면 거부(`:641-644`).

### 5.2 TalkAbout과의 비교 — `[대조]` (TalkAbout이 더 엄격)

| 축 | hermes | TalkAbout |
| --- | --- | --- |
| 판정 단위 | adapter별 allowlist 1장 | `credential scope ∩ membership ∩ account` 3중 교집합(`PERMISSIONS.md:5`) |
| 내부 차등 | **없음 명시** (`SECURITY.md:214-217`) | per-room permission 20종(`PERMISSIONS.md:9-30`) |
| 자원 단위 | 대화(chat) | 방·task·artifact·event·download 각각 |
| 격리 방법 안내 | "run separate agent instances with separate allowlists" | 서버가 강제 |

→ **다채널 인가 구조 자체는 `[제외]`**. TalkAbout에 어댑터 계층이 없고, 있어도 서버 권한 카탈로그가 우선한다.

### 5.3 그래도 가져올 3개 `[채택]` S

1. **"no allowlist ⇒ refuse" 를 명문화**. TalkAbout `docs/PERMISSIONS.md:32`는 "목록에 없는 권한과 agent의 human 전용 권한은 거절합니다"까지 있으나, **"호출자 자체가 미상이면 거절"**을 명시하지 않는다. `_principal_authorized`의 default-deny 경로를 문서 문장으로 옮긴다.
2. **allowlist 설정이 pairing/초대 기본값을 바꾼다**(`:756-758`). TalkAbout은 초대 기반이므로 반대 방향이다: **초대 없이는 어떤 안내도 주지 않는다**가 대응 규칙. `talkabout/api.py`의 미인증 응답이 방 존재 여부를 누설하지 않는지 확인 필요(**미확인**: api.py 338줄 중 401 경로를 읽지 않음).
3. **세션 ID ≠ 인증**을 테스트로 고정. TalkAbout은 이미 `session_id`를 라우팅에 쓰고 권한은 별도 검사하지만(`mcp.py:107-108`), hermes처럼 **문장으로** 선언하고 회귀 테스트로 묶는 편이 낫다.

---

## 6. 로컬라이즈 (질문 6) `[대조]` + `[차용]` S

`hermes-agent/locales/` 35파일: 언어 17종 × (`<lang>.yaml`, `<lang>.tui.yaml`) + `_keys.tui.json`(208KB) / `_keys.desktop.json`.

- 두 계층: `<lang>.yaml`은 **중첩 dict**(`locales/ko.yaml:1-8` `approval.dangerous_header: "⚠️  위험한 명령: {description}"`), `<lang>.tui.yaml`은 **평면 점 표기**(`locales/ko.tui.yaml:1-6` 주석 "Flat dotted keys; **every key from locales/_keys.tui.json**").
- `locales/_keys.tui.json`은 **key 원장**(알파벳 정렬 배열). 파일 주석이 계약을 말한다: 모든 키가 이 목록에 있어야 한다.
- `{description}`, `{0}` 같은 placeholder가 번역문 안에 유지된다(`ko.yaml:2`, `ko.tui.yaml:3` 주석 "Function-valued en entries use positional {0}, {1} placeholders").

`[대조]` TalkAbout은 `web/state.mjs` 기반 ko/en 이중 언어 + **parity 테스트**를 이미 갖고 있다(1라운드 보고 §3 "ko/en parity 테스트"를 TalkAbout 우위 항목으로 기록). hermes의 계층 분리(문장 카탈로그 vs TUI 문자열)는 TalkAbout 규모에 과하다.

`[차용]` **key 원장 파일 + 누락 검출 테스트**만 채택. TalkAbout이 문구를 늘릴 때 "ko에만 있는 키"를 CI에서 잡는다. 비용 **S**, 최적 **hermes**(패턴), 나머지 `[제외]`.

---

## 7. 운영 함정 카탈로그 → `docs/DEPLOYMENT_RUNBOOK.md` (질문 7)

`docs/DEPLOYMENT_RUNBOOK.md`(55줄) 말미에 "## 알려진 운영 함정" 절로 넣을 항목. **각 항목은 코드/문서 근거가 있고, TalkAbout에 실제로 해당하는 것만** 골랐다.

| # | 함정 | 증상 | 근거 | TalkAbout 해당도 |
| --- | --- | --- | --- | --- |
| 1 | **SQLite WAL-reset 손상 버그** (SQLite < 3.51.3) | WAL 모드 DB가 조용히 손상 | `Dockerfile:1-5` "Debian 13 still ships SQLite 3.46.1, which contains the upstream WAL-reset corruption bug", `:111-113` 자체검사 `if v < (3, 51, 3) … exit` | **높음** — `talkabout/worker.py:122-124`이 `journal_mode=WAL` + `synchronous=FULL`. 런타임 SQLite 버전을 `/ready`에 노출하고 3.51.3 미만이면 경고 |
| 2 | **라이브 DB + 사이드카 동시 복사 = torn restore** | 복원 후 DB가 열리지 않거나 최신 트랜잭션 소실 | `hermes_cli/backup.py:121-125` "SQLite sidecars are excluded because `*.db` is snapshotted via `sqlite3.backup()`: shipping the live WAL/SHM/journal alongside would pair a fresh snapshot with stale sidecar state and produce a torn restore" | **높음** — journal DB를 백업하려면 `-wal`/`-shm`을 함께 담지 말고 `sqlite3.backup()`(파이썬 `Connection.backup`) 사용 |
| 3 | **상승 프로세스가 만든 0o700 디렉터리에 비관리자 접근 불가** | `WinError 5`, 락/로그/상태 경로 전부 실패 | `hermes_dir_acl.py:1-9` | **높음**(Windows 전용 현 단계) — §3.1 |
| 4 | **인증 없는 loopback 외부 노출** | API 키·세션 전량 유출 | `docker-compose.yml:14-17`, `SECURITY.md:313-315`, `gateway/platforms/api_server.py:1417`(키 없으면 기동 거부) | **높음** — `DEPLOYMENT_RUNBOOK.md:16`이 미결정. "`0.0.0.0` bind는 break-glass" 서술 필요 |
| 5 | **hot pull 후 코드 스큐** | lazy import가 `ImportError`, 또는 오래된 모듈과 새 모듈 혼합 실행 | `gateway/code_skew.py:1-8` "The gateway's `sys.modules` is frozen at boot. If the checkout is updated underneath it, a first-time lazy import can resolve a freshly-pulled module against a stale cached dependency → ImportError" | **중간** — `talkabout` worker는 장수 프로세스. 서버/워커 시작 시 revision 기록 + 불일치 시 "재시작 필요" 거부(`:52-57`) |
| 6 | **데이터 볼륨 심링크를 통한 chown** | 엉뚱한 경로의 소유권 변경 | `docker/stage2-hook.sh:204-213`, `:387-406`(XDG_RUNTIME_DIR은 보안 경계) | **중간** — `deploy/podman/install.py`가 chown하는 경로 검사 |
| 7 | **컨테이너 `--user <임의 UID>` 로 기동** | bootstrap 건너뛰고 권한 오류로 사망 | `docker/main-wrapper.sh:31-40`, `docker/stage2-hook.sh:26-74` | **낮음**(TalkAbout은 rootless Podman 고정 UID 10001) |
| 8 | **백업이 재생성 가능한 트리까지 훑어 며칠 걸림** | "backup stuck for days" | `hermes_cli/backup.py:67-91` 주석 "one plugin venv or pip/uv cache under HERMES_HOME walked file-by-file balloons a backup to hundreds of thousands of entries" | **중간** — 백업 대상이 PostgreSQL dump라 직접 해당은 없으나, 아티팩트 디렉터리가 생기면 동일 |
| 9 | **백업 안에 백업이 중첩** | 지수적 증가 | `backup.py:78-79` `"backups"`, `state-snapshots` 제외 | **낮음** |
| 10 | **자격증명 저장소가 백업 아카이브에 포함** | 백업 파일 하나로 전체 유출 | `backup.py:84-86` `browser-profile`(Cookies/Login Data) 제외, `:161-165` `_SECRET_FILE_NAMES = {".env","auth.json","state.db","vault.key","vault.json.enc"}` → **복원 시 0600으로 조인다** | **높음** — TalkAbout 백업에 `TALKABOUT_DATABASE_URL`/시크릿이 섞이지 않도록 명시. `deploy/podman/README.md:87`가 umask 077만 명시 |
| 11 | **복원이 소스 머신의 런타임 상태를 덮어씀** | 게이트웨이/워커가 "starting"에 고착 | `backup.py:142-148` `_IMPORT_SKIP_NAMES = {"gateway_state.json","gateway.pid","cron.pid","gateway.lock","processes.json"}` "They hold runtime state namespaced to the SOURCE machine" | **중간** — TalkAbout journal·cursor·lease 파일에 동일 위험 |
| 12 | **동시 백업 2개** | 부분 아카이브 게시 | `backup.py:181-217` `_backup_operation_lock`(msvcrt/fcntl, 0.25초), `:220-239` `_atomic_output_path`(hidden sibling → `os.replace`) | **중간** — pg_dump 동시 실행 방지 |
| 13 | **백업 무결성 미검증** | 손상된 백업을 성공으로 기록 | `backup.py:373-393`(zeroed DB 서명), `:408-455` `verify_sqlite_integrity`(header → schema probe → `PRAGMA integrity_check`, 2GiB 초과 시 O(1) probe) | **중간** — `pg_restore --list`/`pg_verifybackup` 등가 |
| 14 | **zombie 자식 누적** | PID 고갈 | `Dockerfile:65-70`(tini→s6), `:119-121` | **낮음**(P2 MCP stdio 후) |
| 15 | **allowlist 설정 시 pairing 안내가 정보 누설** | 미등록 발신자에게 초대 코드 살포 | `gateway/authz_mixin.py:726-729` | **중간** — 초대 없는 호출자에게 방/초대 존재를 알리지 않기 |

이 표를 런북에 넣을 때는 **TalkAbout에 해당하는 1·2·3·4·10·11·12·13번만** 남기고 나머지는 "해당 없음 + 이유"로 접는 편이 낫다. 비용 **S**, 최적 **hermes**(다른 저장소에는 운영 함정 카탈로그가 없다).

---

## 8. 백업 (질문 1 보강) — TalkAbout의 실제 격차는 좁다 `[대조]`+`[차용]`

정정: 1라운드 브리프가 "백업 미결정"이라 했으나, TalkAbout은 이미 상당 부분을 갖고 있다(`deploy/podman/README.md:85-97`): umask 077 logical custom-format dump + SHA-256·PG major·source SHA·schema revision 기록(`:87`), **globals backup 아님**을 명시(`:95`), 운영 restore drill 미실시(`:95-97`).

→ hermes에서 가져올 것은 **pg_dump 대체가 아니라 그 주변 4개**다:
1. **백업 슬롯 락**(`backup.py:181-217`) — msvcrt(`LK_NBLCK`)/fcntl(`LOCK_EX|LOCK_NB`) 양쪽, 0.25초 안에 못 잡으면 `BackupInProgressError`. Windows/Linux 둘 다 도는 TalkAbout에 그대로 필요. **S**
2. **원자 게시**(`:220-239`) — `.<name>.<pid>-<tid>.partial`에 쓰고 성공 시에만 `os.replace`. 실패 시 partial 삭제. **S**
3. **복원 epoch/스킵 목록**(`:142-148`) — 소스 머신 런타임 상태를 덮지 않는다. TalkAbout `worker.py` journal·cursor에 동일. **M**
4. **무결성 검증 후 성공 표기**(`:408-455`) — 크기 상한을 넘으면 O(1) probe로 강등("minutes of pegged CPU on a 30 GB state.db, reading as a hung `hermes update`", `:401-405`). **S**

`[차용]` 최적 **hermes**. 부착: `deploy/podman/` + 신규 `tools/backup_verify.py`.

---

## 9. 1라운드 결론과의 관계 (모순 점검)

| 1라운드 주장 | 이번 라운드 판정 |
| --- | --- |
| "Orca는 프롬프트 마스킹을 하지 않는다" | **모순 없음, 강화.** hermes도 프롬프트 마스킹을 하지 않으며, `SECURITY.md:147-148`이 출력 redaction을 경계가 아닌 휴리스틱으로 규정한다. 대안은 마스킹이 아니라 opaque handle(§3.3) |
| "TalkAbout은 stderr 허용목록에서 Orca보다 엄격" | **모순 없음.** hermes의 stderr 처리는 `미확인`이지만, hermes의 강점은 stderr가 아니라 **자식 env와 파일 경계**다. 두 저장소를 같은 축에 놓으면 TalkAbout은 "출력"에서 우위, hermes는 "입력 env·파일 권한"에서 우위 |
| "Orca는 MCP 설정을 쓰지 않고 검사만 한다" | **모순 없음.** hermes도 MCP 설정을 생성하는 코드를 찾지 못했다(이번 범위 확인: `optional-mcps/`·`mcp_tool_*`은 `미확인`). hermes가 주는 것은 **카탈로그 신뢰 규칙**이며 설정 병합이 아니다 |
| "TalkAbout의 tool_broker는 Orca보다 정교" | **모순 없음, 확장.** hermes도 approval gate를 denylist로 인정(`SECURITY.md:142-146`)하므로, 불확실성 회계(`tool_broker.py:232-239`)를 갖춘 것은 TalkAbout뿐 |
| "credential 유출 위험은 활성 누출이 아니다" | **모순 없음.** hermes의 §2.3도 env scrub을 "reduces casual exfiltration. It is not containment"(`SECURITY.md:130-135`)으로 같은 선을 긋는다 |

**새로 생긴 모순 1건(내부)**: hermes는 `SECURITY.md:60-65`에서 "OS만이 경계"라고 선언하면서, 같은 문서 `:301-321`의 Deployment Hardening은 **in-process 조치(allowlist·approval·operator review)를 사실상 필수**로 요구한다. 문서 내부에서 "경계 아님"과 "그래도 필수"가 공존한다 — 이건 모순이라기보다 **계층 선언**이지만, 인용할 때 어느 쪽만 인용하면 오독된다. TalkAbout 문서에 옮길 때 두 문장을 함께 실어야 한다.

---

## 10. Top 5 실행 제안

| # | 제안 | 태그 | 비용 | 부착 위치 | 최적 저장소 | 검증 방법 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **journal SQLite 안전성**: ① 런타임 `sqlite3.sqlite_version_info >= (3,51,3)` 확인을 `preflight`/`/ready`에 추가 ② journal 백업은 `Connection.backup()` 사용, `-wal`/`-shm` 동반 복사 금지 ③ 백업 무결성 검사 | `[채택]` | S | `talkabout/worker.py:122-124`, `talkabout/preflight`(CLI), `deploy/podman/` | **hermes** (`Dockerfile:1-5,111-113`; `backup.py:121-125,408-455`) | 3.46 계열 SQLite에서 WAL 손상 재현은 위험 → 버전 게이트만 단위테스트. 백업본을 열어 `PRAGMA integrity_check` 통과 확인 |
| 2 | **Windows 상태 디렉터리 ACL**: 모든 상태 디렉터리 생성을 단일 `make_user_dir` 경유로 통일, junction/symlink/hardlink 거부, 상승 프로세스에서 사용자 SID ACE 부여, 실패 시 한 줄 복구 명령 로그 | `[채택]` | M | 신규 `talkabout/state_dirs.py` + `talkabout/processes.py` + `deploy/podman/install.py` | **hermes** (`hermes_dir_acl.py:1-27,59-69,127-172,407-437,457-462`) | 상승/비상승 2회 실행 후 비상승 토큰으로 디렉터리 쓰기 성공. junction을 심어 grant가 거부되는지 |
| 3 | **런북에 운영 함정 8건 추가** (§7의 1·2·3·4·10·11·12·13) | `[채택]` | S | `docs/DEPLOYMENT_RUNBOOK.md`(새 절) | **hermes** | 문서 리뷰 + 각 항목에 TalkAbout 파일:라인 근거가 붙었는지 |
| 4 | **MCP/도구 카탈로그 신뢰 규칙**: 40자 SHA pin, self-updater 금지, 선언 capability = 실제 등록, 자격증명은 서버 경유만, provenance는 설치자 기록에만 | `[차용]` | M | `docs/PERMISSIONS.md` + `talkabout/tool_broker.py` + 신규 카탈로그 표 | **hermes** (`plugin-catalog/README.md:17-42,87-103`, `plugins/AGENTS.md` § Catalog) | 카탈로그 항목이 pin 없이 등록되면 거부. 트리 안 자기신고 파일을 무시하는 테스트 |
| 5 | **credential 회전 절차에 "검증 실패 시 즉시 폐기"**: pending 상태로 생성 → runtime_check 실패 시 revoked 처리(현재는 TTL 만료 대기) | `[채택]` | S | `talkabout/identity.py:206-234` | **hermes** (`_secrets_common.py:119-153`) | challenge 불일치로 `failed`가 된 credential이 이후 어떤 요청에도 통과하지 못하고 목록에서 revoked로 보이는 테스트 |

**차선(비용 낮은 순)**: ⑥ 백업 슬롯 락 + 원자 게시(`S`) · ⑦ 코드 스큐 감지(`S`) · ⑧ locale key 원장(`S`) · ⑨ "no allowlist ⇒ refuse" 문장화(`S`).

---

## 11. 미확인 목록 (추정으로 메우지 않음)

| 항목 | 상태 |
| --- | --- |
| `hermes_cli/gateway.py`(271KB)의 `generate_systemd_unit`/`systemd_unit_is_current` 구현 | **미확인** — 계약은 `hermes_cli/AGENTS.md`로만 인용 |
| `gateway/platforms/api_server.py`(229KB)의 인증 미들웨어 전수 | **부분 확인**(`:1060,1399-1417`만) |
| `hermes_cli/auth.py`(126KB)의 provider OAuth 흐름 | **docstring만 확인**(`:1-10`), 흐름 본문 미확인 |
| `agent/vault_store.py`(685줄)의 Fernet 키 유도·회전 구현 | **docstring+`:37`만 확인**. `vault_keychain.py`(154줄) 미확인 |
| `optional-mcps/`·`tools/mcp_tool_*`의 MCP 설정 생성 여부 | **미확인** — "설정을 쓰지 않는다"는 주장은 **이번 범위에서 찾지 못했다** 수준 |
| `hermes_cli/backup.py` 나머지(2180줄 중 1-470) — `run_import`·`_safe_restore_db`·`_restore_destination_epoch` | **미확인**(함수명만 확인) |
| `gateway/control_socket.py`(576줄) 전체 — 인증은 `:3,155-160,280-287,333-336` 확인 | **부분 확인** |
| `tests/agent/test_i18n*.py`의 parity 검증 강도 | **미확인**(파일명만) |
| TalkAbout `talkabout/api.py`(338줄)의 미인증 401 경로가 방 존재를 누설하지 않는지 | **미확인** |
| TalkAbout `talkabout/domain.py`의 `HUMAN_PERMISSIONS` 정의 | **미확인**(1라운드와 동일) |

---

## 12. 라이선스

hermes-agent는 **MIT** (`hermes-agent/LICENSE:1-2`, "Copyright (c) 2025 Nous Research"). 또한 `agent/vault_store.py:14-15`은 **자체적으로 제3자 고지를 포함**한다: "Ported design (opaque-handle vault fill) from Merit-Systems/OpenInstinct (MIT)". 복사 시 이 계보 표기까지 보존해야 한다.

이 보고서가 제안한 것 중 코드 이식 대상(고지 필요): `hermes_dir_acl.py`(462줄), `backup.py`의 락/원자게시/검증 함수, `bot_loop_guard.py`(141줄), `code_skew.py`(57줄). 나머지(카탈로그 규칙·운영 함정·인가 규칙)는 **문서·정책 형태**이므로 문장 재작성이 자연스럽고, 그 경우에도 출처를 주석으로 남긴다.

`opencodex`는 이 보고서의 판정 대상이 아니며(`LICENSE` 미확인, 다른 팀원 담당), §0의 "최적 저장소" 열에서 hermes가 아닌 항목은 **orca**(마스킹 단일 choke point — 1라운드)뿐이다.
