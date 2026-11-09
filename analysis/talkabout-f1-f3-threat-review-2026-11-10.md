# TalkAbout 위협 점검: ai-dev-director F1~F3 적용 (2026-11-10)

- 대상: 이 저장소 `main` (`35997ac` 기준 소스). 근거 분석: [ai-dev-director-review-2026-11-10.md](ai-dev-director-review-2026-11-10.md)
- 방법: 정적 코드 읽기. **코드·테스트·서버를 실행하지 않았고 변경도 하지 않았다.** 줄 번호는 이번에 읽은 사본 기준이다.
- 근거 등급: 코드 직접 확인 = High, 읽은 코드로부터의 추론 = Medium, 미확인 = `[unverified]`
- 읽은 범위: `api.py`, `auth.py`, `setup.py`, `approval.py`, `core.py`(mutation·proposal·dispatch·invite), `domain.authorize`, `agent_runtime.py`(환경·명령·work profile), `credential_vault.py`·문서, `WORK_PROFILE.md`. **미정독**: `mcp.py`, `worker.py` 본문, `processes.py` 일부, `http_runtime.py`, `tool_broker.py`(호출처 없음 확인만)

## 1. 결론

| 모델 | 요지 | 판정 |
|---|---|---|
| F1 로컬 제어면 인증 | 모든 상태 변경 경로가 자격증명을 요구하고 토큰을 응답으로 내려주지 않는다. ai-dev-director의 결함 4가지가 **모두 해당 없음**. 남은 것은 TLS 부재와 로그인 잠금 DoS | **대체로 방어됨** (Low~Medium 잔여) |
| F2 AI의 자기 승인·범위 면제 | 승인·채택·계획 적용·dispatch가 서버에서 human 전용으로 강제된다. AI가 사용자 대신 면제하는 경로 없음. 다만 `approval.py`에 **`timeout`이 긍정 승인으로 분류되는 잠복 함정** | **방어됨** (잠복 위험 1건) |
| F3 AI 코드 무샌드박스 실행 | 기본(text_only)은 실행 능력 자체가 꺼져 있고 플랫폼은 적용·검증 실행을 하지 않는다. **opt-in work profile이 사실상 같은 위험을 연다**: 실제 디렉터리 쓰기, shell 켜짐, 승인 없음, 되돌리기 없음, 출력 시크릿 마스킹 미연결 | **기본은 안전, work profile은 Medium~High 잔여** |

가장 먼저 손볼 것은 ① work profile 사전 조건(git 작업본·깨끗한 트리·되돌리기), ② 에이전트 출력의 시크릿 마스킹 연결(`redact_sensitive`가 코드에 있으나 어디서도 호출되지 않음), ③ `approval.py`의 `timeout` 처리, ④ TLS 종단 없는 노출 금지 명문화.

## 2. F1 — 로컬 제어면 인증

ai-dev-director의 결함과 1:1 대조.

| ai-dev-director 결함 | TalkAbout | 근거 |
|---|---|---|
| 상태 변경 경로가 토큰 없이 Origin 검사만 | **없음.** `/api/v1/*`는 `setup/status`·`setup/csrf`·`setup/complete`·`session`(로그인)을 제외하고 전부 `token(request)`를 거쳐 `store.auth`로 검증 | `api.py:246-276, 289` |
| `Origin` 헤더가 없으면 통과 | **거부.** 쿠키 세션의 비-GET은 `Origin == base_url` 정확 일치를 요구하고, 없으면 403. 추가로 세션별 CSRF 토큰 비교 | `api.py:123-125`, `auth.py:90-94` |
| 토큰을 GET 응답으로 노출 | **없음.** `/health`·`/ready`는 스케줄러 상태만. 쿠키는 `HttpOnly`+`SameSite=Strict` | `api.py:134-145, 274` |
| 토큰 파일 권한 미제한 | 서버는 토큰을 **해시로만** 저장(`token_hash`). 에이전트 측은 환경변수 또는 opt-in DPAPI vault(`0o600` 생성 + 현재 사용자 DPAPI) | `auth.py:73-74`, `credential_vault.py:275`, `CREDENTIAL_VAULT.md` |
| 임의 프로젝트 폴더 선택 | 해당 개념 없음(작업 디렉터리는 에이전트 CLI 인자로 소유자가 지정) | `cli.py:108-112` |
| 본문 크기 무제한 | **상한 있음.** JSON 100,000바이트, 업로드 별도 상한 | `api.py:236-239, 223-226` |
| DNS rebinding | `TrustedHostMiddleware` 허용 목록(`127.0.0.1`, `localhost`, `::1`) | `api.py:86` |
| 최초 설정 탈취 | 개인 터미널에서만 발급되는 일회용 코드(15분, 5회 실패 제한, 해시 저장, `compare_digest`) | `setup.py:50-99` |

