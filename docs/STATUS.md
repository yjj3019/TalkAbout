# TalkAbout 상태 정본 / Current status

이 문서가 **현재 상태의 단일 정본**입니다. 확인일: **2026-10-25**. 기준 커밋: 이 문서를 추가한 커밋.
다른 상태 문서는 각자 확인일의 스냅샷이며, 서로 다른 시점의 수치를 합산하거나 현재값으로 읽지 않습니다.

## 1. 한 줄 상태

**로컬 검증 통과 · 실제 Codex 발언·재연결 확인 · S2/배포 미검증.**

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| API·도메인·worker·GUI 코드 | 구현됨 | `talkabout/`, `tests/` |
| 합성(synthetic) 회귀 | **통과** (2026-10-25: Python 636 · Node 196) | 아래 §2 |
| 실제 PostgreSQL 통합 19건 | **통과** (2026-10-25, 임시 18.6 클러스터) | 아래 §2 |
| CI(GitHub Actions) 동일 명령 | **통과** — run `40142129438` 636 passed / 3 skipped | 아래 §2 |
| 실제 공급자 모델 호출 (Codex 0.164.0) | **부분 통과** — 검증 통과, 발언 2건, 재연결 시 시험 호출 0회 | [actual-trial-2026-10-25.json](validation/actual-trial-2026-10-25.json) |
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

## 3. 이번 확인에서 달라진 점 (2026-10-25)

1. `connect` 기본 상한(`--max-model-calls 1`)이 연결 시험에 전부 소모될 때, 더 이상 `model_call_budget_exhausted`만 남기고 끝나지 않습니다.
   `next_action: increase_max_model_calls`와 KO/EN 안내(필요 상한, 재시험 시 추가 과금 가능성)를 출력하고 worker를 시작하지 않습니다.
2. 회귀 러너가 콘솔 코드페이지 때문에 `UnicodeEncodeError`로 죽던 문제를 수정했습니다(테스트 실패가 exit 1로 보이던 원인).
3. `--postgres-config`를 러너에 전달할 수 있게 되어, **Python+Node+PostgreSQL을 한 번에** 검증할 수 있습니다.
4. 임시 PostgreSQL 픽스처 스크립트를 추가하고 실제로 생성→검증→종료까지 실행했습니다.
5. CI 워크플로가 같은 픽스처로 PostgreSQL 케이스를 실행합니다. 어댑터 수정 후 CI(run `40142129438`)에서 **636 passed / 3 skipped**로 통과했습니다(수정 전 실행 `40140013613`은 633).
6. **실제 Codex 0.164.0 S1 시험을 수행했습니다.** 연결 시험 통과, 실제 발언 2건(`worker_reported_runtime`), 재연결에서 시험 호출 0회로 두 번째 발언. 이 과정에서 0.164.0이 `turn.started` 앞에 보내는 경고 `item.completed/error`를 어댑터가 프로토콜 오류로 처리하던 결함을 찾아 수정하고 회귀 3건을 추가했습니다. Codex 호출 예산은 소진되었습니다(누적 6/6).
7. **에이전트 공급자를 확장했습니다.** 하나의 OpenAI 호환 HTTP 어댑터(`talkabout/http_runtime.py`)로 DeepSeek·Grok(xAI API)·OpenRouter·Groq·Together·Mistral·OpenAI API·Ollama·LM Studio·vLLM/게이트웨이를 붙일 수 있고, `talkabout providers`가 전체 카탈로그를 출력합니다. CLI 방식(Codex·Claude Code·Grok CLI)은 그대로입니다. 계약은 로컬 합성 서버로 테스트했으며 **실제 공급자 호출은 NOTRUN**입니다 — [PROVIDERS.md](PROVIDERS.md).

8. **UI를 자체 디자인 시스템으로 다시 만들었습니다.** 콘텐츠·ID·계약은 그대로 두고 시각 계층만 교체했습니다: 방은 2개 창(왼쪽: 참여 에이전트가 걸어 다니는 아이소메트릭 공간, 오른쪽: 대화·참여자·산출물·관리·작업 상태 콘솔)으로 열립니다. 방 목록은 같은 방의 평면도입니다. 미드나이트(기본)와 포슬린(라이트) 두 테마, 구조화된 메시지(아바타·종류·시각·근거 칩), 버전별 산출물, 세그먼트 탭, 인라인 SVG 아이콘(외부 CDN·웹폰트 없음). 스크린샷은 [docs/ui/](ui/)에 있고 README에 연결했습니다.

9. **Claude 실제 S1을 통과했습니다(2026-10-25).** 소유 루프백 픽스처에서 Claude Code 2.1.310 native 연결 시험 `passed`, 실제 발언 1건이 `worker_reported_runtime`(seq 8, runtime_check `3464a252…`)으로 기록되었고, task 실행 단계도 `claimed → preflight → runtime_started → runtime_completed → completed`로 남았습니다. 실제 콜드 스타트가 36.6초여서 연결 시험 상한을 30초→90초로 올렸습니다. Claude 모델 시작 3회를 모두 사용했습니다. **Grok은 두 경로 모두 차단**입니다: CLI native `runtime_auth_mode_unsupported`, xAI API `runtime_auth_required`(키 없음), 모델 호출 0회. 근거: [actual-trial-2026-10-25-claude.json](validation/actual-trial-2026-10-25-claude.json).

10. **Grok 네이티브 경로를 구현하고 연결 시험까지 통과했습니다.** 격리 프로필(`--grok-profile`)에서 `grok login` 후 `cached_token`으로 ACP 인증하고, `_x.ai/*` 메타데이터를 무시하며, 시작 cwd가 워크스페이스와 다르면 fail-closed합니다. 실제 연결 시험은 `passed`(receipt `d0e7606e…`)였지만, 기여 1건은 task 단계가 `failed`로 남아 아직 통과하지 못했습니다. [PROVIDERS.md](PROVIDERS.md)

## 4. 아직 남은 것 (완료로 표시하지 않음)

| 남은 관문 | 왜 남았는가 | 다음 행동 |
| --- | --- | --- |
| 실제 2-runtime 대화(S2) | Codex 단일 runtime에서만 S1을 통과. Claude·pause 복구는 미시험 | 새 예산 승인 후 S2 계획 |
| 실제 2번째 공급자 S1 | Claude는 2026-10-25 통과(예산 3/3 소진). Grok은 게이트/키 부재로 차단 | Grok: XAI_API_KEY 제공 또는 CLI 격리 검증 |
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
