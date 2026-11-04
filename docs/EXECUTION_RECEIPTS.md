# 실행 영수증 (provider 세션 결속)

이 문서는 **작업 트리 기준**입니다. 배포·실제 provider 호출·공급자 감사 API 연동을 뜻하지 않습니다.

## 목적

TalkAbout 서버는 provider를 호출하지 않으므로 "모델이 실제로 호출됐고 그 응답을 읽었다"를 스스로 증명할 수 없습니다. 실행 영수증은 이 한계를 **대조 가능한 형태**로 좁힙니다. 워커가 완료 보고에 provider가 발급한 세션 핸들(Codex thread/session id, Claude session id, HTTP provider 응답 id)을 함께 보내면, 서버는 다음을 기록합니다.

- 보고가 인증된 연결의 runtime과 일치하는지 (불일치·형식 오류는 `invalid_execution_receipt` 422)
- 세션 핸들의 SHA-256과 완료 메시지 본문의 SHA-256 (원문 세션 id는 작업 내부에만 보관)
- provider가 usage를 반환한 경우 bounded 정수 토큰 수 (`input_tokens`, `output_tokens`)

완료 메시지에는 `execution_receipt` 메타데이터(세션 해시·본문 해시·runtime·usage)가 붙고, UI에는 "실행 영수증" 칩이 표시됩니다. 영수증이 없는 기존 워커의 보고는 이전처럼 동작하며(legacy), 영수증은 필수가 아닙니다.

## 대조 절차 (운영자)

영수증은 **워커의 주장**이며 공급자 인증이 아닙니다. 다만 다음 절차로 provider 측 기록과 독립적으로 대조할 수 있습니다.

1. 작업 실행 컴퓨터에서 해당 provider의 로컬 세션 기록을 찾습니다. Claude Code는 사용자 홈의 `.claude/projects/**/*.jsonl`, Codex CLI는 `.codex/sessions/**/*.jsonl`(버전에 따라 경로가 다를 수 있음)을 확인합니다.
2. 세션 id 후보들에 대해 SHA-256을 계산해 메시지의 `execution_receipt.session_sha256`과 비교합니다. 일치하면 동일 provider 세션의 기록입니다.
3. 그 세션 기록에서 assistant 응답 텍스트를 찾아 UTF-8 SHA-256을 계산하고 `execution_receipt.text_sha256`과 비교합니다. 일치하면 게시된 결과와 provider가 반환한 응답이 바이트 단위로 같습니다.
4. 필요하면 provider 대시보드·감사 로그에서 해당 응답 id/세션의 존재와 시각을 확인합니다.

해시 도구 예: `python -c "import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"`.

## 남는 한계

- 영수증은 워커가 스스로 보낸 값입니다. 악의적 워커는 그럴듯한 세션 핸들을 보낼 수 있습니다. 위 대조가 실패하면 그 보고를 근거로 실행을 승인하지 마세요.
- 서버는 원문 세션 id를 보관하지만 API로 노출하지 않습니다. 대조는 실행 컴퓨터의 로컬 기록을 기준으로 합니다.
- CLI runtime은 토큰 usage를 안정적으로 노출하지 않을 수 있습니다. usage가 없으면 비용 추정은 provider 대시보드/쿼터에 의존합니다.
- 모델의 이해·정확성은 어떤 영수증으로도 증명되지 않습니다. 결과 검토와 후속 질문이 필요합니다.
- 승인 후 provider로 전달된 바이트는 회수할 수 없습니다. 시작 전에는 dispatch hold·중지·lease fencing으로 새 작업을 막을 수 있지만, 이미 시작된 외부 실행은 서버가 되돌릴 수 없습니다.

## 관련 경계별 방법

| 경계 | 앱에서 가능한 방법 | 남는 것 |
| --- | --- | --- |
| 모델 수신·이해 | 실행 영수증(세션·응답 해시)으로 provider 기록 대조, 게시 결과 검토 | 이해·정확성은 증명 불가 |
| 승인 후 회수 | `hold_dispatch`/`release_dispatch`로 새 배정·시작 차단, worker stop, lease 만료 | 이미 시작된 외부 호출 |
| 비용 상한 | 연결 명령의 `--max-model-calls`, 런당 노드 16개 상한(자동 재시도 없음), usage 반환 시 영수증 기록 | 서버는 금액을 모르므로 provider 키별 하드 쿼터·경보가 최종 상한 |
| 적대적 클라이언트 | 인증된 connection runtime과 영수증 runtime 일치 검사, fencing·projection, 자격증명 최소 권한 | 자격증명을 가진 워커의 직접 호출 |
| 운영 환경 | 배포 게이트 문서([DEPLOYMENT_RUNBOOK.md](DEPLOYMENT_RUNBOOK.md), [RELEASE_GAP_MATRIX.md](RELEASE_GAP_MATRIX.md)) | 승인된 격리 환경의 실제 검증 |

