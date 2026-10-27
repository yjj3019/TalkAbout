# TalkAbout 비교 기준과 새로 확인한 우선 결손

기준 commit: `44722bea2e761a6ed5cf04e6a40cb378b45a5bde`.
외부 저장소 분석 과정에서 현재 제품 소스와 대조한 **정적 확인**이다. provider 실행이나 제품 수정은 하지 않았다.

## P0: W1 command 설정과 event parser 계약 불일치

실제 공동 코딩의 가장 가까운 관문은 새 runtime 도입이 아니라 **이미 켠 작업 도구의 이벤트를 안전하게 수용하는 경로**다.

- [work profile 지원 범위](../../talkabout/agent_runtime.py#L72-L80)는 Codex exec-json만 허용한다.
- [work 명령](../../talkabout/agent_runtime.py#L667-L683)은 workspace-write이며 shell/unified_exec/apply_patch 도구를 켠다.
- 하지만 [handler 선택](../../talkabout/agent_runtime.py#L1350-L1351)은 모든 Codex exec-json에 같은 `_codex_exec`를 사용한다.
- [event 정책](../../talkabout/agent_runtime.py#L958-L979)은 `agent_message/reasoning/plan`만 허용하므로 `command_execution`/`file_change`를 받으면 `runtime_tool_policy_violation`이다. work_profile 분기가 없다.
- 이 경우 [실패 처리](../../talkabout/agent_runtime.py#L1359-L1365)는 unknown으로 분류하고 interrupt한다.

**정확한 결론:** command/file-change event를 내는 작업 턴은 현재 parser에 의해 거절된다.
텍스트 `agent_message`로만 코드/diff를 답하는 것까지 전부 실패한다는 뜻은 아니다.
**중요:** command/file-change 이벤트는 native tool 동작이 이미 일어난 뒤 도착할 수 있다.
parser가 거절/interrupt했다고 명령이 실행되지 않았거나 파일이 바뀌지 않았다는 뜻은 아니다.
prompt 전송 뒤 실패가 unknown으로 분류되는 이유도 여기에 있다. 이벤트 수용 수정은 관측 계약 수정이며
per-tool 사전 권한 강제나 OS sandbox 구현이 아니다.
실제 provider 실패를 재현한 것은 아니므로 현장 장애/과금 증거로 표시하지 않는다.
독립 read-only 교차검토에서도 같은 경로를 확인했다.

### 테스트 공백

[test_work_profile.py](../../tests/test_work_profile.py#L1-L5)는 refusal/command/prompt 검증이며 실제 작업 턴은 NOTRUN이라고 명시한다.
[도구 활성화 단언](../../tests/test_work_profile.py#L112-L128)은 있으나 work profile의 command/file-change stream 성공 테스트는 없다.
기본 text profile의 도구 거절은 유지해야 하므로 모든 item을 무조건 허용하는 수정은 부적절하다.

### 제안 gate (아직 구현/실행 안 함)

1. 승인된 work profile에만 `command_execution`/`file_change` 등 **명시한 event schema**를 허용한다.
2. unknown tool, MCP, web/network event는 기존처럼 거절하고 credential/원문 stdout을 서버 로그에 무차별 저장하지 않는다.
3. schema·크기·횟수·수명별로 bounded `worker_reported` evidence를 만든다. runtime event는 OS audit나 작업 결과의 서버 증명이 아니다.
4. synthetic transport로 시작/업데이트/완료/실패/중단/과대/손상/unknown event와 text-profile 거절을 검증한다.
5. 그 후 별도 사용자 예산 승인하에 disposable workspace의 실제 1-turn 파일 변경·테스트·취소를 검증한다.
6. R3 start grant/uncertain no-retry, R4 secret filtering 및 프로세스 cleanup 불변식을 유지한다.

## 다른 비교 기준

- [W1 경계](../../docs/WORK_PROFILE.md#L12-L19): MCP/hooks/plugins/web 기본 차단. 외부 기능이 있다는 이유로 자동 해제하지 않는다.
- [산출물 판별](../../talkabout/domain.py#L104-L117): 현재 patch는 `---`/`+++` 포함 여부의 shape check다. 적용 가능성·base revision·테스트 통과의 검증이 아니다.
- [공유 context](../../talkabout/collaboration.py#L1-L7): ACL-filtered source 존재/범위 확인이지 모델이 읽거나 이해했다는 증거가 아니다.
- [작업 권한 경계](../../docs/WORK_PROFILE.md#L45-L51): 플랫폼 OS sandbox나 서버 검증 실행을 주장하지 않는다.
- R3 start는 retained attempt당 협력 worker에 대한 일회 실행 승인이다. 여러 coding task의 파일 충돌·의존성·테스트 리뷰 DAG를 해결하지 않는다.
- R4 vault는 TalkAbout bearer at-rest 보호다. browser cookie/provider key/document cache를 함께 저장할 권한은 아니다.
