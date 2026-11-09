# ai-dev-director 정밀 분석 (2026-11-10)

- 대상: `https://github.com/digilogd-cmd/ai-dev-director` (MIT, public), `main`, 마지막 push 2026-11-07
- 취득: tarball 143개 파일(434 KB). 11-09 세션에서 받아 둔 사본과 **SHA-256 전수 비교 결과 차이 0건** — 같은 버전
- 방법: 정적 읽기. **저장소 코드·테스트·서버는 하나도 실행하지 않았다.** 아래 줄 번호는 이번에 읽은 사본 기준이며 실행으로 재현한 것이 아니다
- 읽은 범위: `core/safety.py`, `companion_server.py`, `dashboard_server.py`(핵심 핸들러·Origin 검사), `core/process_runner.py`, `core/env_utils.py`, `core/verifier.py`, `core/git_manager.py`, `agents/proposal_builder.py`, `core/file_lock.py`, `core/autopilot.py`, `core/result_manager.py`(apply), `core/orchestrator.py`(run/validate_post_build), `core/bootstrap.py`, `core/solomon_bridge.py`, `agents/antigravity_builder.py`(설정 변조부), 패턴 스캔 전체. **미정독**: `research_*`(TRIAD), `chat_manager`, `usage` 대부분, `console/*.js`, `dashboard_i18n.py`, 테스트 본문
- 근거 등급: 코드 줄 직접 확인 = High, 읽은 코드로부터의 추론 = Medium, 미확인 = `[unverified]`

## 1. 한 줄 평가

세 구독 CLI(Claude/Codex/Antigravity)를 HEAD·BUILDER·REVIEWER로 돌리는 로컬 오케스트레이터. **파일 쓰기 경로의 방어(Scope Guard, 제어면 차단, 해시 매니페스트 반영·롤백)는 설계가 촘촘하다.** 반면 **로컬 HTTP 제어면의 인증이 README 설명보다 훨씬 약하고, "AI가 AI의 범위 위반을 면제"하는 경로가 있으며, 검증 단계가 AI가 쓴 코드를 샌드박스 없이 실행**한다. 개인 PC 단독 사용 전제의 제품으로는 합리적이나, 공유 호스트·무인 운영·타인 코드 대상에는 부적합하다.

## 2. 구조

```
goal ─▶ HEAD(ORDER 작성) ─▶ entry gate ─▶ git worktree 생성(스냅샷 기반)
      ─▶ baseline 테스트 ─▶ BUILDER(전체 파일 JSON/edits 제안, 도구 사용 금지)
      ─▶ Director가 경로 검사 후 직접 기록 ─▶ post-build Scope Guard(git numstat)
      ─▶ money-code 스캔 ─▶ Verifier(빌드/테스트 실행) ─▶ REVIEWER ─▶ HEAD 판정
      ─▶ final PASS gate ─▶ (auto_apply) 해시 매니페스트로 원본에 반영 → 원본에서 재검증 → 실패 시 롤백
```

| 구성 | 규모 | 비고 |
|---|---|---|
| `dashboard_server.py` | 3,198줄 | HTTP·채팅·미션·상태 집계가 한 파일 |
| `core/orchestrator.py` | 1,895줄 | 상태 기계·장애 조치·게이트 |
| 상태 저장 | SQLite `data/director.db` + `data/runs/` | 소스 루트 하위, `.gitignore`로 제외 |
| 테스트 | 66개 파일, 12,479줄, `def test_` 549개 | README는 "604개", 전체 약 25분. 소스 안에 CI 설정 없음 |
| 의존성 | `pyyaml, click, pydantic, psutil, pytest` 전부 `>=` | 잠금 파일 없음 |

## 3. 잘 된 점 (우리 코드에도 가져올 가치가 있는 것)

