# 실제 에이전트 연결 구현·검증 기록

확인일: 2026-10-17 Asia/Seoul (2026-10-16 UTC)
기준 main: `d9ddad53c47e0c75bdd04bc8635996c33241c481`
상태: 구현 브랜치, 운영 미배포. 실제 공급자 계정 E2E 미수행.

## 반영 내용

- 신원 등록 후 KO/EN 연결 wizard. runtime/auth 방식 선택, 명시적인 단기 credential 발급, 비밀 없는 설정 복사/다운로드, 연결 단계 표시.
- 서버 runtime-check challenge/완료/실패 계약. credential·room 바인딩, 만료·철회·회전·runtime 변경 처리. 결과는 `worker_reported_test`이며 공급자의 정체성 인증이 아니다.
- `connect` CLI: 별도 세션 시험 호출 후 유한 worker 실행. 명시적 join, 모델 호출 예산에 시험 포함, unknown 호출의 자동 재실행 금지.
- 방·generation·membership revision별 runtime pool. OS 독점 journal lease, 중단 확인과 명시적 `--resume-worker`.
- Codex exec JSONL 기본 / Claude stream-json / 명시 API-key Grok ACP 구현. 구조화 session ID와 terminal 결과·프로세스 정리를 확인한다. Codex app-server는 안전 옵션이 지원되는 환경에서만 선택 가능한 내부 경로다.
- 시작 전 CLI 도움말·버전의 안전 옵션 검사. 단순 설치 여부로 연결 성공을 표시하지 않는다.
- 방 시작 시 모든 수락된 발언 참가자의 최근 인증 통신과 유효 시험 결과 검사. 초대 대기 제외. synthetic fixture는 실제 AI와 구분.

## 실행한 시험

| 검증 | 결과 | 범위 |
|---|---|---|
| Python, browser fixture 제외 | 271 passed / 24 skipped | 서버 MemoryStore, CLI 통합, fake protocol, retry/cancel/격리/권한 회귀 |
| Node UI logic | 93 passed | KO/EN, auth consent, 비밀 표시 경계, stale 응답, readiness |
| Python compileall | PASS | talkabout 패키지 |
| pip check | PASS | 이번 격리 venv의 설치 의존성 |
| git diff --check | PASS | 작업 트리 |
| 독립 검토 | 발견 결함 수정 후 관련 시험 통과 | 영구 시험 quota, 중단 무시, 방 세션 혼용, check의 receipt 무효화, stale GET에 의한 wizard 소실 |

Python 3.12 환경, pytest 9.1.1, FastAPI 0.141.1, httpx 0.28.1에서 실행했다. 저장소 lock 파일 전체를 재현한 시험이라고 주장하지 않는다. 기존 Starlette/httpx deprecation 경고 1건이 있다. skip은 Windows 전용 프로세스 시험·명시적 PostgreSQL fixture 미제공 등이 포함되며 성공으로 계산하지 않았다.

## 브라우저와 실제 runtime 확인의 경계

자체 환경의 설치 Chromium으로 격리 fixture 실행을 시도했으나 browser startup의 socket 생성이 `Operation not permitted`로 종료됐다. 권한 검토를 거친 재시도에서도 동일했다. 따라서 이번 수정의 실제 브라우저 육안/통합 시험은 **환경 차단·미검증**이다. 로직 시험을 screenshot/브라우저 성공으로 바꾸지 않는다. `TALKABOUT_TEST_CHROMIUM`으로 명시한 격리 브라우저 실행 경로를 제공한다.

사용자 PC에서는 명시된 CLI의 **버전과 도움말만** 확인했다.
- Codex 0.163.0: `--ignore-user-config`는 exec에 있고 root/app-server에는 없음. 이에 기본 adapter를 exec JSONL로 구성했다.
- Claude Code 2.1.301: 요청한 구조화 입출력·안전 플래그 존재.
- Grok 1.0.60: ACP stdio 존재, `--no-memory` 없음. 해당 옵션을 실행 명령에서 제거하고 격리 home/config 정책을 사용한다.

도움말 확인은 로그인·모델 응답·권한 집행·과금 검증이 아니다. 실제 계정 사용, 모델 요청, 새 자격증명, 운영 데이터/서버 설정 변경은 이 검증에서 수행하지 않았다.

## 남은 기능·실증

- **Grok 구독(native) 연결 미완료**: 기존 auth와 startup hook/plugin 설정을 안전하게 분리하는 방식이 미검증이다. 기본 native 모드는 실행 전에 차단한다. API-key 모드도 실제 Windows 실행 파일의 startup 격리 검증 전에는 `runtime_workspace_isolation_unverified`로 차단한다. 우회 옵션은 없다. 격리 HOME/config/temp 및 소유한 Git 경계는 합성 시험으로 확인했으나 설치된 Grok 1.0.60의 집행을 입증하지 않는다.
- Windows 외 실제 CLI 실행은 containment 검증 전 차단. Linux 서버 자체와 참가자 runtime 지원은 별개.
- 실제 Codex/Claude/Grok 응답, 동일 세션 후속 대화, 실계정 승인 거부·취소·프로세스 정리 및 사용량은 아직 시험하지 않았다.
- CLI 재시작 후 provider session 자동 복구, 장시간 상주 서비스, 이미지·파일 artifact 처리, 임의 외부 A2A 연결은 이번 구현의 완료 항목이 아니다.
- 운영 병합·배포 전 Windows 합성 프로세스 회귀, 실제 브라우저, 격리 PostgreSQL, 명시 승인된 provider E2E가 필요하다.

## English status

This branch implements the connection workflow and bounded, text-only runtime contracts. Synthetic tests are not evidence of provider authentication or real model execution. Browser startup was blocked by the test environment. Both Grok native authentication and API-mode startup isolation, as well as non-Windows runtime execution, remain fail-closed. No production deployment, provider requests, or billing changes were performed.

## Windows verification checkpoint

JJYOO-PC의 격리 검증은 이전 head `194c32b302167b93c94404ea9ba39f61216eab43`, tree `b33b94d742bf12568ef94efe6a96b8349b962562` 대상으로 수행했다. 최신 Grok 차단 변경의 Windows 재검증 결과가 아니다.

- Python: 275 passed, 17 skipped, 2 errors, 0 failed. PostgreSQL 16건과 POSIX 전용 1건 skip.
- 오류 2건은 큰 매개변수의 자동 test ID가 262,247자로 확장되어 Windows 환경 변수 한도를 넘은 한 케이스의 setup/teardown이다. 짧은 ID를 사용한 별도 보조 시험 6 passed는 원본 suite 통과를 대체하지 않는다.
- Node: 91 passed.
- Chrome: 기존 흐름 및 연결 wizard 36조합 통과; KO/EN 및 390px 포함. 화면 49장 보존.
- 기존 venv를 재사용한 자식 fixture의 이전 checkout 로딩을 막기 위해 PYTHONPATH를 지정 ref로 고정했다. 전용 브라우저·서버 종료 확인.
- 실제 provider·계정·운영 사이트·서버·운영 DB·배포 NOTRUN. 최신 head의 Windows 재실행은 아직 미수행이다.
