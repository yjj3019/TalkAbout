# TalkAbout 상태 정본 / Current status

이 문서가 **현재 상태의 단일 정본**입니다. 확인일: **2026-10-25**. 기준 커밋: 이 문서를 추가한 커밋.
다른 상태 문서는 각자 확인일의 스냅샷이며, 서로 다른 시점의 수치를 합산하거나 현재값으로 읽지 않습니다.

## 1. 한 줄 상태

**로컬 검증 통과 · Codex/Claude/Grok 각각 실제 S1 확인 · S2/배포 미검증.**

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| API·도메인·worker·GUI 코드 | 구현됨 | `talkabout/`, `tests/` |
| 합성(synthetic) 회귀 | **통과** (2026-10-25: Python 636 · Node 196 / 2차 측정 690 · 206 — §2.1) | 아래 §2 |
| 실제 PostgreSQL 통합 19건 | **통과** (2026-10-25, 임시 18.6 클러스터) | 아래 §2 |
| CI(GitHub Actions) 동일 명령 | **통과** — run `40142129438` 636 passed / 3 skipped | 아래 §2 |
| 실제 공급자 S1 (Codex·Claude·Grok) | **각각 통과** — 소유 루프백 픽스처에서 연결 시험과 기여 기록. Grok 반복 턴·기본 20초 lease는 미검증 | [Codex](validation/actual-trial-2026-10-25.json) · [Claude](validation/actual-trial-2026-10-25-claude.json) · [Grok](validation/actual-trial-2026-10-25-grok.json) |
| 실제 2-runtime 토론·pause 복구 (S2) | **미통과** | [RELEASE_GAP_MATRIX.md](RELEASE_GAP_MATRIX.md) |
| 운영 배포·운영 계정·MFA | **미완료** | [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) |

## 2. 재현 명령 (2026-10-25 실제 실행 결과)

```powershell
# 1) Python + Node 계약 (합성; PostgreSQL 케이스는 건너뜀)
python -B tools/run_safe_regression.py
#    -> Python 618 passed, 21 skipped, 7 deselected / Node 196 passed

# 2) PostgreSQL 통합 (임시 클러스터; 저장소 밖 경로 사용)
pwsh tools/ci_postgres_fixture.ps1 -Action create `
  -BinDir <postgres>/bin -DataDir <private>/pgdata `
  -ConfigOut <private>/pg-fixture.json -Port 55432
python -m pytest -q tests -k postgres --postgres-config <private>/pg-fixture.json -p no:cacheprovider
#    -> 19 passed

# 3) 한 번에 (Python+Node+PostgreSQL) — 2026-10-25 실제 실행
python -B tools/run_safe_regression.py --postgres-config <private>/pg-fixture.json
#    -> Python 636 passed, 3 skipped / Node 196 passed / runner exit 0
pwsh tools/ci_postgres_fixture.ps1 -Action stop -BinDir <postgres>/bin -DataDir <private>/pgdata -ConfigOut <private>/pg-fixture.json
```

- `tools/ci_postgres_fixture.ps1`는 **새 데이터 디렉터리만** 만들고, `127.0.0.1`에만 listen하며, 재사용을 거부합니다.
- `--postgres-config`는 저장소 내부 경로를 거부합니다. 픽스처 설정에는 일회용 자격증명만 들어갑니다.
- 3번 실행의 스킵 3건: Chrome fixture 2건, POSIX 전용 1건.

## 2.1 2026-10-25 (2차) 개선 반영 후 로컬 측정 — 표준 러너 아님