1. **AI CLI는 도구 없이·읽기 전용 샌드박스로만 호출**: `codex exec --sandbox read-only -`, Antigravity `--sandbox`, 프롬프트에 "Do not use tools". 파일 쓰기는 Director만 한다 (`proposal_builder.py:143`, `research_runner.py:63-73`).
2. **제어면은 ORDER와 무관하게 거부**: `.git`·`.agents`·`.director_worktrees`·`GEMINI.md`는 허용 경로에 넣어도 쓰기 불가, 승인 불가 코드로 분류 (`safety.py:16-25, 327-329`). 승인 가능 목록은 **기본 거부 allowlist**.
3. **사후 검증이 AI 보고가 아니라 git diff 기준**: numstat·untracked·삭제를 직접 계산해 파일 수·diff 줄 수·함수/클래스 삭제까지 검사 (`safety.py:419-543`).
4. **반영(apply)의 원자성**: SHA-256 매니페스트 → 백업 → `os.replace` → 해시 재확인 → 원본에서 재검증 → 실패 시 롤백 → 감사 로그 (`result_manager.py:446-533`). 사용자가 중간에 고친 파일을 덮어쓰지 않는다는 점은 주석·설정(`config.yaml`의 `on_conflict: block`)으로만 확인했고 `apply_tree` 본문은 정독하지 않았다.
5. 단일 인스턴스 보장(`SO_EXCLUSIVEADDRUSE`, `dashboard_server.py:95-110`), 브리지 요청 `request_id` 멱등(`solomon_bridge.py:49-114`), 토큰 비교에 `secrets.compare_digest`. (리뷰어 fail-closed와 "정지는 정지"는 README 주장이며 해당 코드는 정독하지 않았다 `[unverified]`.)
6. **API 키 제거**: `*_API_KEY`를 환경에서 지운 뒤 CLI 호출 (`env_utils.py:6-22`). 단, 제거 목록은 5개 고정 (§5 L5).
7. **단위 테스트 중 실제 CLI 호출 차단 가드** (`process_runner.py:181-188`).

## 4. 발견 사항 (심각도순)

### F1 — 로컬 제어면의 인증이 README보다 약하다 · High(공유 호스트) / Medium(단독 PC)

- README: "상태를 바꾸는 요청은 로컬 출처 검사와 브리지 토큰을 요구".
- 실제: 토큰(`X-Solomon-Bridge`)을 요구하는 것은 `/ops/roles`·`/ops/missions/stop`·`/ops/shutdown`·`/ops/pick-folder`·`/bridge/*`뿐이다 (`companion_server.py:158-270`).
- **`/chat`(미션 시작), `/project`(대상 폴더 선택), `/run/approve`(HUMAN_REQUIRED 승인), `/result/apply`(AI 결과를 프로젝트에 반영), `/ops/mission/decide`는 Host/Origin 검사만 통과하면 실행**된다 (`dashboard_server.py:3066-3076, 3078-3152`). `companion_server`는 이 경로들을 그대로 `super().do_POST()`로 넘긴다 (`:271`).
- `is_request_origin_allowed`는 `Origin` 헤더가 **없으면 통과**시킨다 (`dashboard_server.py:125-130`). 브라우저의 교차 출처 POST는 막히지만 `curl`·스크립트·다른 로컬 프로세스·같은 PC의 다른 OS 사용자는 그대로 통과한다.
- 토큰 자체도 비밀이 아니다: `GET /ops/state`와 `GET /ops/roles`가 `csrf` 필드로 **브리지 토큰을 응답에 실어 준다** (`companion_server.py:106-108, 121-126`). 같은 조건(Host=localhost, Origin 없음)의 요청이면 누구나 받는다. 토큰 파일 `data/solomon-bridge.token`도 권한 제한 없이 기본 권한으로 생성된다 (`:278-281`).
- `validate_project_path`는 `.git`이 있는 폴더이면 어디든 허용하고, `allowed_roots` 제한은 Solomon 경로에만 있다 (`dashboard_server.py:158-174`). 기본 설정이 `auto_apply: on_pass`·`head_proxy: true`라서, 로컬 무인증 요청 하나로 임의 git 저장소에 대해 미션 시작 → 자동 반영까지 갈 수 있다 `[unverified: 실제 호출로 재현하지 않음]`.
- 막혀 있는 것: DNS rebinding(Host 고정), 외부 웹페이지의 응답 읽기(SOP), LAN 노출(127.0.0.1 바인딩 강제, `dashboard_server.py:3173-3174`).
- 권고: (a) 모든 상태 변경 엔드포인트에 토큰 요구, (b) 토큰을 응답으로 내리지 말고 정적 페이지에 서버가 주입하거나 `HttpOnly` 쿠키+`SameSite=Strict`로 교체, (c) `Origin` 없는 상태 변경 요청 거부, (d) 토큰 파일 0600, (e) `/project`에도 허용 루트 적용.

### F2 — AI가 범위 위반을 "사용자 대신" 면제한다 · Medium–High

- README: "Scope Guard — 빌더는 완화 불가". 빌더는 맞다. 그러나 HEAD 프록시(AI)가 아래 위반을 **자동 면제**한다 (`autopilot.py:146-150`, `orchestrator.py:736-744`):
  `Max files changed exceeded`, `Max diff lines exceeded`, `Dependency configuration modified`, `Critical build/architecture configuration modified`, `Permission configuration modified`.
