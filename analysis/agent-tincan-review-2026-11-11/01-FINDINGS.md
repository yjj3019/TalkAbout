# 01 — 정밀 검토 결과: 6개 영역 findings

Store: `00-SOURCE-MANIFEST.md` (revision `b07895d9`, MIT). 검토 범위: relay/envelope/gateway/client · identity/policy · store/history/notes/textfit · cli/onboard/watch/phone/mcpserver · wake/council/pass · process/tests/docs(병렬 6개 검토 + Lead 원문 검증). 근거는 파일 경로+라인/함수명.

## 발견 요약 (적용 가능성별)

### HIGH — TalkAbout gap을 직접 채울 수 있는 계약/설계

| id | 아이디어 (참고) | 근거 (agent-tincan) | TalkAbout gap |
| --- | --- | --- | --- |
| R-01 | **진행 알림 ≠ 최종 답변**: "working on it, report will follow"류를 최종으로 닫지 않고 보류, 좁은 휴리스틱+결과 단서 제외+명시 final 이스케이프, 실패 모드는 "늦게 닫힘(재실행 금지)" | `internal/envelope/ack.go` (MaxAckBytes·openers·leads·doneMarkers), relay keepAckOpen/closeHeldAcks | 회신/제출 분류가 없음 → **반영**: `talkabout/reply_cues.py` |
| R-02 | **주고받은 기록의 해시체인 무결성**: append-only, 각 행이 `prev_hash` 연결, 검증은 처음 깨진 위치 보고, detail은 메타데이터(개수)만 | `internal/store/audit.go` (Audit/VerifyAudit, auditHash), CLI `audit-verify` | 전역 tamper-evident 원장 부재 → **반영**: `talkabout/chained_log.py` |
| R-03 | **공정 배분 예산 + 예약 통지**: 여러 조각을 하나의 바이트 예산에 공정하게(작은 조각이 큰 조각에 굶기지 않게), 절단 통지는 예산 안에 고정 양식+카운트 | `internal/textfit/textfit.go` (Reserve=64, Truncate, Fit), council/prompts.go·pass/pack.go | fit_context는 단일 직렬화 크기 기반 → **반영**: `talkabout/text_budget.py` |
| R-04 | **전송 계층 신원이 발신자**: 본문/헤더의 from은 무시, 요청당 1회 판정, 조회 실패 시 fail-closed(deny) | `internal/identity/directory.go` (ResolveCaller), trust-model.md | 이미 적용됨(토큰/콜백 판정) — 계약 문서화만 보강 |
| R-05 | **웹 표면 바닥 경화**: 프레이밍·리바인딩 방어 헤더 2중화 | `internal/relay/browser.go` (HardenForBrowsers) | CSP는 있으나 `X-Frame-Options` 없음 → **반영**: api.py 헤더 1줄 |
| R-06 | **초대/자격증명은 digest만 보관**, 원문은 발급 순간에만; 재발급 시 이전 코드 폐기 | `internal/identity/directory.go` (hashInviteCode), store.go 각주 | 이미 code_hash 보관 + 일회성 — 동일 계약, gap 없음(문서화) |

### MEDIUM — 설계 참고 후속 관문 대상

| id | 아이디어 | 근거 | 후속 |
| --- | --- | --- | --- |
| R-07 | 전달(2m)→claim(30m/urgent 10m) 2단계 lease, 만료/requeue/claim-lapsed 구분(claim됐던 적이 있는 요청은 재배달 불가 툼스톤) | `internal/relay/server.go` constants, `internal/store/store.go` DeliverFor/ClaimLeasesFrom/Sweep | R5 시맨틱과 정합 — 실행 연결 시 재검토 |
| R-08 | reply generation 증분 + 발신자 전용 seen + (id,generation) ack, needs_input은 lease를 멈추는 부분 상태 | protocol.md §Replies the asker has not seen, envelope | 재접속 cursor와 통합 설계 후속 |
| R-09 | 검색 가시성 = 체인 가시성(SQL gating), 감사 detail은 `{"count"}`만 | protocol.md §Search | 미구현 검색 기능 관문 |
| R-10 | 능력 광고 게이팅: 서버가 광고한 기능만 클라이언트가 사용, 미지원이면 400/404+안내 | protocol.md §Attachments/§Schedules | MCP/CLI 도구 계약에 이미 유사 규칙 — 문서화 |
| R-11 | wake 텍스트는 개수·지시만(본문 금지), request-email 인증 tag는 감사/로깅 제외 | emailtag.go, trust-model.md | redaction 계약과 정합 — 적용 범위 문서화 |
| R-12 | stroke 옮김 금지: claim된 적 없는 것만 failover, loops(chain에 이미 있는 대상) 금지 | failover.go:276 | 워커 핸드오프 설계 시 재검토 |
| R-13 | 오래/짧은 ack 답장·재-hold·12시간 문맥 유지, 만료 시 명시 실패 문구 | email-후속 구간, protocol.md §Work leases 주석(6h 재질문) | R5 연결 시 검토 |
| R-14 | 대기 항목 요약을 모든 응답에 실어 폴링 최소화(X-Tincan-Held) | protocol.md §Held work | 진단 API 확장 후보 |
| R-15 | 요청별 0700 임시 디렉터리(해시 파일명)+모든 경로 정리+템플릿 답변(내용이 문구를 조종 못 함)+deadline과 분리된 전달 | history/serve.go (MkdirTemp/RemoveAll, renderReply) | 워커 첨부 스테이징 관문 |
| R-16 | 노트 이중 멱등(앱 idempotency key + 원장 준비/결과 기록, 미확인 시 "확인 불가" 명시 실패) | notes 패키지 ledger | 외부 저장소 at-most-once 후보 |

### LOW/NONE — TalkAbout 범위 밖 또는 위험 (반영 안 함)

- **L-01** mail/Push/웹 어댑터(chatgpt-web·history 등), Tailscale WhoIs, Funnel/A2A, 전화 UI — TalkAbout의 loopback/토큰 모델과 다른 도메인, 미적용.
- **L-02** 구 web 런타임 failover·handback·scheduled-웹 메시지 — 실제 웹 브라우저 런타임 미보유(NOTRUN 유지).
- **L-03** council 투표/참석/타임아웃 머신 — TalkAbout 방 모드가 이미 라운드/단계 모델 보유, 기능 중복 위험으로 미적용.

## 검증 여부

위 finding의 **모든 근거는 고정 revision `b07895d9`의 실제 코드/문서에서 라인 단위로 확인**했다. 실제 tailnet/MCP/웹 브라우저 상호운용, 실제 공급자 런타임은 **NOTRUN**이며 어떤 주장도 하지 않는다.
