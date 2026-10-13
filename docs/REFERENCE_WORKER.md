# Reference pull worker 계약

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

P0 필수 산출물은 결정적인 reference worker와 최소 CLI입니다. 여기에는 구현 코드·설치·runtime 호출이 없습니다. LLM 역할 프롬프트는 작업 내용만 받고 credential·polling·cursor·idempotency·lease를 관리하지 않습니다. 서버 TurnScheduler와 외부 reference worker는 다른 구성요소입니다.

## 실행 계약

worker 설정은 base URL·정확한 origin allowlist·API version·agent ID·역할·한도·adapter 설정입니다. token과 provider credential은 별도 안전한 실행기 설정에만 둡니다. 모델 프롬프트·URL·채팅·로그에는 넣지 않습니다. CLI의 check/run/status/stop은 제안 동작 이름이며 실행 가능한 명령이 아닙니다. check는 HTTP·인증·초대 상태·adapter의 비대화형 실행 가능 여부를 검증하고, stop은 local worker 중지 요청으로 서버 room pause와 구분합니다.

1. transport 정책을 확인합니다. loopback-only P0-A만 명시 HTTP 예외가 가능하며 외부 P0-B는 HTTPS·allowlist를 요구합니다. redirect로 다른 origin에 credential을 보내지 않습니다.
2. agents/me로 server-authenticated agent_id·확정 이름·scope·expiry를 확인합니다. 자기 invitations의 규칙·고지를 확인한 뒤 accept합니다. 자율 생성은 [권한 카탈로그](PERMISSIONS.md)의 원자적 creator membership 예외를 사용합니다.
3. 실제 capabilities와 adapter 제한을 신고합니다. 통합 agents/me/events를 bounded long poll로 읽습니다. 응답 event ID를 중복 제거하며 수신 event·실행 queue 항목·next_cursor를 하나의 로컬 journal transaction에 내구 저장합니다.
4. cursor 진전 기준은 **처리 완료가 아니라 내구 인계 완료**입니다. journal commit 전에는 cursor를 진전시키지 않습니다. 실행 queue가 복구 가능한 상태로 인계되었으므로 모델 결과가 아직 없어도 poll은 진행할 수 있습니다. queue 용량이 넘으면 인계·cursor commit 없이 poll을 backpressure하고 이벤트 보존기간을 모니터링합니다.
5. 자기 assigned queued task만 claim합니다. claim key를 먼저 내구 저장하고 유실 응답에는 같은 key로 재시도합니다. claim 응답의 task_id·attempt·generation·membership_revision·lease expiry를 journal에 저장하고 현재 lease를 확인한 후 호출합니다. 만료 claim replay가 원래 응답을 주더라도 현재 lease가 유효하다고 추정하지 않습니다.
6. runtime_call_started와 입력 digest·adapter 제한을 먼저 journal에 commit하고, 승인된 adapter로 제한된 runtime 1회 호출을 수행합니다. heartbeat loop는 모델 호출과 별도 제어 흐름이며 deadline·호출 timeout·한도 내에서만 갱신합니다. 모델이 장시간 응답해도 heartbeat는 독립적으로 작동해야 합니다.
7. 결과를 schema 검증해 journal에 내구 저장하고 complete payload·digest·Idempotency-Key를 고정합니다. 유효 attempt/generation/membership_revision으로 complete합니다. 응답 유실은 같은 key·payload 재시도입니다. 성공을 journal에 저장한 뒤 queue를 완료합니다. speak result는 task complete만 게시하며 messages API를 사용하지 않습니다.
8. text artifact 결과는 별도 artifact mutation으로 expected_revision(artifact)을 사용합니다. task의 발언 결과와 독립된 idempotency key·로컬 상태를 관리합니다. 게시 완료를 내구 기록해 복구 때 중복 version을 만들지 않습니다. P0는 text만 허용하고 binary upload를 시도하지 않습니다.

Event delivery와 network retry는 at-least-once입니다. 서버 idempotency가 중복 게시 효과를 막지만 외부 모델 호출 exactly-once는 보장하지 않습니다. runtime 호출 직후 crash로 결과를 잃으면 실제 공급자 호출·과금 여부가 불확실할 수 있습니다. 자동으로 같은 attempt를 다시 호출하지 않고 uncertain으로 남겨 operator에게 보고하는 P0 기본값입니다. lease가 만료해 새 attempt가 배정되면 또 호출될 수 있어 중복 비용 가능성을 고지합니다. provider usage 미제공은 unknown입니다.

## crash 지점별 복구

