# TalkAbout session log

## 2026-10-26 12:30 UTC — 공유 입력 컨텍스트 재구현 / shared input context, reimplemented

`feat/paired-local-runner`(2026-10-24 WIP)의 남은 판단을 **재구현**으로 처리했다. 그중 collaboration 부분을
현재 구조에 맞춰 새로 구현했고, runner 예약·원장·vault·bridge는 별도 트랙으로 남겼다.

| 요소 | 내용 |
| --- | --- |
| `talkabout/collaboration.py` (신규) | `shared_context`(우선순위 선택·UTF-8 절단·manifest), `add_file_sources`, `fit_context`(직렬화 한도), `validate_manifest`(서버 재검증), `project_manifest`/`project_task`(열람 경계 redaction) |
| `worker.py` | 인라인 `inputs`/`artifact_proposals` 대신 `shared_context` 사용, 첨부 발췌를 manifest에 기록, dispatch 전 `fit_context`, 완료 시 `input_manifest` 전송 |
| `core.py` | 완료 시 manifest 검증·`task.input_manifest`/`message.input_evidence` 기록, artifact version에 `sources`·`input_references`·`version_revision` 저장, 공개 투영에서 참조 redaction |
| 증거 라벨 | `prepared_worker_input_not_model_read_receipt` / `worker_reported_server_validated_refs_not_model_read_receipt` / 구형 worker는 `legacy_input_unverified` |

### 검증

- `tests/test_collaboration_context.py` **10건**: UTF-8 경계, 우선순위(고정·채택), omission 기록, redaction,
  한도 초과 시 실패, **위조 참조 5종 거절 + 방 불변**, 구형 완료 경로, worker 전송→서버 검증, version revision.
- 관련 모듈 회귀 8개 파일 **106 passed / 4 skipped**.
- **주장하지 않음**: 모델이 입력을 읽었다는 증거가 아니다. 원본 WIP의 runner/DB/API 트랙은 미구현.
- 남은 판단: PR #36은 #37로 대체되어 닫았고, 브랜치는 runner 트랙 아카이브로 유지. 머지 후 CI: PR head run `40321168568`, merge `f72cd16` run `40321555761` 모두 두 잡 success.

## 2026-10-26 11:45 UTC — 저장소 정리: 브랜치·draft PR·보호 설정 / repository hygiene

점검에서 나온 정리 항목을 순서대로 처리했다. 코드 변경은 없다.

