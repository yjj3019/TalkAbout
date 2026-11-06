# R6 다음 단계 3기능 시뮬레이션 결과 (2026-11-06, 완전 해결판)

도구: `tools/simulate_r6_contracts.py` (커밋됨, 재현: `python -B tools/simulate_r6_contracts.py --seed N --rounds 100 [--race]`)
시드: `20261106` / `7` / `424242` — 기능당 **100회 × 3시드 = 300 라운드** + `--race` **스토어 기반 스레드 경합 100회 × 3시드**, 총 위반 0건.

시뮬레이션 방식: (a) 서버가 방 변경을 스토어 락으로 직렬화하는 모델의 **순차 적대적 인터리빙** 무작위 생성으로 실제 도메인 함수 호출, (b) `--race`는 **MemoryStore 락 아래 실제 병렬 경합**(grant 8스레드 / outcome 8스레드 / complete 2스레드) — "스레드 경합은 시뮬레이션 범위 밖"이었던 한계를 폐쇄.

---

## ① 정보-마커/콘텐츠 프레임 — 클린 (0 위반)

점검: 프레임 줄 접두 전 행, dict `marker` 고정값, 프레임 내 실제 제어·bidi 코드포인트 부재(이스케이프 후), 바이트 수·sha256 자기일관, 예산 초과 전량 거절, 프롬프트 헤더 문장 1회, JSON 페이로드 무결성, 제어만으로 채운 몸통·미정렬 갈래·빈 본문·lone 서로게이트 포함.

**관찰 (설계 노트, 계약 고정됨)**: 대화 내용이 사용자 의도로 마커 문구(`INFORMATION, NOT INSTRUCTIONS`)를 정확히 인용하면 전체 텍스트 기준 등장 횟수가 2가 될 수 있다. 계약상 "1회"는 **모듈이 추가하는 횟수**(프레임 dict `marker` 필드 1 + 프롬프트 헤더 문장 1)로 한정되며, 본문 내 에코는 인용 데이터라 이스케이프 대상이 아니다. **완전 해결**: `test_room_content_frame_marker_is_added_exactly_once_in_the_header`가 **헤더 기준 정확 1회 + 본문 에코 허용 + 페이로드 무결성**을 고정, 시뮬레이션 A도 헤더 기준 검사로 교정(초기 "marker-count 0" 8건·"raw-control" 8건은 검사기 오탐 — 제품 결함 아님).

## ② retry 계약 — 클린 (0 위반, 집행 300회 + 경합 300회)

점검: 시도당 신규 실행 1회, outcome 응답 `retry_allowed: False` + 닫힌 `retry_reason`(`terminal`/`outcome_unknown_no_auto_retry`), **재생 응답도 동일 계약 균일 적용(수정)**, 종결 상태 모순 거절, 발명된 상태 거절, `uncertain` 후 신규 작업 차단(lease 0), 해소는 `ack_unknown_effects` 필수·재생 멱등. **경합**: grant 8스레드 동시 → 신규 1건·나머지 전부 `reservation_already_started`, outcome 8스레드 동시 → 전원 응답(신규 1 + 재생 7) 모두 계약 키 보유, complete 2스레드 동시 → **단일 claim 1건**.

## ③ 입력 바인딩 — **결함 1건 발견 → 수정됨**

**결함 (심각, 수정 완료)**: `input_fingerprint`가 sha256 없는 미바인딩 소스를 조용히 제외했다.
- `[미바인딩 소스만]` → `None` → **미주장(`[]`)·레거시 미검증 입력과 동일 지문**: "아무것도 입력하지 않음"과 "뭔가 주장했으나 바이트 미바인딩"을 구분 불가.
- `[바인딩 s1, 미바인딩 s2]` → `[바인딩 s1]`과 **지문 동일**: 주장 소스 집합이 달라도(하위집합으로 축소되거나 미바인딩으로 약화돼도) 지문이 안 바뀌는 **무단 축소 공격면**.

수정([talkabout/collaboration.py](talkabout/collaboration.py) `input_fingerprint`): **source_id 자체를 항상 접고**(미바인딩은 빈 다이제스트), `None`은 "소스 미주장"만. 단위 테스트 `test_input_fingerprint_is_none_only_when_no_source_is_claimed`로 고정. 검증된 매니페스트 경로(실제 산출물)는 항상 sha256을 운반하므로 동작 변화 없음 — 결함은 순수 함수 계약의 방어 경계였고 지금은 닫힘.

## 부수 발견 (시뮬레이션 픽스처 쪽, 제품 아님)

- 방 딕트에 `last_seq`/`artifacts` 누락 → `emit`/`validate_manifest` KeyError 2건: 시뮬레이션 픽스처 결함으로 수정(제품 코드와 무관).
- `reservation_outcome` 반환에 `now` 인자 누락 2건: 시뮬레이션 호출 버그로 수정.

## 검증

- `python -B tools/simulate_r6_contracts.py` (시드 3종 × 100회): **총 위반 0**
- 영향 단위 테스트: `test_collaboration_context.py`·`test_content_framing.py`·`test_work_prompt_policy.py`·`test_runner_reservations.py` 등 **149 passed** (PostgreSQL 픽스처 2건은 픽스처 미기동 예상 오류)
- `python -B tools/docs_check.py`: **0 new / 0 known**

## 남은 항목 해소표 (2026-11-06 완결)

| 남은 항목 | 상태 | 해소 내용 |
| --- | --- | --- |
| 지문 암호학적 강도 | **증거 추가** | `test_input_fingerprint_avalanches_and_stays_distinct`: 120개 서로 다른 주장 집합 전부 구별 + 바인딩 sha256 1문자 플립 시 **256비트 중 ≥100비트 변화(avalanche)** 고정. 충돌 내성은 표준 sha256 사양에 위임. |
| 비밀/개인정보 스크리닝 | **프리미티브 구현** | [content_framing.py](talkabout/content_framing.py) `redact_sensitive(text, secrets)` 추가 — 호출자 소유 선택형(최장 일치 우선·중복 교체 없음·자리 표시자 1개), redact→frame 순서면 **바이트 결속이 redacted 텍스트 기준**임을 테스트로 고정. 활성화는 호출자 정책(런타임 `blocked_values` 계약과 일치, 모듈이 자동 판정하지 않음). |
| provider 교체 시 입력 해시 강제 변화 | **정책 명확화** | 지문은 "제출 시점 인클루드 바이트" 기준. 메시지 트림/만료로 참조 `utf8_end`/sha256이 바뀌면 검증(`validate_manifest`)이 거부하거나 지문이 갱신됨 — 참조 변경을 숨기는 경로 없음(기존 byte-change 테스트로 입증). |
| PostgreSQL 픽스처 | **범위 유지** | 로컬에 psycopg/DB 픽스처가 없어 CI 소유(명시적 격리 config 필수). 스토어 락 계층 경합은 `--race`(MemoryStore)로 폐쇄됨. |

## 검증

- `python -B tools/simulate_r6_contracts.py` (시드 3종 × A/B/C/R 100회): **총 위반 0**
- 단위 테스트: framing 20 + collaboration 19 등 영향 스위트 전부 통과, `docs_check` **0 new / 0 known**

## 한계 (NOTRUN, 원리상 남음)

- 지문 충돌 내성의 절대 보증(256비트 sha256 사양에 위임), 자동 시크릿 *탐지*(정책상 호출자 소유 — `redact_sensitive`는 판정이 아닌 적용 도구), PostgreSQL 물리 격리 검증(CI).
