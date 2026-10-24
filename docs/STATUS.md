# TalkAbout 상태 정본 / Current status

이 문서가 **현재 상태의 단일 정본**입니다. 확인일: **2026-10-25**. 기준 커밋: 이 문서를 추가한 커밋.
다른 상태 문서는 각자 확인일의 스냅샷이며, 서로 다른 시점의 수치를 합산하거나 현재값으로 읽지 않습니다.

## 1. 한 줄 상태

**로컬 검증 통과 · 실제 공급자 미검증 · 운영 배포 안 됨.**

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| API·도메인·worker·GUI 코드 | 구현됨 | `talkabout/`, `tests/` |
| 합성(synthetic) 회귀 | **통과** (2026-10-25: Python 633 · Node 196) | 아래 §2 |
| 실제 PostgreSQL 통합 19건 | **통과** (2026-10-25, 임시 18.6 클러스터) | 아래 §2 |
| CI(GitHub Actions) 동일 명령 | **통과** — 633 passed / 3 skipped | run `40140013613` |
| 실제 공급자 모델 호출 | **미검증** — Codex 1회 시도 결과 unknown, Claude 0회 | [actual-native-trial-2026-10-17.json](validation/actual-native-trial-2026-10-17.json) |
| 실제 2-runtime 토론·재연결 (S1/S2) | **미통과** | [RELEASE_GAP_MATRIX.md](RELEASE_GAP_MATRIX.md) |
| 운영 배포·운영 계정·MFA | **미완료** | [DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md) |

## 2. 재현 명령 (2026-10-25 실제 실행 결과)

```powershell
# 1) Python + Node 계약 (합성; PostgreSQL 케이스는 건너뜀)
python -B tools/run_safe_regression.py
#    -> Python 614 passed, 21 skipped, 7 deselected / Node 196 passed

# 2) PostgreSQL 통합 (임시 클러스터; 저장소 밖 경로 사용)
pwsh tools/ci_postgres_fixture.ps1 -Action create `
  -BinDir <postgres>/bin -DataDir <private>/pgdata `
  -ConfigOut <private>/pg-fixture.json -Port 55432
python -m pytest -q tests -k postgres --postgres-config <private>/pg-fixture.json -p no:cacheprovider
#    -> 19 passed

# 3) 한 번에 (Python+Node+PostgreSQL) — 2026-10-25 실제 실행
python -B tools/run_safe_regression.py --postgres-config <private>/pg-fixture.json
#    -> Python 633 passed, 3 skipped / Node 196 passed / runner exit 0
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
5. CI 워크플로가 같은 픽스처로 PostgreSQL 케이스를 실행합니다. 첫 실행(run `40140013613`)에서 로컬과 같은 **633 passed / 3 skipped**로 통과했습니다.

## 4. 아직 남은 것 (완료로 표시하지 않음)

| 남은 관문 | 왜 남았는가 | 다음 행동 |
| --- | --- | --- |
| 실제 provider 첫 응답·재연결(S1) | 네이티브 CLI 로그인·안전 플래그는 확인됐지만 모델 응답은 unknown | 남은 예산(Codex 5 / Claude 3)으로 명시 승인 후 1회 시도 |
| 실제 2-runtime 대화(S2) | S1 통과가 전제 | 별도 승인 |
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

**Local verification passes; no actual provider response and no deployment.**

`python -B tools/run_safe_regression.py --postgres-config <private>/pg-fixture.json` runs Python, Node and a real
ephemeral PostgreSQL cluster in one invocation (`tools/ci_postgres_fixture.ps1` creates and stops it, loopback only,
never reusing a data directory). On 2026-10-25 the single combined run reported **633 Python passes (3 skips) and
196 Node passes**, and a separate selection reported **19 PostgreSQL passes** against a fresh 18.6 cluster. The CI
workflow now runs the same fixture and reported the same 633/3 result on its first run (`40140013613`).

Actual provider evidence is unchanged: one Codex start with an unknown outcome and zero Claude starts. S1/S2 release
gates, operating deployment, accounts/MFA, paging/archival, binary uploads and POSIX execution remain incomplete, and
this document does not claim them. Every other status document is a dated snapshot, not the current value.