- 즉 `package.json`·`requirements.txt`·`Dockerfile`·`Makefile`·`AndroidManifest.xml` 등의 수정이 사람 확인 없이 통과한다. 이후 §F3의 검증 단계가 그 파일을 읽고 실행하므로(`npm test`의 lifecycle script, Makefile 등) 효과가 합쳐진다.
- 위험 가정: HEAD가 오판하거나 프롬프트 주입(저장소 내용·리뷰 지적문)에 흔들리는 경우. `[unverified: 실제 주입 시나리오는 시험하지 않음]`
- 권고: 면제 가능 목록에서 의존성·빌드 설정·권한 파일을 빼고 사람 승인으로 되돌리거나, 최소한 면제 사실을 최종 PASS 화면에 눈에 띄게 노출.

### F3 — AI가 쓴 코드를 샌드박스 없이 실행한다 · Medium–High (설계상 선택, 문서화 부족)

- `Verifier._execute_cmd`는 worktree에서 `test_command`를 사용자 권한·네트워크 허용 상태로 실행하고, 환경은 API 키 5개만 제거한다 (`verifier.py:14-84`).
- **반영 후 재검증은 사용자의 실제 프로젝트 트리에서** 실행된다 (`result_manager.py:514-523`). 롤백은 `applied` 목록의 파일만 되돌리므로 테스트가 만든 부수 파일·외부 부수효과는 남는다.
- Scope Guard·money-code 스캔이 실행 전에 돈다는 점은 좋지만(`orchestrator.py:727-763`), 둘 다 정규식·경로 기반이라 실행 코드의 의미를 보지 못한다.
- 권고: 검증을 컨테이너/Job Object+네트워크 차단 환경으로 옮기거나, 최소한 반영 후 재검증을 worktree에서 한 번 더 한 뒤 원본에서는 읽기 전용 확인으로 대체.

### F4 — money-code 방어는 휴리스틱이며 "코드로 강제"라는 표현은 과하다 · Medium

- 탐지는 `order_send(`, `OrderSend(`, `create_*_order(`, `import stripe` 등 5개 정규식이며 추가된 줄/신규 파일만 본다 (`autopilot.py:107-141`). `getattr`·문자열 결합·`__import__`·직접 HTTP 호출·다른 SDK는 탐지 대상이 아니다. `.mq4/.mq5/.mqh`는 실행되지 않는다는 이유로 제외.
- 판단: 방어 계층의 하나로는 적절하나 README의 "Hard rules, enforced in code" 표에서 보장처럼 읽힌다. 입력 텍스트의 `SERIOUS_TERMS`(결제·실계좌 등) 부분 문자열 매칭도 같은 성격의 휴리스틱이다.

### F5 — 외부 반출과 "원본 무수정" 표현 · Medium

- **반출**: 빌더 프롬프트에 편집 가능 파일 최대 140,000자 + 참고 소스 60,000자를 통째로 넣어 외부 벤더 CLI로 보낸다 (`proposal_builder.py:143-157, 211-212`). 민감 파일 제외는 **경로 문자열**에 `secret/credential/token/private_key`가 들어 있을 때와 숨김 폴더·100KB 초과만 해당한다 (`:111-113`). 소스 파일 **내용**에 박힌 키는 걸러지지 않는다.
- **무수정 표현**: 원본을 "절대 직접 수정하지 않는다"고 하지만 실제로는 `.git/info/exclude` 수정(`git_manager.py:58-63`), `RUN-*` 브랜치·worktree 생성(`:66`), 스냅샷 커밋 객체를 사용자 저장소에 기록(`bootstrap.py:61-65`), `.git`이 없으면 `git init`(`:33`)을 수행한다. 정리는 `clean-worktrees` 수동 명령에 의존.
- 스냅샷 제외 규칙은 경로에 `secret/credential/token/.env/private_key`가 든 파일만 거른다(`bootstrap.py:54`). `settings.yaml`·`config.json`처럼 이름이 평범한 설정 파일은 스냅샷 대상이다.

### F6 — 프로세스·락 견고성 · Medium–Low

- `ProcessTreeRunner`: stdout/stderr를 **무제한 리스트에 누적**(`process_runner.py:223-230`) — CLI 출력이 폭주하면 메모리 증가. Windows에서 Job Object 없이 `taskkill /T`와 psutil에 의존하므로 부모가 먼저 죽거나 자식이 분리되면 누수 가능 `[unverified]`.
- `FileLock`: 소유자 PID가 살아 있으면 영구 비-stale로 취급하므로 **PID 재사용 시 락이 풀리지 않는다**(`file_lock.py:52-65`). 획득할 때마다 `atexit.register`를 누적(`:118`).
- Antigravity 도구 모드(레거시 경로)는 사용자 전역 `~/.gemini/antigravity-cli/settings.json`을 실행 중 덮어쓰고 복구한다 (`antigravity_builder.py:436-470`). 복구 로직은 정교하나 사용자의 동시 `agy` 세션과 충돌할 수 있다. 기본 설정(proposal 모드)은 이 경로를 쓰지 않는다.

