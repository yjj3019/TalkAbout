# Classroom platform (단톡방/클래스 룸) — 실현 계약 요약

> TalkAbout이 실현하는 사용자 스토리: **사람이 AI 에이전트들을 클래스 룸(단톡방)에 초대하고 사람이 조율하며, 에이전트들이 각자 생각을 말해 토론하고 산출물(문서·코드 등)을 생성·채택하는 플랫폼**. 모든 검증은 메모리 스토어 기반 합성 테스트이며, 실제 공급자·브라우저·tailnet 상호운용은 **NOTRUN**(예산·승인 게이트).

## 1. 사용자 요구 → 구현 계약 매핑

| 요구 | 구현 계약 | 위치 | 상태 |
| --- | --- | --- | --- |
| 방(단톡방/클래스 룸) | room(사람/에이전트 생성, 사람 핸드오버), topic·mode·limits·deliverable | [domain.py](../talkabout/domain.py) · API | 구현됨 |
| 에이전트 초대 | invitation + accept, 역할·권한·credential·runtime 검증 | agent-invitations / auth / connector | 구현됨(합성) |
| 사람 조율 | control(시작/일시중지/재개/종료/주제·모드·제한·산출물/핀/dispatch) + 승인 + 채택 | [core.py](../talkabout/core.py) | 구현됨 |
| 토론(각자 생각) | 3모드 – 자유 토론(명시적 턴) · 구조 토론(제안→검토→종합) · 공동 창작(연구→창작→검토) | modes/domain | 구현됨 |
| 산출물(문서·코드) | artifact 버전(텍스트) · change_proposal(코드 패치 `---`/`+++` 형상 검증, **적용 없음**) · 사람 채택 | artifacts/adoptions | 구현됨 |
| 산출물 방향 전환 | 조율 `change_deliverable` | `c920d43` | 구현됨 |
| 조율 현황판 | `GET /rooms/{id}/production`(읽기 전용, 고정 어휘 결정 4종) | [production.py](../talkabout/production.py) `d02e916` | 구현됨 |
| 검색 가시성 | `GET /rooms/{id}/search`(가시 창·무변경·사람 전용) | [search.py](../talkabout/search.py) `1e784d7` | 구현됨 |
| 시나리오 계약 | 문서·자유 토론·코드 패치 3종 E2E | [test_classroom_scenario.py](../tests/test_classroom_scenario.py) `5b06672`~`ce034b8` | 구현됨 |

## 2. 안전 경계 (불변)
- 채택·승인·실행은 **사람만**; 산출물은 서버가 저장·검증만 하고 **적용·배포·실행·모델 호출 없음**.
- 읽기 전용 프로젝션(production/search/diagnostics)은 상태·event·cursor를 절대 변경하지 않음(테스트로 고정).
- 모든 신규 계약은 fail-closed(미지 값/경계 위반은 거절), 고정 어휘·상한(바이트/개수) 존재.

## 3. 다음(승인 시) 로드맵
- 실제 공급자 런타임(worker/MCP)→산출물 제출 참조 계약, 브라우저 UI, tailnet 상호운용, 산출물(패치) 적용 — **NOTRUN**.
- 후속 관문: 회신 분류/원장/예산의 worker 경로 연결(R-07 lease 툼스톤 등, analysis/agent-tincan-review-2026-11-11 참조).
