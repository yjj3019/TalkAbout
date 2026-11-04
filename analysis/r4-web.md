# R4 웹 성능 — SSE `room_update` 코얼레싱 (P1-4 / B7·B8)

- 대상: `talkabout/web/app.mjs` **단일 파일 국소 패치**. 근거: `analysis/r3-talkabout-baseline.md` §0 B7/B8, `analysis/r3-00-SYNTHESIS.md` §4.3 P1-4, 서버 배치 근거 `talkabout/api.py:131-134`(한 SSE 메시지 = `data.events` 이벤트 배치).
- 라이선스: 외부 저장소 코드 복사 없음. 디바운스/스로틀 개념만 자체 구현(외부 코드 인용 없음).

## Before → After

Before (`talkabout/web/app.mjs:967` 이전): `room_update` 수신마다 즉시 `refresh()`. `refresh()` 1회 = `GET /api/v1/rooms` + `openRoom()`(`GET rooms/{id}` + `GET .../interventions`) + `agents`/`handovers`/`capabilities` ≈ 6 HTTP. 빈 `events` 배치(heartbeat 성격)도 동일 비용.

After (`app.mjs:967-974`, `app.mjs:1000`, `app.mjs:17`):
1. 이벤트 핸들러는 `data.events`를 파싱해 **비어 있으면 refresh 생략**, 배열이 아니면(속성 없음) 보수적 refresh. 파싱 실패(JSON 오류)도 보수적 refresh.
2. 유효 이벤트는 `queueRoomUpdate()`로 모은다 → **마지막 이벤트 후 100ms 조용할 때 1회**. 연속 이벤트는 타이머를 재장전하되, **첫 이벤트로부터 400ms가 지나면 즉시 flush**(상한, 창이 길어져도 데이터가 늦지 않음).
3. `document.hidden`이면 예약하지 않고 `roomUpdatePending`만 세운 뒤 `visibilitychange`(가시화)에서 **1회 따라잡기**(`communityWatch`의 hidden 가드와 동일 스타일).
4. `closeLive()`가 타이머/대기 플래그를 리셋 → 방 전환·로그아웃·수동 새로고침 시 이전 방의 지연 refresh가 새 방을 건드리지 않음. `access_changed`(직접 refresh + closeLive), `stream_end`(closeLive+안내), `onerror`(직접 refresh)는 기존 동작 유지(`onerror`만 대기 타이머 중복을 막기 위해 reset 추가).

## 왜 안전한가

- `refresh()`/`listEpoch`/`view.epoch`/`readController` 경쟁 상태 메커니즘은 **한 줄도 바꾸지 않았다.** 코얼레싱은 refresh **호출 횟수**만 줄이며, 각 refresh는 기존과 동일하게 epoch로 자기 무효화를 판정한다.
- refresh 최대 빈도가 이벤트 빈도와 분리됨: 조용한 경우 ≤10회/s, 폭주 시 ≤2.5회/s(400ms 상한). 기존에는 이벤트 수만큼 무제한.
- 빈 배치 생략은 "변경 없음"이라는 서버 계약(`events` 배치)에만 근거하며, 스냅샷을 바꾸지 않는 배치라 안전. 계약을 벗어나는 payload는 전부 **기존 refresh 경로로 폴백**.
- 지연 상한: 마지막 이벤트 후 최대 100ms(조용) / 첫 이벤트 후 최대 400ms(폭주). 탭 복귀 시 즉시 1회.
- 126KB 파일 전체 재포맷 없음: 변경 13줄(+11/−2), 인라인 SVG/CDN/모션 추가 없음, `prefers-reduced-motion` 무관.

## 검증 (실행함)

- `node --check talkabout/web/app.mjs` → 통과(ESM).
- JS 계약 테스트 12개 파일, **총 247 tests / pass 247 / fail 0**: `node --test`는 이 샌드박스에서 `spawn EPERM`으로 실패(문서화된 환경 제약, `docs/STATUS.md:47`) → 저장소 관례대로 **`node tests/<file>.mjs` 인프로세스**로 실행.
- 신규 로직 전용 임시 하네스 14개 체크 전부 통과(빈 배치 무요청, 5연속→1회, 400ms 상한, 파싱 실패 폴백, hidden 지연→가시화 1회, access_changed 직접 refresh+대기 취소, 방 이탈 시 취소). 하네스는 실행 후 삭제(저장소에 미잔류).

## 측정 방법(인용 가능한 수치)

- 코드 계량: 기존 요청/분 ≈ 6 × (events/min). 코얼레싱 후 ≈ 6 × refresh/min, refresh 상한 = 150/min(400ms)이므로 폭주 시에도 최대 900/min이나, 실제로는 이벤트가 몰린 만큼 줄어든다. 예: 10개 이벤트가 100ms 창에 몰리면 기존 60요청 → 6요청(1/10). 조용한 방은 변화 없음.
- 실브라우저 측정 절차: DevTools Network에서 `/api/v1/` 요청 수를 방 1개·1분간 집계, 패치 전후 비교. 서버 폭주 재현은 테스트 픽스처의 다건 메시지로 가능(별도 미실시).

## 위험 / 남은 일

- `room_update`가 "내 방 스냅샷 변경"을 항상 담는다고 가정하지 않음: 계약 밖 payload는 refresh하므로 **최악의 경우 기존과 동일 비용**(회귀 없음), 최선의 경우 대폭 감소.
- SSE와 10초 폴링(`communityWatch`)의 중복 refresh는 이번 범위 밖(기존 동작 유지). 필요 시 `communityRead` 유사 가드로 후속 통합 가능.