### F7 — 기타 · Low

- 제공자 장애 분류가 CLI/모델 **출력 문자열 부분 일치**(`unauthorized`, `rate limit`, `timeout:` 등)에 의존 (`provider_manager.py:30-80`). 리뷰 본문에 같은 단어가 있으면 오분류로 장애 조치가 일어날 수 있다 `[unverified]`.
- 경로 비교가 `.lower()` 정규화라 대소문자 구분 파일시스템에서 `SRC/x`와 `src/x`를 같게 본다 (`safety.py:244-250`). 윈도우 우선 설계에는 맞지만 Linux/macOS 지원 주장(미검증)과 충돌.
- 레거시 명령 허용 검사는 `startswith("python")` 접두사 매칭이라 `python -c "..."`가 통과한다 (`safety.py:366-371`). 기본 proposal 모드는 호출하지 않는다.
- 반영 요청 멱등 키(`_RECENT_APPLY_REQUESTS`)는 프로세스 메모리라 재시작 시 사라진다. `datetime.utcnow()` 사용(Python 3.12+ 경고).
- `_read_form`은 `Content-Length` 상한 없이 읽는다 (`dashboard_server.py:2970-2976`) — 로컬 메모리 소진 가능.
- f-string SQL은 내부 컬럼명 조합이며 외부 입력은 아니다(`state_machine.py:361-366`, `chat_manager.py:70` 등). 위험으로 보지 않는다.
- 시크릿 하드코딩·`shell=True`·`pickle`·`yaml.load` 사용: 스캔 결과 **없음**. `exec`는 `scripts/test_role_cli_smoke.py` 1건(스모크 스크립트).

## 5. README 주장 대비 실제

| README 주장 | 판정 | 근거 |
|---|---|---|
| 종량제 API 금지, 키 제거 | 부분 확인 | 5개 변수 고정 제거. Bedrock/Vertex 등 다른 자격은 미처리 |
| 원본 무수정 | **과장** | F5 |
| Scope Guard, 빌더는 완화 불가 | 빌더에 한해 확인, **AI 프록시 면제 존재** | F2 |
| 리뷰어 fail-closed | 미확인 `[unverified]` | 리뷰어 어댑터·`apply_final_pass_gate`를 정독하지 않음 |
| 돈·자격증명은 사람이 | **휴리스틱** | F4 |
| 상태 변경에 로컬 출처+토큰 요구 | **부분만 사실** | F1 |
| 정지는 정지 | 부분 확인 | 정지 엔드포인트와 `resume_on_start` 설정은 확인(`companion_server.py:181-192, 283-285`). 재시작 후 STOPPED 유지 로직은 미정독 `[unverified]` |
| 604개 테스트, 593/596 통과 | 미검증 | 실행하지 않음. 파일 내 `def test_`는 549개 |

## 6. TalkAbout 적용 관점

- 이미 반영(11-09): 구성요소별 `lstat()` 경로 경계, launch 실패 시 정리 확인.
- 추가로 가져올 만한 설계: ① 제어면 경로를 ORDER와 무관하게 거부 ② 사후 diff 기반 범위 검사(보고가 아닌 관측) ③ SHA-256 매니페스트+롤백+감사 반영 ④ 승인 가능 코드의 기본 거부 allowlist ⑤ 요청 멱등 키 ⑥ 테스트 중 실제 CLI 호출 차단 가드.
- 따라 하지 말 것: ① 토큰을 응답으로 내려주는 방식 ② AI 프록시의 범위 위반 면제 ③ 원본 트리에서의 반영 후 검증 ④ 출력 무제한 누적 ⑤ PID 존재만으로 락 소유 판단.
- 라이선스: MIT. 코드 복사 없이 설계 아이디어만 쓰는 현재 방식(독자 구현)이면 고지 의무 이슈 없음.

## 7. 한계

- 코드 실행·테스트·서버 기동·네트워크 호출을 하지 않았다. F1·F2·F3는 코드 읽기만으로 추론했고 재현 증거가 없다.
- `research_*`·`chat_manager`·프런트엔드·테스트 본문은 정독하지 않았다. 해당 영역의 결함은 이 보고서에 반영되지 않았다.
- 줄 번호는 `main`(push 2026-11-07) 사본 기준이며 이후 변경은 반영되지 않는다. 저장소의 커밋 이력은 tarball에 없어 조사하지 않았다.
- 심각도는 "개인 PC 단독 사용" 대비 "공유 호스트·무인 운영"을 나눠 매겼으며, 위협 모델은 저장소가 명시하지 않아 추정이다.
