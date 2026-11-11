# Classroom runtime execution path — 설계안 (승인 후 구현 대상)

> 목표: 사람이 조율하는 클래스 룸에서 **worker(MCP/런타임)가 산출물(문서·코드)을 제출하는 실행 경로**를, 현재 합성 계약(서버 형상 검증·멱등·사람 채택) 위에 안전하게 연결한다.
> **경계**: 이 문서의 연결·배포는 아직 **미구현(NOTRUN)** — 실제 공급자 런타임·브라우저·tailnet 상호운용, 산출물(패치) 적용은 승인 후 도입. 아래 경로의 *서버 계약*은 이미 합성 테스트로 고정됨.

## 1. 실행 경로 (설계)

```
[사람] change_deliverable/change_mode 조율 ──> room{deliverable: analysis|change_proposal}
   -> schedule: researcher -> creator -> reviewer (co_creation) / 발언 턴 (free)
[worker] GET rooms/{id}/tasks (assignee) -> claim(멱등 key, lease) -> preflight(fencing)
   -> runtime 실행(모델 부재: SyntheticRuntime fixture) -> complete {
        text(산출물 본문), generation/membership_revision, attempt, input_manifest
     }
[서버] 형상 검증(fail-closed):
   - change_proposal 방: text는 --- / +++ 마커 포함 필수, 아니면 invalid(거절)
   - analysis 방: 텍스트 1..64000B
   - creator task: artifact 버전 기록(ai_generated=True), 동일 key 재시도는 멱등
[사람] production(채택 대기) -> adopt -> adopted 기록(코드 패치 적용은 절대 없음)
```

## 2. 안전 계약 (불변, 이미 테스트로 고정)
- complete 응답 손실 후 동일 키 재시도 = 동일 응답(멱등) — `test_modes_handover`.
- 비(非)diff 제안 거절 — `test_work_deliverables`.
- 채택·승인·실행 명령은 사람 전용; 에이전트는 room:contribute 한도 내 제출.
- worker task context는 room.deliverable 포함(`worker.py` line 642) — 신규 계약 아님.

## 3. 승인 후 구현 순서 (추정, 리뷰 필요)
1. **worker 산출물 출력 정책**: runtime 결과를 room.deliverable 형식(문서/패치)으로 제출하는 참조 변환(합성 runtime) + 형상 위반 시 fail-closed.
2. **worker 실행 경로 시나리오 5**(현 시나리오 4와 동일 서버 경로를 Worker 클래스 구동으로 확장 — worker_pool_e2e 패턴).
3. **브라우저 UI**: production/search를 조율 패널로, change_deliverable/채택 제어 표면.
4. **실제 공급자·tailnet 연결, 산출물(패치) 적용** — 별도 승인.

## 4. 기존 합성 커버리지
| 경로 구간 | 테스트 |
| --- | --- |
| claim/lease/fencing/멱등 | worker_api · worker_pool_e2e · required_predecessor_worker |
| 산출물 형상·버전·채택 | work_deliverables · modes_handover |
| 사용자 스토리 4종 | classroom_scenario |
| 조율 현황/방향 전환 | room_production · control_change_deliverable |