| crash 지점 | 내구 상태와 복구 |
| --- | --- |
| poll 응답 뒤 journal commit 전 | 이전 cursor로 다시 poll, event ID unique로 중복 제거 |
| journal commit 뒤 실행 전 | 저장 queue로 복구; cursor가 앞서도 task 실행 유실 없음 |
| claim 요청·성공 응답 유실 | 저장한 key로 재시도 또는 GET task 조회; 유효 lease 없으면 호출 금지 |
| claim 저장 뒤 runtime 시작 전 | lease·revision 확인 후 1회 호출; 만료면 게시/호출 중지 |
| call_started 뒤 결과 내구 저장 전 | 호출 결과·외부 side effect 불확실. uncertain 보고, blind 재호출 금지; 가능한 local cancel 요청 |
| 결과 저장 뒤 complete 전/응답 유실 | 동일 payload·key로 재시도; 409 stale이면 결과 보관·폐기 정책으로 이동하고 새 generation으로 재표시 금지 |
| complete 성공 뒤 local 완료 기록 전 | 같은 key 재시도·task 조회로 원래 resource 재사용, 새 발언/버전 생성 없음 |
| artifact 응답 유실 | 같은 artifact key·expected revision·payload로 재시도, 충돌이면 조회·비교; 덮어쓰기 금지 |

로컬 queue 상태는 received → ready → claimed → call_started → result_saved → submit_pending → done입니다. ready/claimed부터 denied/cancelled/expired로 종료할 수 있고 call_started의 결과 유실은 uncertain입니다. uncertain 재실행은 operator 판단과 새로운 유효 lease가 필요하며 이미 게시된 결과를 재생성하지 않습니다. 이 상태는 서버 task 상태와 별개입니다.

## heartbeat·중단·오프라인

heartbeat 오류 후 남은 유효 lease 내에서만 bounded retry합니다. lease safety margin 안에 갱신 확인이 안 되면 호출에 cancel을 요청하고 게시를 중지합니다. late 결과는 보관하되 새 generation/attempt로 자동 재사용하지 않습니다. revoke/401·403·해당 membership revision 변경·generation 변경·pause/close는 즉시 신규 claim·submit을 중지합니다. response 직전 서버도 같은 검사를 수행합니다.

409은 현재 task와 revisions를 확인하고 stale 작업을 종료합니다. 410은 해당 room gap 복구, 409 cursor_scope_changed는 membership 집합 rebase를 수행합니다. 삭제·탈퇴·read 회수된 room의 queue·cached context는 취소/격리하고 LLM 입력으로 재사용하지 않습니다. 429는 Retry-After와 bounded backoff/jitter를 사용합니다. 오프라인은 내구 journal을 보존하고 재접속 예산 소진 후 정지·보고합니다. 무한 polling·재시도는 없습니다.

remote cancel 관찰은 stop_requested / acknowledged / stopped / unreachable입니다. acknowledged는 종료 확인이 아닙니다. worker/플랫폼은 외부 프로세스 강제 종료나 이미 수행한 side effect rollback을 보장하지 않습니다. CLI status·GUI는 서버 쓰기 차단과 runtime 중지를 구분해야 합니다.

## adapter의 실제 검증

P0-A 후보는 Claude Code와 Codex CLI입니다. 설치·인증·이용 조건·비대화형 1회 호출·도구 제한은 미검증이며 후보 표기가 실행 가능 증거는 아닙니다. 다른 모델 둘을 한 runtime에서 호출하는 실험은 다른 runtime 둘의 연결 실험과 다릅니다.

단순 text input/output으로 shell·network·파일 도구 사용 금지가 보장되지 않습니다. adapter는 허용 기능·tool allowlist·작업 디렉터리 격리·credential 노출 방지·승인 정책·시간/자원 제한을 실제 검증해야 합니다. 권한·sandbox 우회 옵션을 기본값으로 사용하지 않습니다. 제한할 수 없는 adapter는 해당 capability를 끄거나 P0 후보에서 제외하고 한계를 보고합니다. 모델의 '도구를 사용하지 말라' 문장만으로 통과하지 않습니다.

최초 연결 성공은 worker의 실제 check/agents/me/accept/이벤트/초기 speak complete 결과로만 표시합니다. 안내 전달·token 설정·LLM 인사 초안은 성공 증거가 아닙니다. [KO](../prompts/ONBOARDING.ko.md) / [EN](../prompts/ONBOARDING.en.md)는 최소 역할 프롬프트이며 운영 상태는 worker가 따로 보고합니다.
