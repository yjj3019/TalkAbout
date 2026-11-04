# R5 합성 — munder-difflin · kanzo에서 TalkAbout으로 가져올 것

대상: [munder-difflin](https://github.com/HarnessMD/munder-difflin) (MIT, 커밋 `8702b73`) ·
[kanzo](https://github.com/emanuele-em/kanzo) (AGPL-3.0, 커밋 `862399f`)
방식: **정적 분석만**(빌드/실행/테스트 실행/서버/모델 호출 없음). 상세 근거는 각 문서 참조.

| | munder-difflin | kanzo |
|---|---|---|
| 정체 | Electron 멀티에이전트 하네스("office of clones"). CLI 에이전트(Claude Code/Codex/Gemini…)를 PTY로 감싸고 프롬프트 오케스트레이터(god)가 운영 | Rust 단일 바이너리 칸반 + MCP 서버 + CLI. 사람과 에이전트가 한 보드를 공유 |
| 강점 | PTY 생존성·비용 breaker·파일 메시징 hops·종료 ACK·usage dedupe | 결정적 task 모델·원자 인수·MCP 계약·변경 토큰·마이그레이션 호환성 |
| 약점 | 오케스트레이션이 **LLM 프롬프트**(DAG·재시도·공정성 없음), 문서-코드 불일치 다수, fail-open 기본값 | 인증/권한 없음(LAN 신뢰), lease/만료 없음, 외부 e2e 없음 |
| 라이선스 | MIT(코드 이식 시 출처·고지), 픽셀 자산은 별도 | AGPL-3.0 — **코드 복사 금지**, 아이디어만 |
| 상세 | [r5-munder-difflin.md](r5-munder-difflin.md) | [r5-kanzo.md](r5-kanzo.md) |

---

## 1. TalkAbout에 실제로 유용한 것 — 우선순위

전제: TalkAbout은 이미 PG 트랜잭션, room 권한/epoch, plan DAG dispatch(`running/completed/partial/aborted`), claim/lease/uncertain,
SSE, human 3-ack를 갖고 있다. 아래는 **그 위에 얹을 가치가 있는 것**만 골랐다.

| 순위 | 아이디어 | 출처(근거) | TalkAbout 대상 | ROI/리스크 |
|---|---|---|---|---|
| 1 | **plan 실행 동시성 상한** — 한 run에서 동시에 실행할 노드 수 제한, idle 노드 reap | munder `config.ts:439-442`(maxConcurrentWorkers=4), `index.ts:4886-4889` | `domain.py` reconcile/큐잉, `core.py` dispatch | 높음/중간. 16개 root가 한꺼번에 provider를 깨우는 비용 폭주를 막는다 |
| 2 | **비용/폭주 breaker(순수 정책)** — healthy→steering→constrained→stopped, cost/token cap, loop/에러 폭풍 감지, hardStop 기본 off | munder `breaker.ts:1-24,42-72` | 신규 `talkabout/guard.py` + `worker.py` heartbeat | 높음/중간. 현재 방 단위 limits만 있고 실행 중 비용 가드가 없다 |
| 3 | **누적 usage dedupe** — `session_id` 키로 누적 스냅샷 중복 기록 방지 | munder `usage.ts:39-53,122-139` | `worker.py`/`store.py` 비용 이벤트 | 높음/낮음. 재시도·resume 시 이중 과금 집계 방지 |
| 4 | **MCP tool 표면 설계** — 짧은 평문 응답, 목록에서 human 전용 항목 숨기고 개수만, "다음 항목 인수"는 한 트랜잭션 | kanzo `mcp.rs:1,396-413`, `store.rs:660-681` | `talkabout/mcp.py` 확장 시 | 높음/낮음. 에이전트 토큰 절약 + 노출 최소화 |
| 5 | **crash tail ring** — 프로세스 실패 시 마지막 8KB 출력만 이벤트에 첨부 | munder `pty.ts:40,67-74` | `worker.py` 실패 이벤트, `bounds.py` 크기 캡 | 중간/낮음. 디버깅 가치 대비 구현 작음 |
| 6 | **변경 토큰** — `{counter}-{db_identity}` 같은 단일 불투명 토큰으로 UI 무효화 판정 | kanzo `web.rs:373-385`, `app.js:1152-1165` | 웹 SSE fallback/폴링, 스냅샷 신선도 | 중간/낮음. SSE와 중복되지 않게 "폴링 대체"로 한정 |
| 7 | **graceful close ACK** — 공지→ACK→완료→teardown, 타임아웃 | munder `closingTime.ts:1-51` | room close/abort 경로 | 중간/중간. 즉시 취소 기본값은 유지하고 opt-in 모드로 |
| 8 | **에이전트 문맥 export** — 계획/보드 스냅샷을 markdown/JSON으로 | kanzo `export.rs:10-120` | CLI/artifact API | 중간/낮음. 외부 에이전트가 MCP 없이 문맥 획득 |
| 9 | **마이그레이션 호환 바닥** — "구버전이 읽을 수 있는 최소 버전"을 데이터에 저장 | kanzo `store.rs:13-42,213-243` | room payload/스키마 버전 정책 | 중간/낮음. 혼합 배포·롤백 안전 |
| 10 | **hops 하네스 소유 규칙** — 서버가 카운터 소유, 클라이언트 값은 클램프, 캡 초과 시 god/드롭 | munder `hive.ts:1619-1668` | 향후 A2A 메시징 도입 시 | 낮음(현재 A2A 없음)/낮음. 설계 선반영 |

### 채택하지 말 것
- **god 프롬프트 오케스트레이션**(munder `hive.ts:1530-1613`): 의존성·재시도·공정성을 LLM 판단에 맡긴다. TalkAbout의 결정적 DAG가 우위.
- **autoMode 기본 ON / hook fail-open**(munder `config.ts:427`, `hooks.ts:205`): 보안 기본값을 가용성과 바꾸는 사례. TalkAbout은 fail-closed.
- **무인증 LAN 공유/포워딩**(kanzo `home.rs:20-24`, `web.rs:285-289`): 권한·human 경계와 정면 충돌.
- **메시지 1건 = git 커밋 1건**(munder `hive.ts:1671-1676`): PG 이벤트 원장으로 이미 감사성을 확보한 TalkAbout에 불필요.
- **git으로 DB 파일 동기화**(kanzo `sync.rs`): 바이너리 DB merge. TalkAbout은 PG가 원천.
- **평평한 `tasks.json` 원장**(munder `taskLedger.ts`): DAG·revision·의존성 모델이 있는 TalkAbout에 후퇴.

---

## 2. 두 저장소의 대비가 주는 교훈

| 질문 | munder-difflin | kanzo | TalkAbout의 위치 |
|---|---|---|---|
| 오케스트레이션을 누가 결정하나 | LLM 프롬프트(god) | 사람 + 에이전트가 카드 이동 | **결정적 DAG(서버)** — 가장 강한 쪽 |
| 실패/중단을 어떻게 다루나 | PTY 재시작·closing ACK·breaker stop | 사람이 카드를 되돌림 | lease/uncertain/abort — 더 정교 |
| 비용 폭주 방지 | breaker(토큰 velocity·cap) | 없음(보드 크기 무관) | **미구현** — munder에서 가져올 것 |
| 사람의 통제 지점 | 도구 권한 프롬프트, god 에스컬레이션 | human 카드, 보드 직접 조작 | 3-ack+abort+private plan — 더 엄격 |
| 에이전트 문맥 전달 | memory.md + inbox + 프롬프트 | MCP tool 응답·export | plan_node + 이벤트 — 노출 최소화 우위 |
| 배포/운영 | Electron 앱 | 단일 바이너리 | FastAPI+PG 서버 |

**핵심**: 두 저장소 모두 "에이전트가 실제로 일하는 제품"이지만, **결정성**(kanzo의 task 보드, TalkAbout의 DAG)과
**가드레일**(munder의 breaker·hops·closing)은 서로 다른 축이다. TalkAbout은 전자에서 이미 앞서 있으므로 **후자만 보강**하면 된다.

---

## 3. 구체적 다음 단계 (권장)

1. **`domain.py` run 동시 실행 상한** — `run`에 `max_parallel`(기본 4) 개념을 추가하고, ready 노드 큐잉을 상한까지만.
   기존 의존성 진행·abort·capacity 테스트에 회귀 케이스 추가. (munder `maxConcurrentWorkers` 사상)
2. **`guard.py` breaker 프로토타입** — 순수 함수(입력: usage diff, 반복 tool/에러, 진척)로 먼저 단위 테스트하고,
   이후 `worker.py` heartbeat와 연결해 "steer 이벤트→제한→정지" 순으로 집행. (munder `breaker.ts`)
3. **MCP/export 소도구** — `mcp.py`에 계획/보드 요약 tool(짧은 평문, human 전용 항목 숨김)과
   `export` 스냅샷(계획+노드 상태+댓글)을 추가. (kanzo `mcp.rs`, `export.rs`)

각 단계는 TalkAbout의 기존 테스트를 건드리지 않고 신규 테스트로 검증 가능하다. 1번은 DAG 엔진 변경이라 설계 검토가 필요하고,
2·3번은 격리된 추가 모듈이라 위험이 낮다.

---

## 4. 검증 수준과 한계

- **munder-difflin**: t3code-analyst가 304줄 분석(§0~§7, `path:line` 인용). Lead는 `hive.ts` 메시징/breaker/`tasks.json`/`cache` 관련
  핵심 구간을 직접 대조했다. 빌드·실행·124개 테스트 실행은 하지 않았다(지시대로).
- **kanzo**: handy-analyst의 턴이 파일 저장 전에 두 번 종료되어, **Lead가 직접** `src/` 핵심 파일을 읽고 [r5-kanzo.md](r5-kanzo.md)를 작성했다.
  `app.js`/`index.html`/`update.rs`/`service.rs` 후반은 부분 열람이다.
- 두 문서 모두 **정적 분석**이며, README 성능 수치·실제 동작·보안 침투 검증은 하지 않았다.
- 라이선스: munder 코드는 MIT(출처·고지 필요), kanzo 코드는 AGPL-3.0(복사 금지). 이 문서는 아이디어·계약 수준 참고다.
