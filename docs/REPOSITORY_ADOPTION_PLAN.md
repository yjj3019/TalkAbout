# 외부 저장소 분석 결과 순차 반영 계획

**사용자 승인: 2026-10-27 · 단계별 구현/검토/CI · 공급자 실호출은 별도 예산 승인 필요**

근거: [7개 저장소 분석](../analysis/repo-review-2026-10-27/README.md).
외부 코드를 통째 흡수하거나 설치하지 않는다. 일반 설계 패턴을 현재 Python worker/server 경계 안에서 독립 구현한다.
R3 start grant·uncertain no-retry, R4 bearer-only vault, no-web/MCP/plugin 기본 차단은 불변 조건이다.

| 단계 | 범위 | 상태 | 필수 gate |
|---|---|---|---|
| A0 | W1 Codex exec-json 도구 이벤트 계약 연결 | PR #43 병합; PR/merge CI 통과 | work만 명령/파일 변경 schema 허용, text-only/unknown tool 거절, terminal ledger와 한도, synthetic stream |
| A1 | bounded work evidence + 검증 기록 | 구현 중 | agent 제출 미검증 표시, 완료 fence·메시지 가시성, secret-free 제한 schema, 원자 저장·legacy 호환; 독립 검증 영수증 별도 |
| A2 | 공동 코딩 task 의존성·write scope·workspace/review 계약 | 대기 | cycle·stale CAS·overlap·권한 검사, 다른 사용자 로컬 경로 미노출, 서버 자동 patch 적용 금지 |
| A3 | uncertain 사람 해결 | 대기 | 자동 재실행 금지, 이유·대상 attempt 감사 기록, 늦은 outcome 충돌 규칙 |
| A4 | 관찰 패널 재접속·작업 수명 분리 | 대기 | view detach와 cancel 구분, stable identity, cursor gap snapshot, 재접속이 호출 시작 안 함 |
| A5 | opt-in browser capability 설계/pilot | 보안 관문 대기 | 별도 소유 process/profile, egress·인간 승인·cookie 격리·untrusted context·artifact ACL. 기본 no-web 유지 |
| A6 | opt-in PDF/document capability 설계/pilot | 제품/보안/라이선스 관문 대기 | quarantine·parser OS 격리·quota/retry·room cache·asset pin·AGPL/의존성 검토. binary 기본 차단 유지 |

## A0: 도구 이벤트는 관찰이지 사전 승인 아님

기존 work 명령이 도구를 켜도 `_codex_exec`가 text-only item만 수용하던 문제를 수정했다.
승인된 work profile에만 좁은 `command_execution`/`file_change` 및 bounded `todo_list` 관찰 계약을 추가했다.
이벤트는 native 실행 후 도착할 수 있으므로 parser 거절/interrupt는 명령 미실행·파일 무변경 증거가 아니다.
시작한 prompt의 실패/불확실 결과는 기존 R3/worker uncertain 의미를 유지한다.

- 명시적 item type·ID·phase·status·필드 타입·크기/개수 상한을 검사한다.
- 열린 tool item이 남으면 final turn success로 처리하지 않는다.
- command exit nonzero/tool failed는 관찰된 결과일 뿐 곧바로 전체 turn 실패나 테스트 통과가 아니다.
  모델이 오류를 설명하고 final 답변을 낼 수 있지만 검증 성공으로 표시하지 않는다.
- raw command/output/path를 일반 로그·서버 evidence에 무차별 저장하지 않는다.
- unknown MCP/web/다른 tool은 fail-closed, text profile은 종전 거절 유지.
- 실제 CLI schema와의 실계정 호환 시험은 NOTRUN이며 합성 stream 통과와 구분한다.

## A1: 제안 계약

처음에는 버전이 있는 제한된 종류·상태별 개수만 보관한다. 자유형 명령·출력·경로·종료 코드·바이트 수를 서버에 전송하지 않는다. 완료·정리 성공 후 고유 item의 마지막 상태로 집계하며 이벤트별 변화 수를 고유 파일 수로 오인하지 않는다.
evidence는 일반 public task 필드가 아니라 완료 message의 seq/현재 read_start_seq 필터에 귀속한다. 기존 task projection은 arbitrary 필드를 새 멤버에게 노출할 수 있다. heartbeat/event에 싣지 않고 동일 완료 payload의 fingerprint와 원자 저장으로 replay를 고정한다.
서버는 어떤 승인된 agent도 이 값을 제출할 수 있으므로 출처를 `agent_reported_unverified`로 표시한다. 형식과 범위만 검증하며 runtime 이벤트의 실제 출처, 파일 변경·명령 실행·테스트 정확성을 검증했다는 뜻이 아니다.
base commit/diff/output digest와 독립 verification receipt는 증거를 실제 생성하는 승인된 producer가 있을 때만 추가한다.
존재하지 않는 증거를 자동 생성하거나 agent final 문장에서 'passed'를 추출해 검증으로 승격하지 않는다.

## 단계 종료 방식

각 단계는 작은 PR로 분리하고 synthetic 회귀·독립 review·문서 gate·필수 Windows/PostgreSQL 및 POSIX CI를 통과해야 한다.
현재 분석 산출물은 승인된 설계 근거로 보존하며 제품 구현·테스트 통과의 영수증으로 쓰지 않는다.