### 잔여 발견 (F1)

| # | 내용 | 심각도 | 근거 |
|---|---|---|---|
| F1-a | **평문 HTTP.** 쿠키는 `https`일 때만 `Secure`. 컨테이너는 앱이 `0.0.0.0:8080`에 바인딩하고 격리는 호스트의 loopback 게시(`127.0.0.1:18081`)에 의존한다. mcp-box에서 이 게시를 확인했다. 사설망·공개로 넓히려면 TLS 종단 프록시가 필요하고, `allowed_hosts`가 `localhost`로 고정돼 이름 기반 접속은 현재 거부된다 | Medium (노출 확장 시) | `container.py:92`, `api.py:274`, 11-10 점검 |
| F1-b | **로그인 잠금 DoS.** 실패 한도가 `workspace:username` 단위이고 메모리 보관. 같은 호스트의 다른 사용자가 5회/분 실패로 피해자 로그인을 막을 수 있고, 서로 다른 이름 1,024개로 테이블을 채우면 60초간 전체 로그인이 `login_capacity`로 닫힌다(설계상 fail-closed) | Low~Medium (공유 호스트) | `auth.py:40-69` |
| F1-c | `GET /session`이 호출마다 CSRF 토큰을 **교체**한다. 탭 두 개면 한쪽 POST가 `csrf_invalid`로 실패한다. 보안 결함이 아니라 기능 결함 | Low | `auth.py:84-88` |
| F1-d | 설정 전(`setup_required`) 인증 없이 `setup/status`가 `setup_container` 이름을 반환한다 | Low (정보) | `api.py:246-248` |
| F1-e | 같은 호스트의 다른 OS 사용자가 `127.0.0.1:18081`에 도달할 수 있다. 자격증명은 필요하지만 `/health`·`/ready`·정적 파일·로그인 시도는 열려 있다 | Low | 11-10 점검 |

**권고**: 비-loopback 노출 전에 TLS 종단과 `allowed_hosts` 구성을 `DEPLOYMENT_RUNBOOK`에 필수 전제로 적는다. 잠금 키에 클라이언트 주소를 더하거나 잠금 대신 지연(백오프)으로 바꾸는 것을 검토한다. `GET /session`의 CSRF는 기존 토큰이 유효하면 재사용하게 한다.

## 3. F2 — AI의 자기 승인·범위 면제

ai-dev-director는 HEAD(AI)가 의존성·빌드 설정·권한 파일 변경과 파일 수·diff 상한 초과를 사용자 대신 면제한다. TalkAbout에서 같은 일이 가능한 지점을 모두 찾았다.