| 조치 | 내용 |
| --- | --- |
| 설정 | `delete_branch_on_merge` **켬**, `main` 보호 설정(필수 체크: `safe-regression`·`posix-containment`, force push·삭제 금지, 관리자 우회 허용) |
| draft PR | #9–#12 닫음 — 네 건 모두 head 브랜치가 이미 main에 머지된 **중복 draft** |
| 원격 브랜치 | **16개 삭제** — 머지 완료 11개 + 대체된 미머지 5개(변경 경로가 모두 main에 이미 존재) |
| 남긴 브랜치 | `main`, `feat/paired-local-runner` |
| 미머지 WIP | [PR #36](https://github.com/yjj3019/TalkAbout/pull/36) **draft**로 열어 결정 대기 |

삭제한 브랜치 tip(복구 참고):

```text
b9e62ef chore/completion-verification        a769c1a feat/agent-community-flow
deaa5e0 feat/initial-setup-guide             cdb5b66 feat/internal-broker-attachments
dcc6303 feat/onboarding-readiness            1ca670f feat/room-invite-flow
a4fde42 feat/runtime-connection-onboarding   e4824d6 feat/scoped-session-continuity
d09dcc2 feat/text-attachment-vertical        c4621cb fix/native-windows-environment
a9feb77 test/mcp-tool-contract               fab10fb chore/operating-deployment-guide
cd2870d feat/automatic-db-install            1eeb747 feat/first-owner-setup
08318f4 feat/p0-core-worker                  7ae0821 feat/podman-packaging
```

**남은 판단**: `feat/paired-local-runner`(2026-10-24 스냅샷)는 main 위로 rebase하면 14파일 25 hunk가 충돌한다.
겹치는 영역을 서로 다르게 구현했기 때문이다(프롬프트 빌더·예산 회계·메시지 재시도 UI). 이 브랜치의 테스트는
main 기준으로 **실행된 적이 없다(NOTRUN)**. 재구현·부분 채택·폐기 중 하나를 결정해야 한다.

## 2026-10-26 08:00 UTC — 웹 UI: 산출물·작업 프로필 노출 / expose work capabilities in the UI

W0/W1이 CLI·API에만 있던 것을 화면에서 쓸 수 있게 했다.

| 위치 | 변경 |
| --- | --- |
| 방 생성 폼 | `deliverable` 선택(대화만 / 분석 / 변경 제안(diff)). 선택한 경우에만 요청 본문에 포함 |
| 방 헤더 | 산출물 배지 표시(`deliverable`) |
| 연결 마법사 1단계 | **로컬 작업 허용(opt-in)** 체크박스 + 작업 디렉터리 입력. 둘 다 유효할 때만 생성 명령에 `--work-profile --work-workspace '<경로>'` 추가 |
| 안전장치 | 기본 꺼짐 · Codex에서만 표시 · 경로가 안전 문자 집합을 벗어나면 플래그 미추가 + 경고(셸 주입 방지) |

### 검증

- `tests/test_connection_ui_wizard.mjs`에 3건 추가(총 **51건**), Node 9개 파일 **209 passed**.
- 브라우저 실사용 캡처는 하지 않았다(합성 DOM). CI의 표준 러너가 같은 노드 테스트를 실행한다.

## 2026-10-26 07:30 UTC — W1: opt-in 로컬 작업 프로필 / work profile

방에서 **실제 코딩·분석**이 가능하도록 소유자가 명시적으로 켜는 작업 프로필을 구현했다. 기본은 꺼짐이고 sandbox가 아니다.

| 항목 | 내용 |
| --- | --- |
| CLI | `--work-profile --work-workspace <dir>` 함께 필요. 현재 **Codex `exec-json` 전용**, Claude/Grok/app-server는 거절 |
| 격리 | Codex sandbox `read-only` → `workspace-write`. shell·patch 도구만 켜고 **MCP·hooks·plugin·skill discovery·web search는 계속 끔** |
| cwd | 소유자 지정 디렉터리. 런타임은 생성·삭제하지 않음(`_FixedWorkspace`) |
| 거절 | 없음 / 파일 / symlink / 홈 / 파일시스템 루트 / TalkAbout 소스 트리(조상·자손) / 쓰기 불가 |
| 프롬프트 | 작업 디렉터리 경로와 "그 밖은 건드리지 말 것, 플랫폼은 아무것도 적용하지 않는다"를 앞에 붙임 |
| 증거 | 서버는 무엇도 검증하지 못한다. 기여는 `worker_reported_runtime`으로만 남고 채택은 사람이 한다 |

### 검증 (합성만)

- `tests/test_work_profile.py` **17건** + `tests/test_work_profile_cli.py` **5건** 통과.
- **실제 공급자 작업 턴은 NOTRUN** — 예산·동의 필요. 시험 시에는 변경이 남지 않는 임시 디렉터리를 workspace로 두고 `--max-model-calls`/`--max-seconds`를 작게 잡는다.
- sandbox·egress 제한은 **미구현**이며 [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 관문을 그대로 유지한다.

## 2026-10-26 07:00 UTC — W0: 방 산출물·patch artifact / work deliverables without execution

방이 단순 채팅이 아니라 **검토 가능한 실제 작업 산출물**을 갖도록 W0을 구현했다. 실행 채널은 열지 않았다.

| 요소 | 구현 |
| --- | --- |
| 방 `deliverable` | `analysis` / `change_proposal`(기본 없음). `core.create` 허용 필드·`new_room` 검증·`public_room` 노출 |
| 프롬프트 | `agent_runtime.prompt_text`가 산출물별 결과 형식을 요구. 두 산출물 모두 "플랫폼은 실행하지 않는다"를 명시 |
| patch artifact | `change_proposal` 방의 창작자 버전은 `kind: patch`. diff 형태가 아니면 `invalid_patch`(422)로 거부 |
| 채택 | 기존 사람 채택 그대로. **서버는 patch를 적용하지 않는다** |

### 검증

- `tests/test_work_deliverables.py` **13건** 통과(방 생성·검증, 프롬프트 형식, diff 거부/수용, patch kind).
- 실제 에이전트가 diff를 제대로 산출하는지는 **NOTRUN**(공급자 예산 필요). 합성 프레임으로만 검증했다.
- 다음 단계 W1(소유자 opt-in 로컬 도구 실행)은 [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 관문을 따로 통과해야 한다.

## 2026-10-26 06:30 UTC — POSIX 격리 계층(C1) + Linux CI 잡 / POSIX containment layer

세 번째 트랙 후보. **POSIX 실행은 여전히 차단**이며, 이번 작업은 격리 계층만 구현하고 활성화는 보류했다.

### 구현 (C1)

`talkabout/processes.py`에 `stop_posix_group`과 POSIX 분기를 추가했다. Windows 경로는 그대로다.

| 속성 | 구현 |
| --- | --- |
| P1 그룹 격리 | `start_new_session=True`로 자식을 새 세션·그룹 리더로 띄우고 pgid 소유를 확인 |
| P2 부모 우선 종료 | 부모 SIGTERM → grace → 그룹 SIGTERM → 그룹 SIGKILL |
| P3 회수 증명 | SIGKILL 후 **생존자 재확인이 비어야만** 성공. 읽을 수 없으면 `process_exit_unconfirmed` |
| P5 PID 재사용 가드 | `/proc/<pid>/stat`의 start time을 launch 시 기록하고 신호 직전에 대조 |
| P6 상한 | 사다리 전체가 유한(최대 ~6초), 자기 그룹 신호 금지, `shell`/`creationflags` 옵션 거부 |

### 검증

- `tests/test_posix_containment.py` **9건** — **WSL Ubuntu / Python 3.12에서 3회 연속 9 passed**(flakes 없음).
  로컬 Windows에서는 `skipif`로 9건 skip.
- **CI 결과**: PR #32 head `d2f8103` run `40235059346` — `posix-containment` **pass(20초, ubuntu-latest)**, `safe-regression` **pass(2분27초, windows-latest)**. merge `e719365` run `40235300612`도 두 잡 모두 pass.
- **CI 사실 정정**: 이 저장소의 CI는 `windows-latest` 하나뿐이어서 POSIX 코드는 **어떤 CI에서도 실행되지 않았다**.
  `.github/workflows/safe-regression.yml`에 `posix-containment`(`ubuntu-latest`) 잡을 추가해 이 9건을 실행한다.
- 계약 테스트 1건 갱신: `test_posix_detached_descendant_program_cannot_launch` → `test_posix_execution_path_fails_closed_before_launch`.
  격리 계층은 이제 POSIX를 지원하지만 **제품 경로는 여전히 fail-closed**라는 새 계약을 검사한다.
- Windows 전체: **719 passed / 4 failed / 32 skipped** — 실패 4건은 `EXCLUDED` 브라우저 3 + 샌드박스 named-pipe 1.

### 보류한 것 (P4 · C2)

supervisor가 SIGKILL로 죽으면 POSIX에 Job Object 등가물이 없어 자식이 남는다. 이 구멍(P4)이 닫히기 전에는
`cli.py`·`adapters.py`의 POSIX 거부를 해제하지 않았다. 후보 두 가지(`prctl(PR_SET_PDEATHSIG)`의 스레드 발화 위험,
부모 감시 escort 프로세스)는 [POSIX_CONTAINMENT_PLAN.md](POSIX_CONTAINMENT_PLAN.md) §7에 정리했고 채택하지 않았다.
따라서 **이 계층은 제품 경로에서 도달 불가능**하고, "POSIX 지원"이 아니다.

## 2026-10-26 00:50 UTC — 문서 SSOT gate + provider 한도 어휘 / docs gate and typed limits

다음 트랙 후보 중 **문서 SSOT gate**와 **provider 실패 어휘 타입화**를 반영했다. 합성 검증만 했고 실제 공급자 재시험은 하지 않았다(NOTRUN).

### 1) 문서 SSOT gate

`tools/docs_check.py` — 문서 드리프트를 CI에서 막는다.

| 검사 | 내용 |
| --- | --- |
| `link` | README·`docs/*.md`의 상대 링크가 실존하는 파일을 가리키는가 |
| `anchor` | `file.md#anchor`가 해당 문서의 실제 제목 slug로 해석되는가(GitHub 규칙) |
| `path` | 문서가 백틱으로 인용한 저장소 경로(`talkabout/…`, `tests/…` 등)가 존재하는가 |
| `orphan` | 모든 `docs/*.md`가 README 또는 `docs/` 안에서 링크되는가 |

- `tests/test_docs_contract.py`가 게이트를 CI에 묶고, **검사기가 비어 있지 않음을 시드 위반으로 증명**한다(깨진 링크·오래된 경로·없는 앵커 각각).
- 알려진 예외 목록 `KNOWN`은 **줄어들기만** 한다(현재 0건).
- 이 작업에서 실제 결함 1건을 고쳤다: `docs/RUNTIME_IMPLEMENTATION_VALIDATION.md`가 문서 지도에 없어 고아였다 → `docs/STATUS.md` §5에 추가.
- 검사기 오탐 2건도 수정: 한국어 제목의 slug(공백 하나당 하이픈 하나, `/` 제거 후 남는 이중 하이픈 유지), MCP 메서드명 `tools/list`(경로 아님).

### 2) provider 한도·실패 어휘 타입화

- `rate_limit_event`를 **닫힌 집합**으로만 분류한다: `rejected` / `allowed` / `unknown`.
  `rejected`일 때만 `runtime_usage_limit_reached`로 turn을 중단하고, `allowed`·`unknown`은 turn을 계속한다.
- 근거는 `last_diagnostics.usage_limit_events`·`usage_limit_evidence`에 남는다(상태 토큰·존재 키·`resets_at`만, 자유 텍스트·provider 메시지 없음).
- codex `error`/`turn.failed`도 닫힌 짧은 토큰(`usage_limit_exceeded`, `quota_exceeded` 등)만 `runtime_usage_limit_reached`로 타입화한다. 자유 텍스트 메시지는 증거로 쓰지 않는다(`limit_failure_token`은 64자 초과 토큰을 무시).
- **한계**: status 어휘는 실제 공급자 프레임에서 확인한 값이 아니라 **닫힌 집합 가정**이다. 모르는 값은 전부 `unknown`으로 흘려 turn을 계속하므로 어휘가 틀려도 건강한 turn을 중단시키지 않는다. 어휘 확정은 실제 시험에서 `usage_limit_evidence`로 해야 한다(NOTRUN).

### 검증 (합성만)

- Python **719 passed / 4 failed / 23 skipped** — 실패 4건은 `EXCLUDED` 브라우저 항목 3 + 이 세션 샌드박스 named-pipe 1.
- 신규 테스트 **16건**: 문서 계약 7 · 한도 어휘 9(`tests/test_agent_runtime.py`).
- 표준 러너는 CI에서만 완주한다([STATUS](STATUS.md) §2.1). 이번 변경은 PR #31 CI로 확인했다: head `33bd91d` run `40208480239` **success**, merge `66075d1` run `40208764352` **success** (guarded Python + 실제 임시 PostgreSQL + synthetic UI 계약).

## 2026-10-26 00:20 UTC — 서버 측 결함 3건 수정 / server-side correctness fixes

같은 분석에서 남겨둔 항목 중 **실제 결함으로 확인된 3건**만 반영했다. 합성 검증만 했고 실제 공급자 재시험은 하지 않았다(NOTRUN).

| 결함 | 이전 동작 | 수정 |
| --- | --- | --- |
| runtime 이전 실패가 모델 호출 예산을 소모 | `attempt`(claim마다 +1)로 만료를 판정해, 한 번도 호출하지 못한 task 3회 claim 후 `expired` | 만료는 **runtime 도달 시도 수**(`_started_attempts`)로 판정. claim만 반복한 task는 다시 `queued`로 돌아가고, `attempt >= attempts×2` 상한에서만 종료 |
| 전원 unavailable 방의 무음 정지 | `eligible`이 비면 `schedule()`이 조용히 `return` → 방이 active인 채 영구 대기 | `task_seconds×2` 대기 후 `room.stalled`(1회, `mode_status=stalled`), 그 2배에서 `room.closed`. agent 복귀 시 stall 해제 |
| resync 커서를 배달 증거로 인정 | snapshot/rebase 커서의 offset 점프를 그대로 `delivered_seq`/`read_seq`로 기록 | 커서에 `skipped_rooms`를 남기고, 해당 방 ack은 `delivery_unproven`(422). 명시적 `join` 재동기화 필요 |

- `record_task_event`는 이제 각 stage에 `attempt`를 기록하고 `_started_attempts`를 추적한다(중복 heartbeat stage는 1회만 계산).
- `public_task`가 `_` 접두 키를 일반적으로 숨긴다(내부 lease/증거 필드 유출 방지).
- 새 이벤트 타입 `room.stalled` 추가 — `emit`은 타입을 검증하지 않으므로 additive이며, 클라이언트는 모르는 타입을 무시한다.

### 검증 (합성만)

- Python **703 passed / 4 failed / 23 skipped**. 실패 4건은 `EXCLUDED` 브라우저 항목 3건 + 이 세션 샌드박스의 named-pipe 제한 1건. **같은 환경에서 실행마다 브라우저·MCP 픽스처 항목의 성패가 달라지므로**(직전 실행 7건 실패) 실패 수를 회귀 지표로 쓰지 않는다.
- 신규 테스트 **8건**(`tests/test_scheduler_bounds.py`): 예산 분리 4 · stall 2 · ack 증거 2.
- 검증 하네스는 직전 커밋과 동일한 비표준 방식이다([STATUS](STATUS.md) §2.1).

### CI 확인 — 표준 러너 통과

로컬 샌드박스에서는 표준 러너가 완주하지 못했지만, **GitHub Actions에서는 표준 러너로 통과했다.** 이 실행이 이번 변경의 기준선이다.

| 실행 | 대상 | 결과 |
| --- | --- | --- |
| [`40206446277`](https://github.com/yjj3019/TalkAbout/actions/runs/40206446277) | PR #30 head `d5a381b` | **success** — guarded Python + 실제 임시 PostgreSQL + synthetic UI 계약 |
| [`40206504760`](https://github.com/yjj3019/TalkAbout/actions/runs/40206504760) | merge commit `7e7a0aa` (`main`) | **success** |

로컬에서 보였던 4건의 실패(브라우저 픽스처 3 + named-pipe 1)는 CI에 나타나지 않았으므로 **이 환경의 제약**이었음이 확인된다. 다만 **CI 통과는 릴리스 관문 통과가 아니다**: 실제 공급자 재시험·S2·운영 배포는 여전히 NOTRUN이다.


### 하지 않은 것 (NOTRUN)

실제 공급자 재시험, PostgreSQL 통합, S2 2-runtime 대화, POSIX 실행 격리, quota 4분류·인증 3값·구조적 로깅, 문서 SSOT gate.

## 2026-10-25 23:40 UTC — 3-저장소 분석 기반 개선 반영 / improvements from external review

외부 저장소 3개(Orca · opencodex · hermes-agent) 정밀 분석에서 도출한 개선안 중 **합성 검증이 가능한 항목만** 반영했다. 실제 공급자 시험은 하지 않았다(NOTRUN).

### 반영한 것 (코드)

| 영역 | 변경 | 근거 |
| --- | --- | --- |
| ACP 클라이언트 | 대기 중 요청에 속하지 않는 응답 프레임을 fatal(`runtime_protocol_mismatch`)이 아니라 **격리**로 처리. 개수와 **구조만**(id 타입·필드명·error/result 유무) 진단에 남기고 turn은 계속한다 | `talkabout/agent_runtime.py` `_quarantine_response` |
| 유한 종료 | 타임아웃 탈출로가 `runtime.shutdown()`을 **무제한** 대기하던 것을 5초 escort로 제한. 확인 못 하면 `runtime_stopped=False`(=미확인) | `talkabout/runtime.py` `bounded_shutdown` |
| journal stop 플래그 | 읽기 실패를 stop으로 승격하지 않는다: BUSY/LOCKED만 유한 재시도, 손상·기타 오류는 **unknown**(=정지 아님). 폴링 20ms→200ms. 큐를 못 읽으면 `stop_unconfirmed` | `talkabout/worker.py` `_stop_flag` |
| 승인 종료 조건 | `responder_available=False`인 write는 `awaiting_approval`로 세우지 않고 **즉시 denied**(`no_user_can_answer`). `expire_pending()`으로 `awaiting_approval`이 요청 만료를 넘기지 못하게 한다 | `talkabout/tool_broker.py` |
| 첨부 저장소 | Windows에서 `mode=0o700`을 전달하지 않는다. Windows는 mode를 무시하는데, 제한 토큰에서는 그 값이 **쓸 수 없는 디렉터리**를 만든다 | `talkabout/text_files.py:36-48` |
| journal SQLite | `storage_health`로 링크된 SQLite의 WAL-reset 취약 범위를 판정하고 프로세스당 1회 경고. **PRAGMA는 바꾸지 않는다** | 신규 `talkabout/storage_health.py` |
| 웹 접근성 | `prefers-reduced-motion` 블록 안에 잘못 들어가 있던 `.stage-caption` pill 규칙 제거, `body::before`·`.motes`·`.prop-lights`·`.live-dot` 애니메이션 정지 | `talkabout/web/style.css:1692-1700` |
| 로컬 실행 | 증거 디렉터리를 `TALKABOUT_CI_RESULTS`로 바꿀 수 있게 함(기본값·동작 동일) | `tools/run_safe_regression.py` |

### 검증 (합성만)

- Python: **692 passed / 7 failed / 23 skipped**. 실패 7건 중 6건은 `tools/run_safe_regression.py`의 `EXCLUDED` 목록에 있는 브라우저·MCP 프로세스 항목, 1건은 이 세션 샌드박스의 named-pipe 제한(`multiprocessing/connection.py:614` WinError 5). 스킵 23건 = PostgreSQL 픽스처 미제공 22 + POSIX 전용 1.
- Node: 9개 파일 **206 passed** (`node <file>` 인프로세스 실행).
- 신규 테스트 **63건**: escort 7 · storage_health 34 · 승인 8 · stop 플래그 8 · ACP 격리 3 · 첨부 저장소 3.
- 계약 테스트 2건 갱신: `test_grok_unexpected_prompt_response_id_is_quarantined_not_fatal`(구: `..._fails_closed`), `test_stop_read_closes_after_query_error_without_claiming_a_stop`.

### 검증 환경의 한계 (제품 결함 아님)

`.ci-results`가 이전 샌드박스 실행이 만든 0o700 DACL 때문에 읽히지 않고, 같은 이유로 `tempfile.mkdtemp`/`TemporaryDirectory`가 만든 디렉터리에 접근할 수 없다. `node --test`는 자식 프로세스 파이프가 막혀(`spawn EPERM`) 실행되지 않는다. 그래서 **`tools/run_safe_regression.py`를 그대로 돌리지 못했고**, 동일 pytest 플래그 + 로컬 전용 `sitecustomize`(Windows에서 무시되는 mode 인자 제거)로 우회했다. CI의 표준 명령은 변경되지 않았다.

### 하지 않은 것 (NOTRUN)

실제 Codex/Claude/Grok 재시험, Grok 기본 lease·반복 turn, PostgreSQL 통합 재실행, 2-runtime 대화(S2), POSIX 실행 격리, 문서 SSOT gate, quota 4분류·인증 3값·구조적 로깅 도입. 이 항목들은 [RELEASE_GAP_MATRIX.md](RELEASE_GAP_MATRIX.md)의 관문을 그대로 유지한다.

## 2026-10-18 22:14 UTC — WIP planning checkpoint / 구현 전 종료

사용자가 오늘 작업을 종료하도록 요청하여 기능 구현과 추가 테스트를 시작하지 않고 이력만 보존했다. 아래 항목은 재개 계획이며 구현 완료를 뜻하지 않는다.

### Branch and changes

- Follow-up branch: `feat/scoped-session-continuity`.
- Base and HEAD before this documentation checkpoint: `c4621cb32d0fc90f3280ec546197861247470aa2`.
- A separate worktree was prepared to preserve the existing PR12 checkout and its changes.
- Application/test changes: **none**. The only checkpoint change is `docs/SESSION_LOG.md`.
- This checkpoint is WIP documentation only. No follow-up implementation PR was created. Existing PR12 remains separate; merge and deployment were not performed.
- No repository `AGENTS.md` or project skill files were found in the inspected project scope.

### Completed before stopping

- Confirmed the clean starting tree and the `c4621cb` source baseline; created the follow-up branch.
- Inspected the existing CLI, `StructuredRuntime`, `RoomRuntimePool`, `ConnectorLease`, journal, and connection-check implementation.
- Read the [official app-server contract](https://learn.chatgpt.com/docs/app-server). Initialization, thread/turn lifecycle, interruption and account metadata will be relevant to the proposed adapter changes.
- Reported the intended additive CLI/local-journal scope before implementation. Public REST API changes are not currently planned.
- Identified an account-binding design limit: the official account response examples include nullable email/plan metadata and do not promise an immutable account identifier. A metadata digest must not be described as proof of an identity or workspace change that the provider does not expose. The acceptable binding policy still needs review.

### Not implemented / next resume steps

1. Separate connection-check and room-conversation budget reporting. Keep the existing default total ceiling of one call; do not increase paid calls automatically. Decide the additive CLI options and KO/EN next-action messages before editing.
2. Design opt-in durable provider-session records in the local journal. Bind restoration to account/profile, agent credential, room, generation and membership scope; retain unknown-turn latches, fence concurrent owners using the existing journal lease, and never store raw credentials, authentication/session-file contents or email values.
3. Decide and document the account identity boundary first. Missing or changed identity evidence must fail closed rather than silently resume or start another conversation. Do not claim that an email/plan digest detects every account/workspace change.
4. Keep Codex exec as the default. Review the existing app-server implementation before adding an explicit CLI/feature gate. Cover initialize/initialized, thread start/resume, correlated turn IDs, terminal results, interruption and process cleanup. Do not add full-access or permission-skipping modes or take ownership of CLI authentication.
5. Start with fake/subprocess targeted tests for independent connection-check and first-response budgets, worker restart, account/room isolation, duplicate prevention, cancellation, cleanup and unknown results. Run broader tests only when the eventual code changes justify them.
6. Preserve implementation work on this follow-up branch and prepare a Draft PR against the intended review baseline when there is reviewable code. Do not merge or deploy automatically.

### Validation status

- **This follow-up session:** no application, targeted, full Python, Node UI or Chrome tests were executed. No new test result is claimed.
- Documentation-only closeout checks: inspect the changed file, validate local links, and run `git diff --check` before committing.
- Historical `c4621cb` result: Python **496 passed / 1 skipped**, UI Node **136 passed**, Chrome **5 passed** as a subset of that one Python full run. The collector failure was recovered from completed artifacts; observer warnings remain documented. These are prior results, not a new validation of the proposed session-continuity features.
- Source: [controller adoption and full validation](validation/controller-adoption-full-review-2026-10-18.json).

### Execution boundary and cleanup

- Actual provider calls and new trial credentials in this follow-up session: **zero**.
- The proposed fourth real trial was previously rejected before launch by automatic approval review. No fourth reservation, model-entry marker or fixture manifest was created. Prior conservative counts remain Codex **3**, Claude **0**, with **3** prior trial credentials in total; the third native start remains unknown.
- Do not retry that blocked action, change authentication/billing/security settings, create a new execution route/thread to bypass review, or treat a synthetic success as real provider validation.
- Actual connection receipt, first real room response, real reconnect and the multi-runtime PoC remain **UNVERIFIED**.
- No PG/browser/provider/test fixture was started for this implementation follow-up. The owned idle Python editing session was exited; other user processes and data were not changed.
- Historical ledgers/latches and the existing PR12 worktree were preserved. The checkpoint is to be saved as a WIP documentation commit on the branch above; its exact resulting SHA is recorded by Git and in the closeout report.

### English handoff

Stopped at the user's request before implementation. Only a branch/worktree and a reviewed plan exist. Resume with the budget split and account-binding decision, then implement scoped persistence and the explicitly selected app-server path behind a feature gate. Use synthetic targeted tests first. Existing exec behavior, public API contracts, historical uncertainty records, provider authentication and paid-call defaults must remain protected.

## 2026-10-19 — scoped Codex continuity implementation increment

Resumed the small implementation unit after the preserved planning checkpoint. Added opt-in local Codex checkpoints with owned writes, scoped identity/profile/credential fencing, explicit unavailable-identity refusal, unknown-outcome no-replay protection, safe status and an explicit experimental app-server selection gate. Added a room-purpose call ceiling inside the unchanged total default of one. Existing Worker saved results resend the same completion key without model recall. No public server API/schema or dependency change.

Final targeted regression: **174 passed / 1 existing POSIX skip**, seven selected files. Earlier added recovery-test fixture failed because its wrapper omitted params; corrected, then 24 new-file cases and the final selected regression passed. Existing Starlette deprecation warning remains. Full suite was not repeated; earlier c462 results above remain historical. See [validation record](validation/scoped-session-continuity-2026-10-19.json) and [behavior/limitations](CODEX_SESSION_CONTINUITY.md).

Actual provider calls and new actual trial credentials: **zero**. Real account continuity/reconnect and multi-runtime PoC remain unverified. The native adapter supplies no verified stable account subject, so automatic resume stays blocked rather than inferring identity from email/auth mode/home. Earlier denied real trials were not retried; all five preserved trial-record hashes match. No merge, deployment, authentication or billing change. The prior PR12 worktree remains at c4621cb32d0fc90f3280ec546197861247470aa2.

## 2026-10-19 — local full regression and cancellation-state correction

Continued locally from 8b79853745ab7ffdd771e4efff3c13069db243a3 with publication blocked. Review reproduced an audit-state defect: a prior completed turn could be recorded as the terminal outcome of a new turn cancelled during preparation or failing before dispatch. Resetting last_outcome after checkpoint begin prevents this; two additional synthetic cases verify unknown state and no replay. The focused session test file passed all 18 cases.

Full Python regression: **505 passed / 18 skipped / 0 failures / 0 errors** across 523 collected cases. Seventeen skipped cases are explicitly **NOTRUN PostgreSQL** because no fresh isolated fixture config was supplied; no existing DB was probed. The remaining skip is POSIX-only on Windows. Installed Chrome **5 passed**, included in Python totals; Node UI **136 passed / 0 skipped / 0 failures**. The existing Starlette deprecation warning remains. This is separate from the previous 174-case partial validation, and does not establish PostgreSQL or real-provider acceptance.

The first full attempt stopped after 226 passes and 3 skips because the external progress observer used test-mutated sys.argv; its exit was 3, not success. Capturing the output path fixed the observer, and the replacement full run completed. No product assertion failures caused that interruption. Python account-file/process/socket guards, fresh browser profiles and fixture-origin routing kept execution synthetic and loopback-only. Existing five preserved trial-record hashes still match. No installation, actual provider call, new actual trial credential or remote push/PR retry was performed.

Evidence: [full regression record](validation/scoped-session-full-review-2026-10-19.json). User journey and current limitations: [continuity readiness](CODEX_CONTINUITY_REVIEW.md). The next local commit adds this correction and evidence; it preserves 8b798537 as its ancestor rather than amending or rewriting it. Actual stable identity, first external response, follow-up and restart restore remain unverified; automatic resume remains blocked.

## 2026-10-19 — local recovery guidance

Continued from 193fd60 with remote publication and real provider tests blocked. Existing journey states already distinguish registration, invitation, recent connector evidence and a worker-reported room response. The remaining local UX gap was a generic worker_failed exit and unexplained session reason codes. Added fixed KO/EN session-recovery reasons, next actions and a same-config offline status example to local CLI output. Unknown exception text is never echoed. Existing UI recovery copy points to local terminal status; exact local checkpoint causes are not sent through the unchanged public API. Resume policy, credential/room scope, budgets and retry behavior are unchanged.

Latest targeted Python: **47 passed / 0 failures / 0 skipped**. All seven synthetic Node UI suites: **137 passed / 0 failures / 0 skipped**. Prior 193fd60 full Python 505/18 skip, Chrome subset 5 and Node 136 are historical, not rerun results for this increment. No full Python, Chrome, PostgreSQL or real-provider run was performed in this increment. Existing Starlette deprecation warning remains. Five preserved trial-record hashes match. Actual provider calls, new actual trial credentials and remote push/PR attempts: zero.

Evidence: [recovery guidance validation](validation/recovery-guidance-review-2026-10-19.json). Current user journey, safe inspection and remaining gates: [continuity readiness](CODEX_CONTINUITY_REVIEW.md). Preserve both previous commits as ancestors; no history rewrite, merge or deployment.

## 2026-10-19 — remaining PostgreSQL fixture verification

Confirmed unchanged application HEAD 9f68f602cc70b80224991c9c18c846b6ed8c434f and a clean starting tree. No AGENTS.md or project skill files were found in the inspected scope. Reused existing local PostgreSQL 18.6 test binaries, initialized a new owned data directory and verified actual data ownership and 127.0.0.1-only listening before selecting the seventeen previously skipped cases. **17 passed / 0 failures / 0 errors / 0 skips**, test exit 0; owned init/test/server Jobs all stopped. Five prior trial-record hashes match. Earlier full Python 508/18-skip, Chrome subset 5 and Node 137 remain a separate invocation; no full regression was repeated.

POSIX remains NOTRUN: WSL launchers and distribution names exist, but there is no verified approved disposable POSIX test workspace with offline dependencies. No distro startup, host mount, personal Linux data inspection or remote access was performed. No installation, existing DB use, actual model call, actual credential issuance, push/PR retry, merge or deployment occurred.

Documentation changes only: added a narrow [reproduction and release-gate procedure](REMAINING_VALIDATION.md), recorded [PG evidence](validation/remaining-postgres-review-2026-10-19.json), clarified continuity readiness and corrected stale deployment text claiming push/merge authorization and omitting synthetic Chrome evidence. The original historical validation records remain intact. A follow-up documentation commit preserves the tested implementation history.

## 2026-10-25 — verification completion, PostgreSQL in CI, first actual Codex contributions

Branch `chore/completion-verification` (merged into `main` by fast-forward push; PR #16 closed as merged). No history rewrite.

### Code and tools

- `talkabout/cli.py`: a spent `--max-model-calls` ceiling stops before the worker and reports `increase_max_model_calls` with KO/EN next-step text. Cost defaults unchanged.
- `talkabout/agent_runtime.py`: Codex 0.164.0 emits `item.completed`/`item.type=error` (an under-development-feature advisory) before `turn.started`. The adapter treated that as `runtime_protocol_mismatch`. It is now ignored as a non-output advisory; unknown item/event types, `turn.failed` and `error` still fail closed. Three synthetic regressions added.
- `tools/run_safe_regression.py`: `--postgres-config` forwarding, repository-path refusal, and a legacy-console `UnicodeEncodeError` fix.
- `tools/ci_postgres_fixture.ps1`: owned, loopback-only, disposable PostgreSQL cluster; refuses data-directory reuse; stops only what it started.
- `.github/workflows/safe-regression.yml`: fetches the pinned 18.6 binaries, creates the fixture, runs the one regression command, always stops the cluster.

### Verification

- Local, one command with PostgreSQL: **633 passed / 3 skipped**, Node **196 passed**, runner exit 0.
- Separate PostgreSQL selection: **19 passed** on a fresh 18.6 cluster.
- CI (windows-latest, run `40140013613`): same **633 / 3**, job success in 2m35s.
- `tests/test_agent_runtime.py`: 102 passed, 1 skip after the adapter fix.

### Actual provider trial (S1, Codex only)

- Memory-backed loopback fixture, synthetic identities, manual-start room, room-scoped 1h credential.
- First `connect` failed with `runtime_protocol_mismatch` (1 model start). One diagnostic native `codex exec` with the adapter's exact safety flags captured the event stream (1 start).
- After the fix: connection test passed, room started by explicit human control, first contribution `seq 8` published as `worker_reported_runtime` (1 start).
- Reconnect with `--max-model-calls 1`: `connection_check_calls: 0` (exact receipt reused) and a second contribution `seq 12` (1 start). No duplicate attempts or messages.
- Cleanup: room closed, four credentials revoked, fixture server stopped.
- Model starts this trial: **5**. Lifetime Codex starts: **6 of 6**, so further Codex starts require new approval. Claude budget remains 3.

### Actual provider trial (S1, Claude only, 2026-10-25)

- Same owned loopback fixture pattern (MemoryStore + real `create_app`), synthetic identities.
- Claude Code 2.1.310, native auth. Connection test `passed`; one real contribution at seq 8 with `worker_reported_runtime` and runtime_check `3464a252`.
- Task stages recorded by the new event stream: `claimed → preflight → runtime_started → runtime_completed → completed`.
- The real cold start took 36.6s; the connection-check bound was 30s, so it was raised to 90s.
- Claude model starts this trial: **3 of 3** (one diagnostic check, one check after a fixture restart, one contribution).
- Grok: CLI native refused with `runtime_auth_mode_unsupported`; xAI API refused with `runtime_auth_required` (no `XAI_API_KEY`). Zero model starts.
- Evidence: [validation/actual-trial-2026-10-25-claude.json](validation/actual-trial-2026-10-25-claude.json).

### Boundaries

No operating deployment, operating database, account creation, MFA, paging/archival, binary upload or POSIX runtime support. Provider token usage and billing remain unknown. Worker-reported evidence is not vendor attestation.

### Next

S2 (Codex + Claude in one room, pause/recovery) needs new approval and a fresh budget; the Claude S1 path is the cheapest next step.