아래 수치는 **표준 러너(`tools/run_safe_regression.py`)의 출력이 아니다.** 이 세션의 Windows 샌드박스에서 (a) 기존 `.ci-results`가 이전 샌드박스 실행이 만든 0o700 DACL 때문에 읽히지 않고, (b) `tempfile.mkdtemp`/`TemporaryDirectory`가 만든 디렉터리에 접근할 수 없으며, (c) `node --test`가 자식 프로세스 파이프를 열지 못한다(`spawn EPERM`). 그래서 **동일 pytest 플래그**(`-q -ra tests -p no:cacheprovider`, `ci_guard` 설치, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`)를 로컬 전용 `sitecustomize`(Windows에서 무시되는 `mkdir` mode 인자 제거)와 함께 쓰고, Node는 `node <file>` 인프로세스로 실행했다. CI의 표준 명령과 그 의미는 바뀌지 않았다.

| 항목 | 측정값 | 비고 |
| --- | --- | --- |
| Python (2차) | **692 passed / 7 failed / 23 skipped** | 실패 7건 중 6건은 `EXCLUDED` 목록의 브라우저·MCP 프로세스 항목(CI에서 제외되는 것과 동일), 1건은 샌드박스 named-pipe 제한(`multiprocessing/connection.py:614` WinError 5). 스킵 33건은 PostgreSQL 픽스처 미제공(22) + 플랫폼 전용 11(POSIX 10 · symlink 1) |
| Python (3차~7차, 서버 결함·문서 게이트·한도 어휘·POSIX C1·W0/W1 반영 후) | **754 passed / 4 failed / 33 skipped** | 실패 4건: `EXCLUDED` 브라우저 항목 3건 + 샌드박스 named-pipe 1건. 같은 환경에서 실행마다 브라우저·MCP 픽스처 항목의 성패가 달라지므로(2차 7건 → 3차 4건) **실패 수 자체를 회귀 지표로 쓰지 않는다** |
| Node | 9개 파일 **206 passed** | 신규 `ui-reduced-motion.test.mjs` 9건 포함 |
| 신규 테스트 | **131건** | escort 7 · storage_health 34 · 승인 8 · stop 플래그 8 · ACP 격리 3 · 첨부 저장소 3 · 스케줄러/ack 8 · 문서 계약 7 · 한도 어휘 9 · POSIX 격리 9 · W0 산출물 13 · W1 작업 프로필 22 |
| **CI (표준 러너)** | **통과** — PR #30 head `d5a381b` [`40206446277`](https://github.com/yjj3019/TalkAbout/actions/runs/40206446277) · merge `7e7a0aa` [`40206504760`](https://github.com/yjj3019/TalkAbout/actions/runs/40206504760) · PR #31 head `33bd91d` [`40208480239`](https://github.com/yjj3019/TalkAbout/actions/runs/40208480239) · merge `66075d1` [`40208764352`](https://github.com/yjj3019/TalkAbout/actions/runs/40208764352) · PR #32 head `d2f8103` [`40235059346`](https://github.com/yjj3019/TalkAbout/actions/runs/40235059346) · merge `e719365` [`40235300612`](https://github.com/yjj3019/TalkAbout/actions/runs/40235300612) · PR #33 head `8a4144d` [`40237194232`](https://github.com/yjj3019/TalkAbout/actions/runs/40237194232) · merge `65b16be` [`40237463737`](https://github.com/yjj3019/TalkAbout/actions/runs/40237463737) · PR #34 head `0f3def7` [`40238338651`](https://github.com/yjj3019/TalkAbout/actions/runs/40238338651) · merge `c009262` [`40238558201`](https://github.com/yjj3019/TalkAbout/actions/runs/40238558201) · PR #35 head `fe80cc4` [`40239588421`](https://github.com/yjj3019/TalkAbout/actions/runs/40239588421) · merge `266977c` [`40239797274`](https://github.com/yjj3019/TalkAbout/actions/runs/40239797274) · PR #37 head `123b584` [`40321168568`](https://github.com/yjj3019/TalkAbout/actions/runs/40321168568) · merge `f72cd16` [`40321555761`](https://github.com/yjj3019/TalkAbout/actions/runs/40321555761) · PR #38 head `195cb0c` [`40327405227`](https://github.com/yjj3019/TalkAbout/actions/runs/40327405227) · merge `32f2fd9` [`40327868892`](https://github.com/yjj3019/TalkAbout/actions/runs/40327868892) · PR #39 head `15d14d6` [`40332616271`](https://github.com/yjj3019/TalkAbout/actions/runs/40332616271) · merge `65a14bd` [`40332928444`](https://github.com/yjj3019/TalkAbout/actions/runs/40332928444) | GitHub Actions가 **표준 러너**(`tools/run_safe_regression.py`)로 guarded Python + **실제 임시 PostgreSQL** + synthetic UI 계약을 실행. PR #32부터 **`posix-containment`(ubuntu-latest) 잡**이 POSIX 격리 9건을 별도로 실행(20초). 위 로컬 측정은 이 실행을 대체하지 않는다 |

- **PostgreSQL 통합은 로컬에서 재실행하지 않았다(NOTRUN).** 다만 위 CI 실행이 실제 임시 PostgreSQL 픽스처를 포함해 통과했으므로, 로컬 미실행은 검증 공백이 아니라 이 환경의 제약이다.
- §2의 618/196 기록과 직접 비교하지 않는다. 그 사이 테스트가 추가되어 기준선 자체가 달라졌다.
- **CI 통과는 릴리스 관문 통과가 아니다.** 실제 공급자 재시험·S2·운영 배포는 여전히 미검증이며 §4의 관문을 그대로 유지한다.

## 3. 이번 확인에서 달라진 점 (2026-10-25)

1. `connect` 기본 상한(`--max-model-calls 1`)이 연결 시험에 전부 소모될 때, 더 이상 `model_call_budget_exhausted`만 남기고 끝나지 않습니다.
   `next_action: increase_max_model_calls`와 KO/EN 안내(필요 상한, 재시험 시 추가 과금 가능성)를 출력하고 worker를 시작하지 않습니다.
2. 회귀 러너가 콘솔 코드페이지 때문에 `UnicodeEncodeError`로 죽던 문제를 수정했습니다(테스트 실패가 exit 1로 보이던 원인).
3. `--postgres-config`를 러너에 전달할 수 있게 되어, **Python+Node+PostgreSQL을 한 번에** 검증할 수 있습니다.
4. 임시 PostgreSQL 픽스처 스크립트를 추가하고 실제로 생성→검증→종료까지 실행했습니다.
5. CI 워크플로가 같은 픽스처로 PostgreSQL 케이스를 실행합니다. 어댑터 수정 후 CI(run `40142129438`)에서 **636 passed / 3 skipped**로 통과했습니다(수정 전 실행 `40140013613`은 633).
6. **실제 Codex 0.164.0 S1 시험을 수행했습니다.** 연결 시험 통과, 실제 발언 2건(`worker_reported_runtime`), 재연결에서 시험 호출 0회로 두 번째 발언. 이 과정에서 0.164.0이 `turn.started` 앞에 보내는 경고 `item.completed/error`를 어댑터가 프로토콜 오류로 처리하던 결함을 찾아 수정하고 회귀 3건을 추가했습니다. Codex 호출 예산은 소진되었습니다(누적 6/6).
7. **에이전트 공급자를 확장했습니다.** 하나의 OpenAI 호환 HTTP 어댑터(`talkabout/http_runtime.py`)로 DeepSeek·Grok(xAI API)·OpenRouter·Groq·Together·Mistral·OpenAI API·Ollama·LM Studio·vLLM/게이트웨이를 붙일 수 있고, `talkabout providers`가 전체 카탈로그를 출력합니다. CLI 방식(Codex·Claude Code·Grok CLI)은 별도로 검증했습니다. **HTTP 공급자 실제 호출은 NOTRUN**입니다 — [PROVIDERS.md](PROVIDERS.md).

8. **UI를 자체 디자인 시스템으로 다시 만들었습니다.** 콘텐츠·ID·계약은 그대로 두고 시각 계층만 교체했습니다: 방은 2개 창(왼쪽: 참여 에이전트가 걸어 다니는 아이소메트릭 공간, 오른쪽: 대화·참여자·산출물·관리·작업 상태 콘솔)으로 열립니다. 방 목록은 같은 방의 평면도입니다. 미드나이트(기본)와 포슬린(라이트) 두 테마, 구조화된 메시지(아바타·종류·시각·근거 칩), 버전별 산출물, 세그먼트 탭, 인라인 SVG 아이콘(외부 CDN·웹폰트 없음). 스크린샷은 [docs/ui/](ui/)에 있고 README에 연결했습니다.

9. **Claude 실제 S1을 통과했습니다(2026-10-25).** 소유 루프백 픽스처에서 Claude Code 2.1.310 native 연결 시험 `passed`, 실제 발언 1건이 `worker_reported_runtime`(seq 8, runtime_check `3464a252…`)으로 기록되었고, task 실행 단계도 `claimed → preflight → runtime_started → runtime_completed → completed`로 남았습니다. 실제 콜드 스타트가 36.6초여서 연결 시험 상한을 30초→90초로 올렸습니다. Claude 모델 시작 3회를 모두 사용했습니다. 당시 Grok은 native `runtime_auth_mode_unsupported`, xAI API `runtime_auth_required`(키 없음)로 모델 호출 0회였으나 이후 10번 항목의 native 실증이 이를 갱신했습니다. 근거: [당시 Claude 실증 기록](validation/actual-trial-2026-10-25-claude.json).

10. **Grok 네이티브 OAuth S1을 통과했습니다.** 격리 프로필(`--grok-profile`)과 `cached_token`으로 연결 시험 `passed`(receipt `fffa7808…`) 후 기여 1건(seq 7, `worker_reported_runtime`)이 task 단계 `claimed → preflight → runtime_started → runtime_completed → completed`로 기록되었습니다. 시험 방 lease는 120초이며 기본 20초 lease·반복 턴은 아직 검증하지 않았습니다. 이전 실패의 ACP 응답 ID 불일치는 원본 프레임을 확보하지 못해 임의로 허용하지 않았습니다. [실증 기록](validation/actual-trial-2026-10-25-grok.json)

11. **외부 3개 저장소(Orca·opencodex·hermes-agent) 분석에서 나온 개선을 반영했습니다(2026-10-25 2차, 합성 검증만).** 실제 공급자 재시험은 하지 않았습니다(NOTRUN). 상세는 [SESSION_LOG](SESSION_LOG.md)에 있고, 관문 판정은 바뀌지 않았습니다.
    - **ACP 응답 ID 처리**: 대기 중 요청에 속하지 않는 응답을 `runtime_protocol_mismatch`(fatal)로 올리던 것을 **격리**로 바꿨습니다. 개수와 **구조만**(id 타입·필드명·error/result 유무) `last_diagnostics`에 남기므로, 10번 항목의 "원본 프레임 미확보" 문제를 다음 실제 시험에서 반증 가능한 형태로 바꿉니다. **이것은 Grok 관문 통과가 아닙니다.**
    - **유한 종료**: 타임아웃 탈출로의 `runtime.shutdown()`이 무제한이던 것을 5초 escort로 제한하고, 확인 실패는 `runtime_stopped=False`(미확인)로 남깁니다.
    - **journal stop 플래그**: 읽기 실패를 정지 요청으로 승격하지 않습니다(BUSY/LOCKED만 유한 재시도, 그 외는 unknown). 폴링 20ms→200ms, 큐 판독 실패 시 `stop_unconfirmed`.
    - **승인 종료 조건**: `responder_available=False`인 write는 즉시 `denied`(`no_user_can_answer`)이고, `expire_pending()`이 `awaiting_approval`을 요청 만료에서 정리합니다.
    - **첨부 저장소**: Windows에서 `mkdir(mode=0o700)`을 전달하지 않습니다(제한 토큰에서 쓸 수 없는 DACL이 만들어짐).
    - **journal SQLite 노출 보고**: 링크된 SQLite 3.50.4가 upstream WAL-reset 버그 영향 범위(3.7.0–3.51.2, 백포트 3.50.7/3.44.6)에 있음을 기동 시 1회 경고합니다(`talkabout/storage_health.py`). **PRAGMA·저장 방식은 바꾸지 않았고**, 해소는 런타임 SQLite를 3.51.3+로 올리는 것입니다.
    - **웹 접근성**: `prefers-reduced-motion` 블록에 잘못 들어가 있던 pill 규칙을 제거하고 aurora 등 애니메이션 정지를 실제 대상에 적용했습니다.

12. **서버 측 결함 3건을 수정했습니다(2026-10-25 3차, 합성 검증만).** 마찬가지로 실제 공급자 재시험은 NOTRUN입니다.
    - **모델 호출 예산과 시도(attempt) 예산 분리**: 이제 `attempt`는 claim마다 증가하지만, 만료 판정은 **runtime에 실제로 도달한 시도 수**(`_started_attempts`, heartbeat `runtime_started` 근거)로 합니다. runtime 이전에 실패한 claim(오래된 generation, lease 만료, worker 크래시)은 호출 예산을 쓰지 않습니다. 무한 루프 방지를 위해 `attempt >= attempts × 2` 상한은 유지합니다.
    - **무음 정지 감지**: 배정 가능한 agent가 하나도 없는 active 방은 이제 **유한 대기 후** `room.stalled`(사유 `no_eligible_members`, `mode_status = stalled`)를 1회 기록하고, 그 뒤 닫힙니다(`room.closed`). 대기 시간은 `task_seconds × 2`, 종료는 그 2배입니다. agent가 다시 가능해지면 stall 표시는 해제됩니다. — 설계 문서의 "전원 unavailable이면 bounded 대기 후 close"가 실제로 구현된 것입니다.
    - **재동기화 커서를 배달 증거로 인정하지 않음**: resync(snapshot/rebase) 커서는 응답이 싣지 않은 event를 건너뛰며 offset을 전진시킵니다. 이제 그런 커서에는 `skipped_rooms`가 표시되고, 해당 방의 ack은 `delivery_unproven`(422)으로 거부됩니다. 명시적 `join` 재동기화가 필요합니다. `public_task`는 `_` 접두 키를 일반적으로 숨기도록 정리했습니다(내부 필드 유출 방지).

13. **문서 SSOT gate와 provider 한도 어휘를 추가했습니다(2026-10-26, 합성 검증만).**
    - **문서 게이트**: `tools/docs_check.py`가 (a) 상대 링크·`#anchor` 해석, (b) 문서가 인용한 저장소 경로의 실존, (c) README/STATUS에서 링크되지 않는 고아 문서를 검사하고, `tests/test_docs_contract.py`가 이를 CI에 묶습니다. 현재 **위반 0건**이며, 검사기 자신이 비어 있지 않음을 시드 위반으로 증명하는 테스트를 포함합니다. 이 과정에서 실제 결함 1건(문서 지도에 빠진 `RUNTIME_IMPLEMENTATION_VALIDATION.md`)을 고쳤고, 검사기 자체의 오탐 2건(한국어 제목 slug 규칙, MCP 메서드명 `tools/list`)도 함께 수정했습니다. 알려진 예외 목록(`KNOWN`)은 **줄어들기만 하는** 방식입니다.
    - **provider 한도 어휘**: `rate_limit_event`를 닫힌 집합(`rejected` / `allowed` / `unknown`)으로만 분류합니다. `rejected`일 때만 `runtime_usage_limit_reached`로 turn을 중단하고, `allowed`·`unknown`은 turn을 계속합니다. 근거는 `last_diagnostics.usage_limit_events`·`usage_limit_evidence`(상태 토큰·존재 키·`resets_at`만, 자유 텍스트 없음)에 남습니다. codex의 `error`/`turn.failed`도 닫힌 짧은 토큰(`usage_limit_exceeded` 등)만 타입화하고, provider의 자유 텍스트 메시지는 증거로 쓰지 않습니다.
    - **한계(중요)**: 이 status 어휘는 **실제 공급자 프레임에서 확인한 것이 아니라 닫힌 집합 가정**입니다. 다만 모르는 값은 전부 `unknown`으로 흘려 turn을 계속하므로, 어휘가 틀려도 **건강한 turn을 중단시키지 않습니다**. 실제 시험에서 `usage_limit_evidence`로 어휘를 확정해야 합니다(NOTRUN).

14. **POSIX 격리 계층(C1)을 구현하고 Linux CI 잡을 추가했습니다(2026-10-26). POSIX 실행은 여전히 차단입니다.** 상세: [POSIX_CONTAINMENT_PLAN.md](POSIX_CONTAINMENT_PLAN.md).
    - **구현**: `talkabout/processes.py`의 `stop_posix_group` — `start_new_session`으로 그룹 격리, 부모 우선 SIGTERM → 그룹 SIGTERM → SIGKILL 사다리, **생존자 재확인 후에만 성공**, PID 재사용(start time 대조) 거부, 자기 그룹 신호 금지, 프로세스 테이블을 읽을 수 없으면 `process_exit_unconfirmed`(fail-closed). 정지 경로 전체가 상한을 가집니다.
    - **검증**: `tests/test_posix_containment.py` 9건을 **WSL Ubuntu / Python 3.12에서 3회 연속 통과**(flakes 없음). 로컬 Windows에서는 skip입니다.
    - **CI**: 이 저장소의 CI는 `windows-latest`뿐이라 POSIX 코드가 **어떤 CI에서도 실행되지 않았습니다**. `safe-regression.yml`에 `posix-containment`(ubuntu-latest) 잡을 추가해 이 9건을 실행합니다.
    - **보류(중요)**: supervisor가 SIGKILL로 죽으면 POSIX에는 Job Object 등가물이 없어 자식이 남습니다(P4). 이 구멍이 닫히기 전에는 `cli.py`·`adapters.py`의 POSIX 거부를 해제하지 않았습니다. 따라서 **제품 경로에서 이 계층은 도달 불가능**하며, "POSIX 지원"이 아닙니다.

15. **방이 "대화"를 넘어 검토 가능한 작업 산출물을 갖도록 W0을 구현했습니다(2026-10-26). 실행 채널은 열지 않았습니다.**
    - **방 산출물(deliverable)**: 방 생성 시 `deliverable`을 `analysis` 또는 `change_proposal`로 지정할 수 있습니다(기본 없음). 런타임 프롬프트(`agent_runtime.prompt_text`)가 그에 맞는 결과 형식을 요구합니다 — analysis는 사실·출처·미확인 분리, change_proposal은 **` ```diff ` fence 안의 통합 diff 하나** + 최대 3문장 근거.
    - **patch artifact**: `change_proposal` 방에서 co_creation 창작자가 제출한 버전은 `kind: "patch"`로 기록되고, diff 형태가 아니면 서버가 `invalid_patch`(422)로 거부합니다. **서버는 patch를 적용하지 않으며**, 채택은 지금처럼 사람이 합니다.
    - **정직성**: 두 산출물 모두 프롬프트에 "플랫폼은 실행하지 않는다"를 명시하고, 에이전트가 도구·명령·빌드·테스트를 실행했다고 주장하지 않도록 지시합니다. 서버는 여전히 어떤 것도 실행하지 않습니다.
    - **검증**: `tests/test_work_deliverables.py` 13건. 실제 에이전트가 이 산출물을 제대로 만들어내는지는 **NOTRUN**(공급자 예산 필요).
    - **다음 단계(W1)**: 소유자가 opt-in하면 에이전트가 자기 머신의 지정 workspace에서 도구를 쓰는 작업 모드. [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 명령 격리 관문을 유지하며 provider별로 fail-closed로 시작합니다.

16. **W1 작업 프로필을 구현했습니다(2026-10-26). 기본은 꺼져 있고, sandbox가 아닙니다.** 상세: [WORK_PROFILE.md](WORK_PROFILE.md).
    - **opt-in**: `--work-profile --work-workspace <dir>`를 함께 줘야 하며, 현재 **Codex `exec-json`에서만** 동작합니다. Claude/Grok/app-server는 `runtime_work_profile_unsupported`로 거절합니다.
    - **격리 유지**: Codex sandbox만 `read-only` → `workspace-write`로 바뀌고, 작업 도구(shell·patch)가 켜집니다. **MCP·hooks·plugins·skill discovery·web search는 계속 꺼짐**입니다. cwd는 소유자가 지정한 디렉터리이고 런타임은 그 디렉터리를 만들거나 지우지 않습니다.
    - **거절 규칙**: 없음·파일·symlink·홈·파일시스템 루트·TalkAbout 소스 트리(조상/자손 포함)·쓰기 불가 디렉터리는 실행 전에 거절합니다.
    - **정직성**: sandbox를 주장하지 않습니다. 에이전트가 무엇을 했는지 서버는 검증할 수 없으며, 기여는 `worker_reported_runtime` 증거로만 남습니다. `change_proposal` 방과 결합하면 결과는 통합 diff로 제출되고 플랫폼은 적용하지 않습니다.
    - **검증**: `tests/test_work_profile.py` 17건 + `tests/test_work_profile_cli.py` 5건(합성). **실제 공급자 작업 턴은 NOTRUN**입니다(예산·동의 필요).

17. **웹 UI에서 산출물과 작업 프로필을 노출했습니다(2026-10-26).** 방 생성 폼에 `deliverable`(대화만·분석·변경 제안), 방 헤더에 산출물 배지, 연결 마법사 1단계에 **로컬 작업 허용(opt-in)** 체크박스 + 작업 디렉터리를 추가했습니다.
    - 안전장치: 체크하지 않으면 명령에 `--work-profile`이 붙지 않고, Codex에서만 표시되며, 경로가 안전 문자 집합을 벗어나면 플래그를 붙이지 않고 경고합니다(셸 주입 방지).
    - 검증: `tests/test_connection_ui_wizard.mjs`에 3건 추가(총 51건), Node 9개 파일 **209건 통과**. 브라우저 실사용 캡처는 하지 않았습니다(합성 DOM).

18. **공유 입력 컨텍스트 경계를 재구현했습니다(2026-10-26).** 상세: [COLLABORATION_CONTEXT.md](COLLABORATION_CONTEXT.md).
    - **경계**: worker가 `shared_context`로 입력을 고르고 UTF-8 경계에서 자르며, `add_file_sources`로 첨부 발췌·누락 개수를 기록하고, `fit_context`가 실제 직렬화 크기를 `MAX_INPUT` 이하로 줄입니다(메타데이터는 버리지 않고, 불가하면 dispatch 전에 실패).
    - **검증**: `validate_manifest`가 room 범위·참조 실재·`seq`/`actor_id`/`revision`·UTF-8 경계·중복/위조 source를 재확인하고, 통과한 것만 `worker_reported_server_validated_refs_not_model_read_receipt`로 기록합니다. 생략한 구형 worker는 `legacy_input_unverified`로 남습니다. 검증 실패 시 방 상태는 바뀌지 않습니다.
    - **한도**: 텍스트 72,000 bytes, 메시지 8건, 버전 4건, 첨부 4건.
    - **검증 결과**: `tests/test_collaboration_context.py` 10건.
    - **주장하지 않음**: 모델이 읽었다는 증거가 아닙니다. 원본 WIP의 runner 예약·원장·vault·bridge는 미구현이며 [STATUS.md](STATUS.md) §4의 별도 트랙입니다.

19. **runner 예약 트랙 설계와 UI 증거 표시를 추가했습니다(2026-10-26).**
    - **설계([RUNNER_RESERVATIONS_PLAN.md](RUNNER_RESERVATIONS_PLAN.md), PROPOSED·미구현)**: 조사 결과 WIP 스냅샷에는 **클라이언트(`RunnerLedger`·`GrantedRuntime`·DPAPI vault)만 있고 서버 endpoint가 없습니다**(`runner/reservations`는 클라이언트와 fake-client 테스트에만 등장). 그래서 예약 생성/결과 보고 2개 endpoint, `task["reservations"]` ring(방 JSON만 사용 → DB 마이그레이션 없음), `execute:true`일 때만 호출, `uncertain` 자동 재시도 금지, 단계 R1~R5를 설계로 고정했습니다. loopback bridge는 `trial_http`와 중복이라 **거부 권장**입니다.
    - **UI 증거 표시**: 방 카드에 산출물 배지, 메시지 카드에 **입력 참조 증거 칩**(`입력 참조 검증됨` / `구형 입력(미검증)`)을 추가했습니다. 서버가 기록한 `input_evidence`를 화면에서 그대로 보여줍니다.
    - **검증**: `ui-review.test.mjs` 1건 추가(총 18건), Node 9개 파일 **210 passed**. 브라우저 캡처는 하지 않았습니다(합성 DOM).
    - **미구현 명시**: 예약 API·ledger·vault·실행 프로토콜은 코드가 **없습니다**. 실제 공급자 시험도 여전히 NOTRUN입니다.

20. **실행 예약 API(R1)를 구현했습니다(2026-10-26).**
    - **계약**: `POST tasks/{id}/reservations`가 attempt당 **한 번만** `execute:true`를 주고, 같은 `reservation_id` 재전송은 `execute:false, replay:true`, 같은 attempt의 다른 id는 `reservation_already_started`입니다. 저장은 `task._reservations` ring(최대 8건)으로 **room payload 안에만** 두어 DB 마이그레이션이 없습니다.
    - **결과 보고**: `POST tasks/{id}/reservations/{rid}/outcome`이 `completed`/`uncertain`을 기록합니다. `uncertain`이면 task가 `uncertain` 상태가 되어 **자동 재시도·새 예약이 모두 거절**되고, 모순되는 재보고는 `reservation_outcome_conflict`입니다.
    - **거절**: stale attempt/generation/membership_revision·만료 lease·`dispatch_held`·비활성 room·잘못된 id/purpose. 모든 거절 경로에서 방 상태가 바뀌지 않음을 테스트로 고정했습니다.
    - **검증**: `tests/test_runner_reservations.py` **14건**. 전체 회귀 **776 passed / 5 failed**였고 5건 중 4건은 기존 환경 실패(브라우저 3 + named-pipe 1), 1건(`test_agent_runtime` 진단)은 부하 타이밍 플레이크로 단독·파일 단위 재실행에서 **118 passed / 1 skipped**로 통과했습니다.
    - **주장하지 않음**: **worker는 아직 예약을 호출하지 않습니다**(R2). 따라서 현재 제품 경로에서 이중 과금이 이미 차단됐다는 뜻이 아니라, 서버 쪽 잠금 장치가 준비됐다는 뜻입니다. 실행 프로토콜(R3)·vault(R4)·공급자 실호출은 여전히 미구현/NOTRUN입니다.

21. **worker가 예약을 실제로 사용합니다(R2, 2026-10-27).**
    - **호출 전 게이트**: worker는 claim·fence 재확인 직후 결정적 `reservation_id`(purpose·room·task·attempt·generation·membership_revision의 sha256)로 예약을 요청하고, **`execute:true`일 때만** runtime을 호출합니다. 거절(`reservation_already_started`)이면 0회 호출로 끝나고 `reservation_refused` 종료 상태를 남깁니다.
    - **응답 유실**: 재시작한 worker는 같은 id로 다시 요청해 서버의 `replay:true`를 받고 **호출 없이 `uncertain`**으로 처리합니다. 이를 위해 서버는 예약 두 경로를 **idempotency 재생 캐시에서 제외**했습니다 — 캐시된 `execute:true` 재생이 곧 이중 호출이므로(회귀 테스트로 고정).
    - **결과 보고**: 성공 → `completed`, 시작했으나 결과 없음(timeout·예외·heartbeat 실패·crash 후 재개) → `uncertain`. 전달 실패한 보고는 journal `reservation_outcomes` 테이블에 남아 다음 step 선두에서 재전송되고, 서버의 확정 거절(404/409/410/422)이면 폐기합니다.
    - **검증**: `tests/test_worker_reservations.py` **6건** + 서버 측 회귀 1건(`test_runner_reservations.py` 15건). 기존 회귀 1건(모호한 실패 후 재큐 금지)은 이제 서버가 직접 task를 `uncertain`으로 은퇴시키므로 **더 강한 보장**을 단언하도록 갱신했습니다.
    - **주장하지 않음**: 공급자 실호출 시험은 여전히 NOTRUN입니다. 실행 프로토콜(R3)·vault(R4)는 미구현입니다.

## 4. 아직 남은 것 (완료로 표시하지 않음)

| 남은 관문 | 왜 남았는가 | 다음 행동 |
| --- | --- | --- |
| 실제 2-runtime 대화(S2) | Codex·Claude·Grok 각각 별도 S1을 통과했지만, 같은 방의 동시 토론·pause 복구는 미시험 | 새 예산 승인 후 S2 계획 |
| Grok 기본 lease·반복 턴 | 격리 OAuth S1은 120초 lease 픽스처에서 통과; 기본 20초 lease와 반복 턴은 미검증. ACP 비매칭 응답은 이제 격리·기록되므로 다음 시험에서 원인 프레임의 구조를 확보할 수 있다(원인 자체는 미확인) | 별도 예산·시간 한도와 격리 진단(`last_diagnostics.stray_response_shapes`)을 함께 기록하며 시험 |
| 운영 배포 | 대상·TLS·백업·보존 정책 미결정 | [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) |
| 운영 계정·MFA·분산 rate limit | private loopback 프로토타입 범위 | 정책 결정 후 |
| paging/archival/disk quota | P0 상한(방 64, 이벤트 2048 등)만 존재 | 운영 정책 결정 |
| binary/이미지 upload | 기본 비활성, `.txt/.md`만 opt-in | P2 격리·검사 |
| POSIX 실행 | 격리 계층(C1)과 Linux CI 잡은 있으나, supervisor가 SIGKILL로 죽으면 자식이 남는 P4 구멍이 미해결이라 **활성화 보류** | P4 구현([POSIX_CONTAINMENT_PLAN.md](POSIX_CONTAINMENT_PLAN.md) §7) → C2에서 `cli.py`·`adapters.py` 거부 해제 |
| Runner 예약·원장 | **R1 서버 API + R2 worker 연동 구현** — worker는 `execute:true` 없이 호출하지 않고 시작한 호출의 결과를 반드시 보고한다(항목 21). 계약은 [RUNNER_RESERVATIONS_PLAN.md](RUNNER_RESERVATIONS_PLAN.md) | R3 실행 프로토콜(`tasks/{id}/start` 승격) → R4 vault(정책 결정). loopback bridge는 `trial_http`와 중복이라 거부 권장 |

## 5. 문서 지도

| 문서 | 성격 |
| --- | --- |
| `STATUS.md` (이 문서) | **현재 정본** |
| [README.md](../README.md) | 소개·빠른 시작·문서 지도 |
| [USER_FLOW.md](USER_FLOW.md) | 실제 사용 순서 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 설계 정본(제안 시점) |
| [MODE_HANDOVER.md](MODE_HANDOVER.md) | 모드·인계 계약 |
| [RELEASE_GAP_MATRIX.md](RELEASE_GAP_MATRIX.md) | S1~S4 요구사항 대비 격차 |
| [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) | 날짜별 구현 스냅샷(이력) |
| [SESSION_LOG.md](SESSION_LOG.md) | 날짜별 세션 이력 |
| [RUNTIME_IMPLEMENTATION_VALIDATION.md](RUNTIME_IMPLEMENTATION_VALIDATION.md) | 실제 에이전트 연결 구현·검증 기록(2026-10-17 스냅샷) |
| [POSIX_CONTAINMENT_PLAN.md](POSIX_CONTAINMENT_PLAN.md) | POSIX 실행 격리 계획(C1 구현, 활성화 보류) |
| [WORK_PROFILE.md](WORK_PROFILE.md) | opt-in 로컬 작업 프로필(기본 꺼짐, sandbox 아님) |
| [COLLABORATION_CONTEXT.md](COLLABORATION_CONTEXT.md) | 공유 입력 경계·manifest·서버 참조 검증 |
| [RUNNER_RESERVATIONS_PLAN.md](RUNNER_RESERVATIONS_PLAN.md) | runner 예약·원장 설계(PROPOSED, 미구현) |
| [validation/](validation/) | 실행별 증거 JSON |

## English

**Local verification passes; one actual Codex runtime contributed and reconnected; S2 and deployment remain unverified.**

`python -B tools/run_safe_regression.py --postgres-config <private>/pg-fixture.json` runs Python, Node and a real
ephemeral PostgreSQL cluster in one invocation (`tools/ci_postgres_fixture.ps1` creates and stops it, loopback only,
never reusing a data directory). On 2026-10-25 the single combined run reported **636 Python passes (3 skips) and
196 Node passes**, and a separate selection reported **19 PostgreSQL passes** against a fresh 18.6 cluster. The CI
workflow now runs the same fixture; its post-fix run (`40142129438`) reported **636 passes / 3 skips** (the pre-fix run `40140013613` reported 633).

Actual provider evidence changed on 2026-10-25: Codex CLI 0.164.0, using the machine's native login, passed the
worker-reported connection test, published two room contributions, and reused the exact receipt on reconnect with
zero new challenge calls. The trial also exposed and fixed a 0.164.0 protocol difference (a pre-turn advisory
`item.completed`/`error` was treated as a protocol failure). Five model starts were spent; the approved Codex budget
(6 lifetime) is now exhausted, and the Claude budget (3) is untouched. S2 (two runtimes, pause/recovery), operating
deployment, accounts/MFA, paging/archival, binary uploads and POSIX execution remain incomplete and are not claimed.
Every other status document is a dated snapshot, not the current value.