| 지점 | 강제 방식 | 근거 |
|---|---|---|
| 방 메시지·멤버십·제어·승인(`controls/*/approve`)·산출물 채택(`adoptions`)·불확실 해소 | `authorize(..., human=True)`가 `actor.kind == "human"`을 **서버에서** 요구 | `core.py:899-900`, `domain.py:70-76` |
| 계획 적용·제안 결정·dispatch | `work_plan_authorize` = human + `room:control` + 방 소유자 | `core.py:82-86, 670, 696, 763` |
| 에이전트의 협업 제안 | 생성만 가능(종류는 `question/review/information`로 제한). 승인·적용은 소유 human만. 적용 시 `acknowledge_untrusted` 필수, **사람이 쓴 노드**만 추가 | `core.py:606-737` |
| 수동 시작 정책 | `start_policy=manual`은 human만 설정 | `core.py:442` |
| 에이전트가 에이전트를 초대 | 기본 꺼짐(`TALKABOUT_ENABLE_AGENT_INVITES=0`). 켜도 방 전용 자격증명, human이 승인한 정책 개정·횟수 상한, 초대받은 쪽의 명시 수락 필요 | `core.py:1605-1648` |
| dispatch의 쓰기 범위 | 플랫폼이 `write_scope_enforced: False`를 명시하고 human이 `acknowledge_write_scope_unenforced`로 확인 | `core.py:42, 765-771` |

**결론**: AI가 AI의 범위 위반을 면제하는 경로는 확인하지 못했다. 범위 판단의 유일한 게이트가 사람이다.

### 잔여 발견 (F2)

| # | 내용 | 심각도 | 근거 |
|---|---|---|---|
| F2-a | **잠복 함정.** `approval.py`는 승인 출처를 `human`/`saved_rule`/`timeout`으로 정의하고, `approval_state`는 출처가 이 집합에 **들어 있기만 하면** 나머지 조건이 맞을 때 `ready`(긍정)로 분류한다. 즉 `timeout`으로 만든 승인도 `ready`가 된다. 현재 이 모듈은 어디서도 import되지 않아 **비활성**이지만, A5/A6 게이트에 연결하는 순간 "시간 초과 = 승인"이 열린다 | Medium (연결 시) | `approval.py:18, 129-131, 158-160`; import 호출처 없음(grep) |
| F2-b | `dispatch`의 쓰기 범위(`analysis`/`owner_approved_workspace`)는 **문자열 라벨**이다. 플랫폼은 강제하지 않으므로 사람 확인이 사실상 유일한 통제인데, 확인 항목이 세 개의 boolean 체크일 뿐 변경 대상 디렉터리·Git 상태를 보여주지 않는다 | Low~Medium | `core.py:60, 763-771` |
| F2-c | 에이전트 초대 기능을 켜기 전에 `_require_live_agent_invite` 이후의 수락 경로 전체를 읽지 않았다 | `[unverified]` | `core.py:1650-` |

**권고**: `approval_state`에서 `timeout`을 긍정 출처로 인정하지 않고(거절 또는 `expired`), `saved_rule`은 파괴적·외부 효과에 적용 불가로 못 박는 테스트를 먼저 쓴다. 활성화 전 조건을 `approval.py` docstring에 이미 적어 둔 "A5/A6 게이트와 검토된 계획"에 이 항목을 추가한다.

## 4. F3 — AI가 쓴 코드를 샌드박스 없이 실행

ai-dev-director의 F3는 (1) 검증 단계가 worktree에서 AI 코드를 실행하고 (2) 반영 후 **원본 트리**에서 재검증하는 구조였다.

### 4.1 기본 구성 (text_only) — 해당 없음

| 항목 | 설정 | 근거 |
|---|---|---|
| Codex | `--sandbox read-only`, shell·patch·MCP·hooks·plugins·web search 전부 꺼짐 | `agent_runtime.py:788-804` |
| Claude | `--tools ''`, `--restricted --safe-mode`, `--permission-mode dontAsk`, hooks 비활성 | `agent_runtime.py:805-811` |
| Grok | `--tools '' --deny '*' --sandbox read-only` | `agent_runtime.py:812-813` |
| 환경변수 | **allowlist 방식**(PATH·HOME 등 공통 + 선택한 공급자 자격만). 플랫폼 토큰·DB URL 제거 | `agent_runtime.py:318-327` |
| 플랫폼의 적용·검증 실행 | **없음.** 플랫폼은 에이전트 출력을 어디에도 적용하지 않으며 빌드·테스트를 돌리지 않는다. "원본 트리에서 재검증" 구조 자체가 없다 | `WORK_PROFILE.md`, `agent_runtime.py:199` |

