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
| Python (2차) | **692 passed / 7 failed / 23 skipped** | 실패 7건 중 6건은 `EXCLUDED` 목록의 브라우저·MCP 프로세스 항목(CI에서 제외되는 것과 동일), 1건은 샌드박스 named-pipe 제한(`multiprocessing/connection.py:614` WinError 5). 스킵 23건은 PostgreSQL 픽스처 미제공(22) + POSIX 전용 1 |
| Python (3차·4차, 서버 측 결함 및 문서/한도 어휘 반영 후) | **719 passed / 4 failed / 23 skipped** | 실패 4건: `EXCLUDED` 브라우저 항목 3건 + 샌드박스 named-pipe 1건. 같은 환경에서 실행마다 브라우저·MCP 픽스처 항목의 성패가 달라지므로(2차 7건 → 3차 4건) **실패 수 자체를 회귀 지표로 쓰지 않는다** |
| Node | 9개 파일 **206 passed** | 신규 `ui-reduced-motion.test.mjs` 9건 포함 |
| 신규 테스트 | **87건** | escort 7 · storage_health 34 · 승인 8 · stop 플래그 8 · ACP 격리 3 · 첨부 저장소 3 · 스케줄러/ack 8 · 문서 계약 7 · 한도 어휘 9 |
| **CI (표준 러너)** | **통과** — PR #30 head `d5a381b` [`40206446277`](https://github.com/yjj3019/TalkAbout/actions/runs/40206446277) · merge `7e7a0aa` [`40206504760`](https://github.com/yjj3019/TalkAbout/actions/runs/40206504760) · PR #31 head `33bd91d` [`40208480239`](https://github.com/yjj3019/TalkAbout/actions/runs/40208480239) · merge `66075d1` [`40208764352`](https://github.com/yjj3019/TalkAbout/actions/runs/40208764352) | GitHub Actions가 **표준 러너**(`tools/run_safe_regression.py`)로 guarded Python + **실제 임시 PostgreSQL** + synthetic UI 계약을 실행. 위 로컬 측정은 이 실행을 대체하지 않으며, 이 브랜치들의 표준 검증은 CI 통과다 |

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

## 4. 아직 남은 것 (완료로 표시하지 않음)

| 남은 관문 | 왜 남았는가 | 다음 행동 |
| --- | --- | --- |
| 실제 2-runtime 대화(S2) | Codex·Claude·Grok 각각 별도 S1을 통과했지만, 같은 방의 동시 토론·pause 복구는 미시험 | 새 예산 승인 후 S2 계획 |
| Grok 기본 lease·반복 턴 | 격리 OAuth S1은 120초 lease 픽스처에서 통과; 기본 20초 lease와 반복 턴은 미검증. ACP 비매칭 응답은 이제 격리·기록되므로 다음 시험에서 원인 프레임의 구조를 확보할 수 있다(원인 자체는 미확인) | 별도 예산·시간 한도와 격리 진단(`last_diagnostics.stray_response_shapes`)을 함께 기록하며 시험 |
| 운영 배포 | 대상·TLS·백업·보존 정책 미결정 | [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) |
| 운영 계정·MFA·분산 rate limit | private loopback 프로토타입 범위 | 정책 결정 후 |
| paging/archival/disk quota | P0 상한(방 64, 이벤트 2048 등)만 존재 | 운영 정책 결정 |
| binary/이미지 upload | 기본 비활성, `.txt/.md`만 opt-in | P2 격리·검사 |
| POSIX 실행 | Windows Job containment 검증 전 차단 | 격리 검증 후 |

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