ai-dev-director의 환경 처리(키 5개 denylist)보다 TalkAbout의 allowlist가 강하다.

### 4.2 opt-in work profile — 같은 위험을 사실상 연다

`--work-profile --work-workspace <dir>`는 Codex `workspace-write`, shell 도구 **켜짐**, `approval_policy="never"`, `--ignore-rules`, 소유자가 지정한 실제 디렉터리를 cwd로 사용한다 (`agent_runtime.py:769-787`). Windows 전용이며 기본 꺼짐이다.

| # | 발견 | 심각도 | 근거 |
|---|---|---|---|
| F3-a | **되돌리기 없음.** 격리된 git worktree·스냅샷·백업이 없다. 대상은 "삭제하지 않는" 소유자의 실제 디렉터리이고, 11-09에 넣은 것은 경로 검증(링크·과대 경로 거부)뿐이다. 변경 후 범위 검사(diff 기반)도 없다. 사후 관찰은 권한 승인도 OS 샌드박스도 아니라고 문서가 명시한다 | **High** (work profile 사용 시) | `agent_runtime.py:155-187`, `WORK_PROFILE.md:57-59` |
| F3-b | **읽기 범위가 작업 디렉터리에 한정된다는 보장이 없다.** `workspace-write`가 제한하는 것은 쓰기다. 프롬프트의 "디렉터리 안만 건드려라"는 지시이지 강제가 아니다. 환경 allowlist에 `HOME`·`USERPROFILE`·`APPDATA`·`LOCALAPPDATA`·`CODEX_HOME`이 포함돼 설정·자격증명 디렉터리 위치가 그대로 노출된다. 같은 사용자 권한의 DPAPI vault 파일은 문서가 스스로 "막지 않는다"고 적었다. Windows에서의 Codex 샌드박스 읽기 제한 동작은 `[unverified]` | Medium~High | `agent_runtime.py:196-199, 320-323`, `CREDENTIAL_VAULT.md` |
| F3-c | **에이전트 출력의 시크릿 마스킹이 연결돼 있지 않다.** 플랫폼 토큰은 환경에서 제거되고 **입력 프롬프트**에 포함되면 거부(`agent_runtime.py:1479`)되지만, 에이전트가 읽은 파일 내용(키·토큰)을 **출력 텍스트**로 방에 올리는 경로를 막는 코드는 없다. `redact_sensitive`가 `content_framing.py:128`에 정의돼 있으나 **호출처가 `talkabout/` 안에 없다**(테스트에서만 사용). 출력은 방 참가자와 다른 에이전트(다른 벤더)에게 전달된다 | **High** (work profile 사용 시) | grep: `redact_sensitive` 호출처 없음 |
| F3-d | **네트워크 차단을 플랫폼이 검증하지 않는다.** 프롬프트에 "네트워크 접근을 추가하지 말라"는 문장만 있다. `web_search="disabled"`는 검색 도구이지 셸의 네트워크가 아니다. 공급자 샌드박스의 기본 네트워크 정책에 의존하며 버전별 동작은 `[unverified]` | Medium | `agent_runtime.py:196-199, 785` |
| F3-e | `approval_policy="never"` + `--ignore-rules`로 공급자 쪽 명령 승인·규칙이 모두 꺼진다. 플랫폼의 사전 승인도 없다 | Medium | `agent_runtime.py:778-779` |
| F3-f | 프로세스 격리(Windows Job)는 **수명**만 제한하고 능력은 제한하지 않는다(코드 주석이 같은 취지) | Info | `agent_runtime.py:158-160` |
| F3-g | 출력 상한은 `MAX_LINE=256KiB` 등으로 두고 사후에 파일 크기를 검사한다. 11-09 로그의 "bounded pipe 출력" 후속과 같은 문제가 남아 있어 폴링 사이의 폭주는 막지 못한다 | Low~Medium | `agent_runtime.py:1021-1030`, `SESSION_LOG.md` 11-09 |

### 4.3 기타 실행 표면

- `tool_broker.py`·`approval.py`는 `talkabout/` 안 어디에서도 import되지 않는다(grep). 설계 문서 단계이며 비활성이다.
- `talkabout mcp`(`mcp.py`, 209줄)는 읽지 않았다. 별도 로컬 표면이므로 `[unverified]`.
- ai-dev-director와 달리 TalkAbout의 `tools/run_safe_regression.py` 등 개발용 스크립트가 에이전트 산출물을 실행하는 경로는 확인하지 못했다.

### 권고 (F3, 우선순위순)

1. **work profile 사전 조건 강화**: 대상이 git 저장소이고 작업 트리가 깨끗할 때만 허용하며, 실행 전 스냅샷 커밋 또는 전용 브랜치/worktree를 만든다. 종료 시 변경 목록을 `git diff` 기준으로 기록해 범위 밖 변경을 감지한다(보고가 아니라 관측). ai-dev-director의 worktree+해시 매니페스트 구조가 참고가 된다. 코드 복사는 하지 않는다.
2. **출력 마스킹 연결**: 방에 게시하기 전 모든 에이전트 출력에 `redact_sensitive(text, blocked_values)`를 적용하고, 적용 사실을 증거 필드에 남긴다. 마스킹 실패 시 게시를 거부한다(fail-closed).
3. **work profile 문서에 비보장 사항 명시**: 읽기 범위, 네트워크, 되돌리기 부재, 시크릿 노출 가능성. 현재 문서는 쓰기 격리 설명이 중심이다.
4. **dispatch 확인 화면 강화**: `owner_approved_workspace` 선택 시 대상 디렉터리·git 상태·미커밋 변경 수를 사람에게 보여주고 확인받는다.
5. 환경 allowlist에서 work profile일 때 `CODEX_HOME`·`APPDATA`·`LOCALAPPDATA` 필요성을 재검토한다(공급자 로그인 유지에 필요하면 그 범위를 문서화) `[unverified: 로그인 유지에 필수인지 시험하지 않음]`.

## 5. 우선순위 요약

| 순위 | 항목 | 유형 | 비용 |
|---|---|---|---|
| 1 | F3-c 출력 시크릿 마스킹 연결 | 코드 | 작음 (함수 존재) |
| 2 | F3-a work profile git 작업본·스냅샷·diff 관측 | 코드 | 중간 |
| 3 | F2-a `approval.py`의 `timeout` 긍정 분류 차단 + 테스트 | 코드 | 작음 |
| 4 | F1-a TLS 종단·`allowed_hosts` 전제 문서화 (서비스 시작 전) | 문서 | 작음 |
| 5 | F3-b/d/e 비보장 사항 문서화, 확인 화면 개선 | 문서·UI | 작음~중간 |
| 6 | F1-b/c 로그인 잠금·CSRF 회전 | 코드 | 작음 |

## 6. 한계

- 코드 실행·재현이 없다. 모든 "방어됨"은 코드 읽기 기준이며, 라우트 전수에 대한 인증 누락 테스트는 하지 않았다. 읽은 범위는 `api.py`의 라우팅 전체(`/api/v1` 단일 진입점)이지만, `core.*` 각 메서드 내부의 권한 검사는 mutation·proposal·dispatch·invite 위주로만 확인했다.
- Codex·Claude 공급자의 실제 샌드박스 동작(특히 Windows의 읽기·네트워크 제한)은 확인하지 않았다 `[unverified]`.
- `mcp.py`, `worker.py` 본문, `http_runtime.py`는 정독하지 않았다.
- mcp-box 운영 인스턴스의 실제 설정은 11-10 읽기 전용 점검 범위(컨테이너·`/ready`·타이머)만 반영했고, 앱 환경변수와 시크릿은 열람하지 않았다.
