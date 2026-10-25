# hermes-agent — ACP 어댑터 · provider 계층 정밀 분석 (R2)

- 대상: `hermes-agent/` (HEAD `930e5ed`, MIT — `hermes-agent/LICENSE:1-3` "MIT License / Copyright (c) 2025 Nous Research")
- 기준 프로젝트: `talkabout/` (Python + PostgreSQL, ACP **클라이언트** 쪽이 Grok에서 막혀 있음 — `docs/STATUS.md:59,66`)
- 범위: `acp_adapter/` 20파일 전체(단, `server.py`·`gateway_server.py`는 프레임·세션·에러·id 부분만), `providers/`, `agent/copilot_acp_client.py`, `agent/acp_openai_bridge.py`, `agent/account_usage.py`, `hermes_cli/auth.py`, `hermes_cli/gateway_client.py`, `hermes_cli/gateway_replay_gap.py`, `toolsets.py`, `toolset_distributions.py`, `tools/registry.py`(check_fn), `pyproject.toml`.
- 규칙 준수: 모든 판정에 상대경로+라인. 확인하지 못한 것은 "미확인", grep으로 못 찾은 것은 "찾지 못했다"로 한정. 라이선스는 §12.

---

## 0. 한 장 요약 — 판정표

| # | 발견 | 태그 | 비용 | TalkAbout 부착 위치 | 최적 저장소 |
|---|---|---|---|---|---|
| 1 | ACP 응답 id 불일치를 **fatal로 올리지 않고 skip+유한 타임아웃**하는 ACP 클라이언트 실물(`agent/copilot_acp_client.py:413-441`) | `[채택]` | S | `talkabout/agent_runtime.py:683-705`, `:992-1004` | **hermes** |
| 2 | id→Future 맵 + **끊김 시 전 pending 정산** + verb별 timeout 오버라이드(`hermes_cli/gateway_client.py:182-223`) | `[채택]` | M | `talkabout/agent_runtime.py` `_JSONLTransport`(149-353) | **hermes** |
| 3 | 실패 프레임을 **bounded 구조 서술자**로만 남기는 규율(16키/200자, `gateway_client.py:67-94`) + wire observer 훅(`acp_adapter/gateway_server.py:124-136`) | `[채택]` | S | `talkabout/agent_runtime.py:329-353` `diagnostics()` | **hermes** |
| 4 | 3중 id(전송 id / `input_id` uuid / `admission_id`) + 콘텐츠 fingerprint 재시도(`acp_adapter/gateway_server.py:746-770`, `:706-726`) | `[차용]` | M | `talkabout/worker.py`(journal), `talkabout/agent_runtime.py:1007-1030` | **hermes** |
| 5 | replay gap 3-상태 정산(`pending_status`/`settled`/`owed_reply`) — 구멍은 잇지 않고 `[output resumed]`로 표시 | `[채택]` | S | `talkabout/worker.py` uncertain 경로 | **hermes** |
| 6 | 승인 timeout은 **거부가 아닌 제3상태**, 옵션 id 불일치는 **fail-closed deny**(`acp_adapter/permissions.py:67-74,117-152`) | `[채택]` | S | `talkabout/tool_broker.py:45-62, 157-230` | **hermes** |
| 7 | 승인 전 **diff 프리뷰 필수화 + 민감경로 자동승인 금지 + 정책은 3단(ask/workspace/session)**(`acp_adapter/edit_approval.py:144-172,177-198`) | `[채택]` | M | `talkabout/tool_broker.py` (`ApprovalRequest`에 `preview` 추가) | **hermes** |
| 8 | capability는 **raw 프레임에서 판정**하고, 없으면 폴백 UI(`acp_adapter/elicitation.py:17-53`) — SDK 타입 모델이 필드를 버리는 사례 포함 | `[채택]` | S | `talkabout/agent_runtime.py:952-962`(initialize 협상) | **hermes** |
| 9 | `auth_type` enum + `external_process`(ACP를 provider로 선언) + **terminal-refresh code** 분류(`hermes_cli/auth.py:150,186-188,1986-1988`) | `[차용]` | M | `talkabout/providers.py:19-42`, `agent_runtime.py:433-445` | **hermes** |
| 10 | auth 증거는 **3-상태**(검증됨 / 검증불가≠로그아웃 / 없음) + `identity`(JWT principal, 비밀 아님)(`hermes_cli/auth.py:2079-2089`, `agent/account_usage.py:709-712`) | `[채택]` | S | `talkabout/identity.py`(runtime_check), `agent_runtime.py:397-399` | **hermes** |
| 11 | 도구 게이팅: `TOOLSETS` dict + `check_fn` TTL/플레이크 유예(`toolsets.py:11-76`, `tools/registry.py:249-267`) | `[차용]` | M | `talkabout/tool_broker.py`(policy profile), S4 | **hermes** |
| 12 | ACP 프레임 자체의 수기 구현(라인 정산·유한 타임아웃 규율)은 hermes가 **SDK 의존**(`pyproject.toml:425`)이라 참조 가치 낮음 | `[대조]` | — | — | orca |

**핵심 명제 판정(요약)**: hermes의 `acp_adapter/`는 ACP 서버가 맞다. 그러나 hermes는 **또 하나의 ACP 클라이언트**(`agent/copilot_acp_client.py`)와 **JSON-RPC over WebSocket 클라이언트**(`hermes_cli/gateway_client.py`)를 함께 갖고 있고, 이 둘이 TalkAbout의 Grok ACP 문제에 **직접 쓸 수 있는 유일한 실물 참조**다. 단 **Grok 고유 원인 진단은 hermes에 없다**(`acp_adapter/`에 `grok|xai` 0건). 상세 판정은 §4.

---

## 1. 대상 실측 확인

| 항목 | 값 | 근거 |
|---|---|---|
| HEAD | `930e5ed` "fix(agent): withhold the reconnect notice too for a wedged inline call" | `git log -1` |
| 라이선스 | MIT, Nous Research | `hermes-agent/LICENSE:1-3` |
| ACP 프레임 구현 | **자체 구현 아님**. `agent-client-protocol==0.9.0` 의존 | `pyproject.toml:425` `acp = ["agent-client-protocol==0.9.0"]` |
| ACP 실행 진입점 | `hermes-acp = "acp_adapter.entry:main"` | `pyproject.toml:571` |
| `acp_adapter/` 파일 | `server.py` 58,486B / `gateway_server.py` 70,364B / `tools.py` 42,778B / `session.py` 29,608B / `model_catalog.py` 15,430B / `commands.py` 15,449B / `gateway_commands.py` 15,044B / `events.py` 13,115B / `content.py` 11,091B / `edit_approval.py` 10,183B / `entry.py` 8,759B / `gateway_reply.py` 8,378B / `permissions.py` 7,502B / `gateway_session_meta.py` 6,642B / `gateway_media.py` 5,183B / `provenance.py` 3,874B / `catalog.py` 3,550B / `elicitation.py` 3,502B / `auth.py` 2,209B / `modes.py` 983B | 실측 |
| `providers/` | `base.py` 21,489B / `__init__.py` 30,833B / `README.md` 3,679B | 실측 |
| ACP 클라이언트 실물 | `agent/copilot_acp_client.py` 26,613B (수기 stdio JSON-RPC 클라이언트) | 실측 |
| RPC 클라이언트 실물 | `hermes_cli/gateway_client.py` 322줄 (JSON-RPC over WebSocket) | 실측 |

> **중요**: `acp_adapter/` 어디에도 `grok`/`xai` 문자열이 없다(전 파일 grep 0건). 즉 hermes는 **Grok CLI를 ACP로 구동하지 않는다**. hermes에서 "grok"은 `xai` HTTP provider의 alias이고(`plugins/model-providers/xai/__init__.py:7-13` `aliases=("grok","x-ai","x.ai")`, `auth_type="api_key"`), OAuth는 `xai-oauth`(`oauth_external`) 행으로만 존재한다(`hermes_cli/auth.py:179-181`). 따라서 hermes는 Grok ACP 원인 규명의 증거가 될 수 없다 — **규율의 증거**일 뿐이다.

---

## 2. 트랙 명제 검증: "hermes는 서버, TalkAbout은 클라이언트"

맞다. 다만 **양방향**이다. hermes 안에는 ACP 관련 주체가 셋 있다.

| 주체 | 방향 | 파일 | 쓰임 |
|---|---|---|---|
| `HermesACPAgent` | 서버(에이전트) | `acp_adapter/server.py:234` (`class HermesACPAgent(SlashCommandsMixin, acp.Agent)`) | 에디터(Zed/Codex/Claude Code/OpenCode)가 hermes를 ACP agent로 호출 |
| `GatewayACPAgent` | 서버(에이전트) | `acp_adapter/gateway_server.py:67` | 같은 ACP 표면을 **게이트웨이 RPC 클라이언트**로 구현 — 즉 "ACP 서버가 다른 RPC의 클라이언트" |
| `CopilotACPClient` | **클라이언트** | `agent/copilot_acp_client.py:280` | hermes가 `copilot --acp`를 **ACP 서버로 spawn**해서 LLM 백엔드로 사용. 구조가 TalkAbout의 Grok 경로와 동일(서브프로세스 + stdio JSON-RPC) |
| `GatewayClient` | **클라이언트** | `hermes_cli/gateway_client.py:145` | JSON-RPC over WS. id→Future 라우팅·끊김 정산 |

`HermesACPAgent.__new__`는 session_manager가 없으면 GatewayACPAgent로 분기한다(`acp_adapter/server.py:245-249`). 즉 **같은 ACP 표면의 두 구현**(in-process / gateway)이 공존하며, 후자가 재연결·손실·id 재정합을 전부 다룬다 — TalkAbout이 필요로 하는 부분이다.

---

## 3. ACP 서버 구현 구조 (질문 1)

### 3.1 프레임·프로토콜 계층은 SDK 위임

`server.py`에는 JSON-RPC 파싱/직렬화 코드가 없다. 전부 `acp.*` 타입(`InitializeResponse`, `PromptResponse`, `RequestError`)을 쓴다(`acp_adapter/server.py:527-550, 1032-1046`). stdio는 stdout을 ACP 전용으로 비우고 로그를 stderr로 돌린다(`acp_adapter/entry.py:65-79` `_setup_logging()`, 주석 "stdout stays clean for ACP stdio").

**판정**: ACP **프레임 규율**(숫자 id만, 초과 라인 개별 정산, 불일치 id 무시)을 hermes에서 배울 수는 없다. 그건 SDK 내부이며 저장소에 벤더링되어 있지 않다(벤더 디렉터리 grep 결과 `acp` 패키지 없음). 이 항목은 1라운드 Orca 결론이 여전히 최적이다(브리프 §3과 모순 없음). hermes가 주는 것은 **그 위의 애플리케이션 계층 규율**이다.

### 3.2 세션 수명 — 메서드 계약표

| ACP 메서드 | hermes 구현 | 관찰되는 규율 |
|---|---|---|
| `initialize` | `server.py:527-550` | 요청된 버전을 echo하지 않고 `protocol_version=acp.PROTOCOL_VERSION`을 돌려준다(:540). capability는 `load_session=True`, `PromptCapabilities(image=True)`, `session_capabilities(fork/list/resume)`(:542-547) |
| `authenticate` | `server.py:552-565` | **initialize에서 광고한 method_id만 ack**(:553 주석, :563 `if not provider or normalized_method != provider: return None`). 그 외는 None |
| `session/new` | `server.py:615-620` | 에이전트 생성(느림·블로킹)을 `asyncio.to_thread`로 오프로드(:617-618) — "인라인으로 두면 모든 JSON-RPC를 서빙하는 루프가 얼었다(#58083)" |
| `session/load` | `server.py:622-630` | 없으면 `None` 반환(에러 아님). **히스토리 replay를 응답 전에 await**(:585-595, 주석: `call_soon`으로 미뤘다가 모든 스펙 준수 클라이언트가 깨졌다, #12285) |
| `session/resume` | `server.py:632-640` | 없으면 **새 세션 생성** 후 resume 응답(:637-638) |
| `session/cancel` | `server.py:642-663` | `cancel_event.set()` + `request_hard_interrupt` + 서브에이전트 `interrupt_for_session`(:653-660). **응답 본문 없음**(None) — 취소의 확정은 prompt 응답의 `stop_reason`으로만 |
| `session/fork` | `server.py:665-677` | 실패 시 `session_id=""`(:671) |
| `session/list` | `server.py:679-703` | cursor = 마지막 `session_id`. **모르는 cursor → 빈 페이지, 전체 목록 아님**(:682-692, for-else) |
| `session/prompt` | `server.py:817-882` | §3.3 |
| `session/set_model` | `server.py:1021-1057` | 진행 중이면 `-32603 "Session is busy"`(:1032), 서빙 불가 modelId는 **`-32602 invalid_params`**(:1041-1046, 주석 "클라이언트가 요청 탓으로 귀속하게 하려고") |
| `session/set_mode` / `set_config_option` | `server.py:1059-1092` | 모르는 mode는 조용히 기본값(:1065-1067). config는 저장만 하고 `config_options=[]` 반환(:1092) |

### 3.3 prompt 턴 — TalkAbout 클라이언트가 기대해도 되는 계약

```
state 없음        → PromptResponse(stop_reason="refusal")            # server.py:820-822
빈 prompt         → PromptResponse(stop_reason="end_turn")           # :827-828
슬래시 명령       → 텍스트 session/update 후 end_turn                # :833-843
이미 턴 진행 중   → redirect 또는 queue, 즉시 end_turn + 안내 텍스트 # :731-751, :845-849
정상 완료         → end_turn (+ usage)                               # :1005
취소됨            → stop_reason="cancelled"                          # :1005
```

핵심은 **동시 prompt가 에러가 아니라 흡수**된다는 점이다. `_claim_turn_or_queue`는 `state.runtime_lock` 안에서 `is_running`을 보고, 진행 중이면 (a) 텍스트 전용 + 런타임이 `redirect`를 지원하면 살아있는 턴에 교정을 주입하고, (b) 아니면 `queued_prompts`에 넣고 `"Queued for the next turn. (N queued)"`를 돌려준다(`server.py:736-751`). 클라이언트는 `end_turn`을 받지만 **그 턴의 답이 아직 생성되지 않았다**는 것을 텍스트 업데이트로 안다.

`_finish_turn`은 종료 시 반드시 (1) 열린 tool call을 닫고(`:884-893` `flush_open_tool_calls`), (2) `is_running=False`로 만든 뒤 큐를 드레인한다(`:990-995`). 취소 상태는 `state.cancel_event`로 조회해 `cancelled`로 매핑한다(`:971,1005`).

### 3.4 진행 이벤트 · 메시지 id

- 스트리밍 델타는 `messageId`로 묶인다. **턴마다 새 id를 강제**한다: `state.message_ids.close()` 후 델타가 새 id를 연다(`server.py:912-916`, 주석 "a new turn must never reuse a previous turn's assistant messageId (ACP clients replace the bubble with that id)").
- `AssistantMessageIdAllocator`의 docstring이 클라이언트측 함정을 명시한다: id가 없으면 "a client that replaces 'the current assistant message' on each chunk collapses separate autonomous turns into one bubble"(`acp_adapter/events.py:186-199`). **id는 UUID4**(ACP 스키마 요구, 이전 턴 id와 충돌 불가, :196-198).
- tool call id는 **이름별 FIFO deque**로 관리해 동명 병렬 호출이 올바른 콜에 닫히게 한다(`events.py:70-94`). 턴 종료 시 남은 콜은 `abandoned`로 닫는다(`events.py:97-116`, 주석: 승인 프롬프트에 막힌 콜은 `tool.completed`를 못 내보내 bubble이 영원히 도는 것처럼 보인다).
- 업데이트 전송은 **예외를 삼키고 로그만** 남긴다(`server.py:263-270`). 전송 실패가 턴을 죽이지 않는다.

---

### 3.5 에러 매핑 규율 — "누구 탓인가"를 코드에서 정한다

hermes는 JSON-RPC 코드를 **귀속(attribution)** 관점에서 고른다. 이건 TalkAbout이 실패를 기록할 때 그대로 쓸 수 있는 규율이다.

| 상황 | 코드 | 근거 | 이유(주석 원문 요지) |
|---|---|---|---|
| 모델 전환 거부(제공 불가 modelId) | `-32602 invalid_params` | `server.py:1040-1046` | "the client attributes it to the request, not to Hermes (#72439)" — 잘못된 파라미터는 요청 탓 |
| 모델 재구성 자체 실패(비활성 provider 등) | `-32603 internal` | `server.py:1041-1044` | 재구성 실패는 서버 내부 탓 |
| 세션 busy(턴 진행 중 모델 전환) | `-32603` | `server.py:1030-1032` | 상태 충돌은 요청 파라미터 문제가 아님 |
| 모르는 config/모드 값 | 조용히 기본값 | `server.py:1065-1067`, `gateway_server.py:569-570, 580-582` | 클라이언트 자체 설정 쓰기로 에디터가 죽지 않게 |
| 프로브 메서드(`ping`/`health`) | `-32601` **정상 응답** | `entry.py:38-61` | "clients treat that as agent alive" — 로그 노이즈만 억제 |
| 텍스트 전용 클라이언트에 도구 요청 | 거부 응답 후 런 종료 | TalkAbout `agent_runtime.py:657-671` | `[대조]` TalkAbout이 이미 더 엄격 |

특히 눈여겨볼 것은 `_BenignProbeMethodFilter`(`entry.py:45-61`)다. `-32601`을 **응답은 유지하되 traceback 로그만 억제**한다. 필터는 `RequestError.code == -32601`이고 `data.method`가 프로브 집합에 속할 때만 통과시킨다(`:55-62`) — 같은 코드라도 **method 이름으로 구분**한다. TalkAbout의 `_STDERR_HINTS` 허용목록(`agent_runtime.py:35-41`)과 같은 사고(비신뢰 문자열을 고정 라벨로만 축약)이며, 상호 보완적이다.

또 하나: hermes는 `.get("message")` 같은 자유 문자열을 **경계에서 자른다**. `rpc_error()`는 reason을 `_bounded_reason`(영숫자/밑줄 80자)으로 제한하고, 실패하면 `data.reason`으로, 그것도 없으면 `request_failed`로 폴백한다(`gateway_client.py:63-65, 85-94`). 원격 진단문이 그대로 사용자에게/로그에 흐르지 않는다.

### 3.6 ACP에는 `tools` 채널이 없다 — `acp_openai_bridge.py`의 구조적 통찰

`agent/acp_openai_bridge.py`는 "ACP has no OpenAI-style `tools`/`tool_calls` channel"이라고 시작한다(`:1-9`). 그래서 hermes는 도구 스키마를 **프롬프트 텍스트로 밀어 넣고**(`render_tool_bridge_sections`, `:110-122`), 응답 텍스트에서 `<tool_call>{...}</tool_call>` 블록을 **정규식으로 다시 뽑아낸다**(`:20-32` 계약 문자열 + `:142` `extract_tool_calls_from_text`). 프롬프트 preamble이 모델에게 그 형식을 강제한다(`copilot_acp_client.py:48-53` "you MUST output tool calls using `<tool_call>{...}</tool_call>` blocks").

이것은 TalkAbout에 **구조적 확인**을 준다: ACP 에이전트를 LLM 백엔드로 쓰는 한 도구 호출은 프로토콜이 아니라 **텍스트 파싱**으로 처리된다. 따라서 TalkAbout의 "text-only 프로파일"(`agent_runtime.py:810-812` codex `sandbox=readOnly`, grok은 `--tools`/`--deny` 플래그 요구 `:519-521`)은 **프로토콜 수준에서 이미 올바른 선택**이며, S4를 ACP 경로로 확장할 때 hermes식 텍스트 브리지를 채택하면 **모델이 도구를 흉내 낼 수 있는 우회로**가 생긴다. 판정 `[제외]` — TalkAbout은 툴을 ACP 텍스트가 아니라 `tool_broker`로만 다뤄야 한다(wire 상 도구 채널이 없다는 사실을 근거로 든다).

---

## 4. ★ 핵심 판정: Grok ACP 응답 ID 불일치 (필수 질문)

### 4.1 TalkAbout 현재 코드 실측 — 어디서 죽는가

| 위치 | 동작 | 문제 |
|---|---|---|
| `talkabout/agent_runtime.py:675-676` | grok이면 `event.get('jsonrpc') != '2.0'`인 **모든 프레임**을 `runtime_invalid_output`으로 거부 | `jsonrpc` 없는 벤더 알림/래퍼 한 줄이 런을 끝낸다 |
| `:677-678` | `id`와 `method`가 동시에 있으면 `_deny_request` → `_send(reply)` 후 `runtime_tool_policy_violation` raise | 서버→클라이언트 요청은 **거부 응답을 보낸 뒤 런을 종료** |
| `:687-689` | `id`가 있으면 `event['id'] != request_id` → `runtime_protocol_mismatch` **fatal** | **id 하나만 달라도 즉시 종료. 프레임은 버려진다** |
| `:996-998` | `session/prompt` 응답도 동일하게 id 비교 후 fatal | 같은 규율 |
| `:302-313` | `wait_success`에서 events에 남은 프레임이 있으면 `runtime_protocol_mismatch` | 종료 후 잔여 프레임도 fatal |
| `:329-353` | `diagnostics()`는 **개수/바이트만** 남기고 원문은 남기지 않음(:330-331 "never publishes raw provider output") | STATUS:59의 "원본 프레임을 확보하지 못해"가 코드로 확인된다 |

요청 id는 프로세스당 단조 증가 int다(`:417`, `:651-655`). **응답 id→요청 매핑 자료구조가 없다** — 오직 "지금 기다리는 id 하나"와 `self._pending` 리스트(알림 보관, `:703`)뿐이다. 즉 **순서 기반 프로토콜 가정**이다.

코드상 mismatch로 귀결되는 경로는 3가지로 읽힌다(전부 위 라인에서 확인 가능, Grok이 실제로 어느 경로였는지는 **미확인**):

1. **id 타입 불일치**(int `1` vs str `"1"`) — `:688`은 타입 강제 없이 `!=` 비교.
2. **`id`를 가진 알림/벤더 프레임** — `:687`이 method 유무를 보지 않고 `id`만으로 응답으로 간주.
3. **`jsonrpc` 멤버 누락** — `:675`에서 응답·알림 구분 없이 거부.

### 4.2 hermes는 같은 자리에서 무엇을 하는가 — 3개 실물

**(A) ACP 클라이언트(`CopilotACPClient`) — fatal이 아니라 skip**

```
msg = inbox.get(timeout=0.1)
if self._handle_server_message(msg, ...) or msg.get("id") != request_id:
    continue                      # copilot_acp_client.py:424-428
if "error" in msg: raise ...      # :429-431
return msg.get("result")          # :432
```

- 서버→클라이언트 메시지(알림/요청)는 **먼저 소비**하고(`:477-510`), 남은 것 중 id가 다르면 **조용히 버리고 계속 읽는다**(`:427`).
- 루프는 `deadline = session_deadline`까지 돈다(`:418-419`). 즉 id가 영원히 안 맞으면 **TimeoutError**로 끝나고, 그때 메서드 이름이 메시지에 들어간다(`:441`).
- 프로세스 조기 종료는 **stderr tail을 1초 더 기다려** 모은 뒤 판정한다(`:433-440`, 주석: 너무 일찍 읽으면 죽은 CLI가 Timeout으로 보여 재시도 정책이 달라진다). stderr는 `deque(maxlen=40)`(`:392`), 디코드 실패 라인은 `{"raw": line}`로 흘려보낸다(`:394-398`) — **비JSON 한 줄이 리더를 죽이지 않는다**.
- id 생성: `iter(range(1, 1 << 62))`(`:408`).
- **세션 전체 1예산**(initialize + session/new + prompt), 요청별 아님(`:409-411`) — "멈춘 CLI가 호출자 타임아웃의 2배를 받으면 안 된다".
- `initialize` capability는 `{"fs": {"readTextFile": True, "writeTextFile": True}}`(`:54-58`)이고, fs 요청은 **cwd 내부로 경로를 가둔다**(`:229-241` `_ensure_path_within_cwd`, NT-namespace 선검사 포함). 쓰기 승인이 필요한 경로는 ACP 브리지에서 **fail closed**(`:270-271`). 권한 요청은 `{"outcome":{"outcome":"cancelled"}}`로 거부(`:496-497`). 모르는 메서드는 `-32601`(`:506-507`).

→ TalkAbout의 `_grok`과 **같은 형태**(서브프로세스·stdio·initialize→session new/load→prompt)이며, 다른 점은 정확히 "불일치 id를 fatal로 만들지 않는다" 하나다.

**(B) JSON-RPC 클라이언트(`GatewayClient`) — id→Future + 끊김 정산**

```
future = self.pending.get(frame.get("id"))          # gateway_client.py:187
if future is not None and not future.done():
    ...set_result / set_exception(rpc_error(...))    # :188-192
elif "method" in frame:
    await self._deliver(frame, len(raw))             # :193-194
# 프레임은 조용히 무시된다(모르는 id + method 없음)
```

- 응답은 **id로 라우팅**되고 순서 무관하게 도착해도 맞는다(`:187-192`). 리더 태스크(`_read`)와 소비자(이벤트 큐)가 분리되어 느린 소비자가 응답을 막지 않는다(`:173-207`).
- **끊김 시 모든 pending을 정산**한다(`:197-207`): `GatewayClientError("Gateway disconnected; turn outcome is unknown. ... do not resend the work.")`. 그리고 그 에러를 **이벤트 큐에도 꽂아** 소비자가 조용히 이벤트를 잃지 않게 한다(`:203-207`).
- `rpc()`는 요청별 `_timeout` 오버라이드, `math.inf`면 무기한(`:209-223`). 기본 30초(`:18`), 압축은 660초(`:22-27`).
- 큐 포화 시 **parking + overflow deque + stall 실패**(`:106-134, :153-171`, `EVENT_STALL_TIMEOUT=30`, 상한 16,384프레임/8MB `:34-35`).

**(C) 서버측 재정합(`GatewayACPAgent`) — 전송 계층이 아니라 턴 계층의 id 규율**

- `prompt.submit`에 `input_id = uuid.uuid4().hex`를 붙이고(`gateway_server.py:770`), `_unacked[session_id] = (retry_key, input_id, staged)`로 보관한다(`:658`). 에디터가 **같은 내용으로 재시도**하면 `submit_fingerprint(session_id, content)`가 같을 때 `input_id`와 staged를 **재사용**한다(`:746-759`, 상한 64세션 `:658-660`).
- ack receipt가 `status="terminal"`이면 그 턴 결과를 **한 번만** 보고하고(`:719-721`), `status="unknown"`이면 **두 번째 턴을 절대 만들지 않고** 명시적 거부로 간다(`:722-726`).
- 완료 대기는 `admission_id` 단위 조건변수 + **연결 세대 카운터**로 한다. `self._link != link`면 "우리 transport가 죽었다"로 ambiguous 처리하고 **새 연결에서 재시도하지 않는다**(`:674-687`).
- 결과 상태는 `cancelled / failed / unknown` 3분기이고, `unknown`은 `_unknown_recovery(...)`로 **회복 안내를 담은 거부**를 낸다(`:695-700`).
- payload 캐리어는 `copy_context()`로 격리된 executor 스레드에서 실행된다(`server.py:873`).

### 4.3 판정

| 질문 | 판정 | 근거 |
|---|---|---|
| Grok ACP 응답 ID 불일치의 **원인**이 hermes에 있는가 | **아니오 — 찾지 못했다** | `acp_adapter/`에 `grok|xai` 0건. Grok ACP 클라이언트 코드 없음 |
| 불일치 id를 **어떻게 다뤄야 하는지**가 hermes에 있는가 | **예 — 부분적이고 충분히 실증적** | ACP 클라이언트가 skip+deadline(`copilot_acp_client.py:413-441`), RPC 클라이언트가 id→Future+정산(`gateway_client.py:182-223`) |
| TalkAbout의 현재 실패 방식(즉시 fatal, 증거 소실)에 대한 대안이 있는가 | **예** | bounded 에러 데이터(`gateway_client.py:63-94`) + wire observer(`gateway_server.py:124-136`) + `_BenignProbeMethodFilter`(`entry.py:45-61`) |
| 그대로 이식하면 Grok이 통과하는가 | **미확인** — 단정 금지 | 원인 프레임이 없으므로 어떤 정규화가 필요한지 알 수 없다 |

**권고 순서(추측 최소화)**:

1. **증거 먼저**(S): `_receive`에서 실패 직전 프레임의 **bounded 구조 서술자**를 에러에 실어 보낸다 — `{jsonrpc 유무, id의 타입과 값(짧게), method 유무, 프레임 바이트}`. hermes의 `_bounded_error_data`(16키/값 200자, `gateway_client.py:67-82`)가 그대로 참고된다. TalkAbout의 redaction 정책(:330-331)과 충돌하지 않는다 — 원문이 아니라 **구조**만 남긴다.
2. **규율 수정**(S): `_rpc`/`_grok`의 `if event['id'] != request_id: raise`를 **skip + `self._pending` 보관 + 남은 deadline 동안 계속 읽기**로 바꾼다(`copilot_acp_client.py:427`). 이러면 불일치가 "런 실패"가 아니라 "그 응답을 못 받음"으로 분리되고, timeout이 원인 미상의 실패를 흡수한다.
3. **id 타입 정규화**(S): int/str 동등 비교는 **1의 증거가 확보된 뒤에만** 허용. STATUS:59이 "임의로 허용하지 않았다"고 한 결정은 옳다 — hermes도 타입을 손대지 않고 skip할 뿐이다(`copilot_acp_client.py:427`은 `!=` 비교).
4. **구조 수정**(M): `_pending` 리스트를 `dict[request_id, Future]`로 바꾸고, EOF/프로세스 종료 시 **미해결 전부를 `runtime_outcome_unknown`으로 정산**한다(`gateway_client.py:197-207`의 이식). TalkAbout은 이미 EOF를 `runtime_outcome_unknown`으로 처리하므로(`agent_runtime.py:291-300`) **정산만 추가**하면 된다.

**최적 저장소**: **hermes**(클라이언트 규율). Grok **원인 규명**은 opencodex의 프록시 트랙이 최적이어야 한다 — 이 판정은 opencodex 담당 분석과 모순되지 않으며, 여기서는 "hermes로 원인을 특정할 수 없다"만 단정한다.

---

## 5. ★ 반복 턴 · 기본 20초 lease 판정

### 5.1 hermes가 답하는 것

| 메커니즘 | 근거 | TalkAbout에 주는 의미 |
|---|---|---|
| 한 세션 = 한 턴. 동시 prompt는 queue/redirect로 흡수하고 `end_turn` + 안내 텍스트 | `server.py:731-751, :845-849` | 반복 턴에서 2번째 prompt를 보내면 서버가 **에러 대신 큐**로 답할 수 있다. 클라이언트는 `end_turn`을 "내 발화 완료"로 해석하면 안 된다 |
| 재연결 후 재정합은 snapshot + pending watermark + replay epoch | `gateway_server.py:177-214, :231-239` | 재연결에서 턴 결과를 추정하지 않고 **권위 스냅샷**으로 맞춘다 |
| gap은 잇지 않는다: `pending_status`(queued/started/None), `settled` True/False/**None(판독불가)**, `owed_reply`가 prefix면 이어붙이고 아니면 `[output resumed]` 마커 | `hermes_cli/gateway_replay_gap.py:11-31` | "응답을 못 받은 구간"을 조용히 스플라이스하지 않고 **표시**한다. TalkAbout의 `uncertain`과 같은 철학이며 더 명시적 |
| 취소 확정은 `stop_reason` | `server.py:642-663, :971-1005` | `session/cancel` 전송은 취소의 증거가 아니다 — TalkAbout도 같은 결론(`agent_runtime.py:1047` 주석) |
| 턴마다 새 `messageId` | `server.py:912-916`, `events.py:185-217` | 반복 턴에서 **턴 경계**를 클라이언트가 id로 구분해야 한다 |

### 5.2 "20초 lease"에 대한 판정

hermes에 20초라는 값은 없다. hermes가 주는 답은 **시간 예산을 용도별로 분리하라**는 것이다: 짧은 RPC 30초(`gateway_client.py:18`), 긴 압축 660초(`:22-27`), ACP 세션 전체 900초(`copilot_acp_client.py:39`), 승인 대기 300초(`permissions.py:117-125`).

TalkAbout 자체 근거로도 20초는 부족하다:

- `docs/STATUS.md:57` — Claude 실제 콜드 스타트 **36.6초**(그래서 연결 시험 상한을 30초→90초로 올렸다).
- `talkabout/agent_runtime.py:490-493` — "a cold CLI start can exceed 10s ... (measured: Grok 1.0.70 help took >10s)".

즉 Grok ACP 한 턴은 **process spawn + version/help probe + initialize + authenticate + session/new + 모델 콜드 스타트**가 모두 lease 안에 들어와야 한다. 판정: `[차용]` **lease 예산을 (spawn/preflight) / (RPC 준비) / (모델 턴) 3구간으로 나누고, 구간별 상한을 따로** 두는 것(hermes의 verb별 `_timeout` 패턴, `gateway_client.py:209-223`). 20초 단일 lease는 실측 근거에 비추어 구조적으로 빠듯하다 — hermes가 이 값을 제시하지는 않으므로 근거는 TalkAbout 자신의 측정이다.

---

## 6. 권한 요청·승인 (질문 2) — `permissions.py` / `edit_approval.py`

### 6.1 스키마와 정책

```
_OPTION_ID_TO_HERMES = {"allow_once":"once","allow_session":"session",
                        "allow_always":"always","deny":"deny","deny_always":"deny"}   # permissions.py:17-20

def _map_outcome_to_hermes(outcome, *, allowed_option_ids):                       # :67
    if not isinstance(outcome, AllowedOutcome): return "deny"                     # :69-70
    if outcome.option_id not in allowed_option_ids:                               # :71-73
        logger.warning(...); return "deny"                                        #   fail-closed
    return _OPTION_ID_TO_HERMES.get(outcome.option_id, "deny")
```

- **모르는 option_id는 deny + 경고 로그**(fail-closed, `permissions.py:67-74`). id 검증이 승인 결정의 핵심이다.
- 옵션 집합은 정책에 따라 달라지고 id는 **고정**이다("Ids are stable across the `allow_permanent=True`/`False` paths even though the option list differs", `:16-22`). 매번 다시 묻는 게이트는 `allow_once`+`deny`로 축소(`:38-53`, `#81887`).
- **timeout은 거부가 아닌 제3상태**: `resolve_permission_timeout`이 `approvals.timeout`(기본 300초)을 요청마다 읽고(`:117-125`), 콜백은 `"timeout"`을 반환해 `tools.approval`이 "사용자 응답 없이 타임아웃"으로 구분 보고한다(`:145-148`). 주석: "ACP 브리지가 60초를 하드코딩해서 승인 카드가 아직 대기 중인 호스트에서 hermes가 스스로 거부했다(#73403)".
- 응답 후 **bubble 종료 상태를 결정과 일치**시킨다: allow→`completed`, 그 외→`failed`(`:108-113`). 클라이언트에 pending 카드가 남지 않는다.
- 도구 콜 id는 `perm-check-N` 카운터로 동시 요청을 분리(`:54-65`). 대기 자체는 `future.result(timeout=...)`로 하고 초과 시 `future.cancel()` + `timed_out=True`(`:77-114`).

`edit_approval.py`는 ACP 런에만 바인딩되는 **ContextVar 요청자**(`:41`, docstring "Intentionally isolated from the generic tool registry: ... CLI, gateway, and other sessions leave it unset")로 동작한다.

- 승인 대상은 **적용 전 diff**: `EditProposal`(tool_name, path, old_text, new_text, arguments, paths)를 만들고(`:24-36`), `write_file`/`patch(replace)`/`patch(patch)` 3종만 지원(`:131-137`).
- V4A 다중 파일 패치는 `paths` 튜플로 **실제 대상 집합**을 따로 보관한다(`:29-36`) — 표시용 문자열 하나로 판정하면 민감 파일이 조인 문자열 속에 숨는다는 주석이 있다(`:149-156`).
- **민감 경로는 어떤 정책에서도 자동승인 금지**: `.env`, `.env.local`, `.env.production`, `id_rsa`, `id_ed25519`, `.git`, `.ssh`(`:44-47, :144-146`).
- `workspace_session` 정책은 **모든 대상이 tmp 또는 cwd 내부**일 때만 자동 승인(`:165-172`, `is_relative_to`).
- 제안서 생성 실패 또는 요청자 예외 → **거부**(`:177-198`, "Requester exceptions deny by default").
- diff는 `acp.tool_diff_content(path=..., old_text=..., new_text=...)`로 전달(`:200-208`).

### 6.2 TalkAbout `tool_broker.py` 이식 판정 `[채택]` (S~M)

TalkAbout은 이미 `ExecutionFence`·`request_digest`/`intent_digest`·idempotency key 충돌 검사·`_unknown` 재정합·capacity·revision 충돌 검사를 갖고 있다(`tool_broker.py:120-160`) — 이 부분은 hermes보다 **엄격**하다 `[대조]`. 없는 것은 세 가지다:

| TalkAbout 현재 | hermes | 이식 |
|---|---|---|
| `Invocation.state ∈ {awaiting_approval, ready, executing, cancelled, ...}` (`tool_broker.py:56-62, 157`) | timeout 제3상태 + 결정↔bubble 종료 일치 | `expired_timeout` 상태 추가, `approved_by=None`으로 기록 |
| `ToolPermission(tool,target,action)`만 (`:13-21`) — **효과 프리뷰 없음** | `EditProposal` diff + `paths` | `ApprovalRequest`에 `preview`(bounded diff/대상경로) 추가 |
| 정책 프로파일 `text_only/fake_tools` (`:24-43`) | `ask/workspace_session/session` + 민감경로 예외(`modes.py:8-16`) | S4 활성화 시 3단 정책 채택 |
| `adapter`는 `FakeToolAdapter`만 허용(`:96-97`) | 실제 fs 브리지도 cwd 가둠(`copilot_acp_client.py:229-241`) | S4 때 경로 가둠 유틸 이식 |

주의: hermes의 승인은 **모델이 실행 중인 같은 프로세스 안**에서 ACP 클라이언트(=호스트)로 올라간다. TalkAbout의 구조(툴 실행이 worker 프로세스, 승인은 서버의 human)에서는 **요청-승인 왕복이 이미 네트워크**이므로, hermes의 "동기 콜백 + `future.result(timeout)`"(`permissions.py:77-114`)를 그대로 쓰면 안 된다. 차용할 것은 **상태 기계와 fail-closed 규칙**, 이식 형태는 TalkAbout의 기존 approval REST 경로다.

---

## 7. provenance (질문 3) — 그리고 3증거 비교

`acp_adapter/provenance.py`가 하는 일은 **세션 계보**이지 "누가 시켰나"가 아니다. 오해를 피하기 위해 정확히 적는다.

- ACP 세션 id는 **안정 공개 핸들**, 내부 `agent.session_id`는 압축(compression) 시 회전한다(`provenance.py:1-9`, `server.py:869-871`).
- `_meta.hermes.sessionProvenance`로 노출: `acpSessionId`, `currentHermesSessionId`, `rootHermesSessionId`, `parentHermesSessionId`, `sessionKind`(root/continuation), `compressionDepth`, 그리고 회전 시 `reason`/`creatorKind="compression"`(`:65-86`).
- 부모 체인은 **상한 100회 + `seen` 집합으로 순환 차단**(`:18` `_MAX_WALK = 100`, `:46-63`), DB 읽기 실패는 `None`(`:42-43`). "reading status text를 파싱하지 않는다"는 설계 주석(`:1-9`).

**비교**: TalkAbout의 3증거(등록됨/연결됨/실제 발언, 브리프 §2)는 **행위자 증거**이고 hermes의 provenance는 **세션 id 계보**다. 직접 대응하지 않는다 `[차용]`. 차용할 개념은 둘:

1. **안정 공개 핸들 vs 회전하는 내부 id의 분리** — TalkAbout의 `room_id`/`membership revision`/`generation`과 같은 층위 사고. ACP 응답 id 문제에서도 "전송 id ≠ 세션 id ≠ 턴 id"를 섞지 않는 규율로 이어진다(§5).
2. **_meta 확장 네임스페이스**(`_meta.hermes`)로 클라이언트가 무시해도 되는 부가 정보다 — TalkAbout이 외부 ACP 클라이언트에 붙일 부가 증거의 형태.

**반증 하나**: TalkAbout은 "Native auth mode/email/home은 안정적 계정 신원이 아니다. 이 어댑터는 공식 stable subject를 노출하지 않는다"라고 단정한다(`agent_runtime.py:397-399`). hermes는 **자격증명에서 유도한 계정 신원**을 실제로 쓴다: `_codex_snapshot_identity(token)`이 JWT principal(`chatgpt_account_id` + `sub`)을 디코드해 `identity`로 쓰고 "never the token itself", "두 자격증명이 한 워크스페이스 멤버를 공유하면 한 번만 센다"고 명시한다(`agent/account_usage.py:709-712`, 필드 주석 `:59-62`). 즉 "stable subject는 없다"는 것은 **native 로그인 파일/이메일 기준**으로는 맞지만, **발급된 토큰 기준**으로는 provider에 따라 존재한다. `[채택]` TalkAbout의 `_verified_account_subject`(:399)를 "불가"로 고정하지 말고 provider별 유도기를 두는 편이 낫다. 단 xAI/Grok용 유도기는 hermes에서 **찾지 못했다**(`account_usage`에 xai identity 없음).

---

## 8. 모델 카탈로그 (질문 4) — id 왕복 규율

`acp_adapter/model_catalog.py`의 본질은 목록이 아니라 **id 왕복(round-trip)**이다.

- 선택지 id는 `provider:model` 인코딩(`encode_model_choice`, `:146-152`)이고, `set_session_model`은 이 문자열을 그대로 `parse_model_input`으로 되돌린다(`server.py:322-327`). 광고한 id가 **다시 들어왔을 때 해석되어야** 한다.
- 중복 제거를 **2단**으로 한다: 인코딩 id(`seen_ids`) + 의미 id(`seen_semantic_ids`, `ollama` == `custom:ollama`)(`:155-187`). "picker에 현재 모델이 두 번 뜨는" 흔한 버그를 막는다.
- provider당 상한 200행(`:11-13`), 현재 모델은 fallback insert로 보존(`:238-...`).
- 카탈로그 조립 실패 시 **단일 현재 모델 행으로 폴백**(`server.py:300-310`) — 목록이 비어도 세션은 산다.
- named custom endpoint는 `custom:<name>` 슬러그로 정규화해 `parse_model_input`/`resolve_runtime_provider`가 해석할 수 있게 한다(`:19-40`, `:254-263`).

**TalkAbout 적용** `[차용]`: `talkabout/providers.py`는 `id`/`aliases`만 있고 **모델 카탈로그가 없다**(`:19-42`). `talkabout cli providers`가 카탈로그를 출력하지만(`STATUS.md:53`) 모델 id는 provider별 `default_model` 하나다(`providers.py:60-109`). 모델 선택이 생기면 hermes의 "광고 id = 수용 id" 왕복 규칙과 2단 중복제거를 그대로 쓰는 게 맞다. 비용 S.

---

## 9. provider 추상화 (질문 5)

### 9.1 hermes 구조

- `ProviderProfile`은 **선언적 dataclass**: `name/api_mode/aliases/auth_type/env_vars/base_url/models_url/...`(`providers/base.py:41-153`). docstring이 의도를 못박는다: "Provider profiles are DECLARATIVE ... They do NOT own client construction, credential rotation, or streaming. Those stay on AIAgent"(`:1-10`).
- `auth_type`이 enum 역할: `api_key|oauth_device_code|oauth_external|copilot|aws_sdk`(`:65`) + `external_process`(ACP/CLI, `:114-123` `process_command/process_args/process_command_env_vars/process_args_env_var`).
- 훅으로만 확장: `prepare_messages`, `build_extra_body`, `build_api_kwargs_extras`, `supported_reasoning_efforts`, `fetch_models`, `create_client`(`providers/base.py:155-380`, 요약표 `providers/README.md`).
- registry는 **plugin 자동 발견**(`plugins/model-providers/<name>/`, 41개 디렉터리 실측)이고, 소비자는 auth/models/doctor/config/runtime_provider/model_metadata/auxiliary/transports 7개 계층(`providers/README.md` "How it wires in").
- **ACP가 provider로 등록**된다: `copilot-acp` 행이 `auth_type="external_process"`(`hermes_cli/auth.py:186-188`), 그 profile이 `process_command/process_args`로 실행 정보를 준다(`plugins/model-providers/copilot-acp/__init__.py:15-24`).

### 9.2 TalkAbout `providers.py`와 대조

| 항목 | hermes | TalkAbout | 판정 |
|---|---|---|---|
| 형태 | dataclass + 훅 + plugin 발견 | frozen dataclass 메타데이터 + dict | hermes 우위(확장성) |
| 정직성 | profile이 "선언"이고 실행은 AIAgent | docstring "The registry is metadata only. ... the server never trusts a provider name for evidence"(`providers.py:10-11`) | **TalkAbout 우위** `[대조]` — 증거 분리 원칙이 코드 주석으로 강제된다 |
| transport | `api_mode` + `external_process` | `transport ∈ {cli, http}` + `status ∈ {ready, gated}`(`:20-24, 55-58`) | 동급. TalkAbout의 `status="gated"`는 hermes에 없는 정직한 표기 |
| auth | profile `auth_type` + 별도 auth registry | `key_env` 하나 + 런타임 `auth_mode` | hermes 우위(§10) |
| 카탈로그 | `fetch_models` 훅 + 폴백 + alias | `default_model`만 | hermes 우위(§8) |

**판정** `[차용]`: TalkAbout의 "metadata only" 원칙은 유지하고, `Provider`에 (a) `auth_types: tuple[str,...]`(지원 모드 명시), (b) `external_process` 실행 스펙 슬롯, (c) `capabilities`(streaming/tools/vision/모델목록)를 추가하는 정도가 적정. hermes식 plugin 발견은 **과하다**(TalkAbout은 단일 서버·폐쇄 카탈로그).

주의: hermes의 profile에는 **클라이언트 고유 쿼크**가 다수 들어간다(`fixed_temperature`, `OMIT_TEMPERATURE`, `unsupported_response_formats`, `native_reasoning_details_type`). TalkAbout의 OpenAI 호환 단일 어댑터(`http_runtime.py`)를 유지하려면 이런 쿼크를 `Provider`에 넣지 말고 **어댑터 내부 표**로 두는 편이 낫다 — profile 비대화는 hermes 스스로도 인정하는 비용이다(`base.py:1-10` "instead of receiving 20+ boolean flags"의 반대급부).

---

## 10. 인증 (질문 6) — API key / OAuth / 로컬 로그인

### 10.1 hermes의 분류

| 계층 | 내용 | 근거 |
|---|---|---|
| registry `auth_type` | `oauth_device_code`(nous), `oauth_external`(openai-codex, **xai-oauth**, qwen-oauth), `oauth_minimax`, `api_key`(다수), `external_process`(**copilot-acp**) | `hermes_cli/auth.py:150, 172-208` |
| 상태 조회 | `auth_type`→함수 **테이블 디스패치**(`_STATUS_BY_AUTH_TYPE`) — if 사다리 금지 | `auth.py:2188-2199` |
| OAuth 플로우 | `OAuthProviderFlow(provider, resolve_fn, status_fn, terminal_refresh_codes=...)` 테이블 | `auth.py:1980-1992` |
| **terminal refresh code** | `{"xai_refresh_failed","xai_auth_missing_refresh_token"}` 등 — "같은 provider의 refresh token을 재시도해도 성공 불가"를 **명시 분류** | `auth.py:1986-1988`, 판정 함수 `_is_terminal_refresh_error`(`:1997-1999`) |
| ACP 인증 협상 | initialize의 `auth_methods`는 **현재 해석 가능한 provider** + 항상 `hermes-setup`(terminal) (`acp_adapter/auth.py:30-51`), `authenticate`는 그 id만 수락(`server.py:552-565`) | |
| 증거 | `(verified: bool, source: str)`. **False = "여기서 검증 불가", "로그아웃"이 아님** | `auth.py:2079-2089` |
| subprocess 없는 검증 | 상태 엔드포인트에서 CLI를 spawn하면 콜드스타트 정지가 재현되므로 spawn 금지(`:2082-2086`). Copilot은 env token/plaintext store/hosts.json을 **읽기만** 한다(`:2092-2125`) | |

### 10.2 TalkAbout `--auth-mode native|api_key` 대조

TalkAbout은 이미 `auth_mode`를 런타임 분기로 갖고(`agent_runtime.py:433-445` `probe()`), native면 자식 env에서 모든 API key를 제거하며(`:640-643`), Grok은 격리 프로필 `HOME/USERPROFILE/APPDATA/...`에 `GROK_HOME`까지 고정하고 기능 플래그를 끈다(`:607-639`). 이건 hermes보다 **엄격**하다 `[대조]`.

hermes에서 가져올 것 `[차용]`:

1. **terminal / transient 구분**(`auth.py:1986-1988`) — TalkAbout은 `runtime_auth_required`/`runtime_auth_mode_mismatch`가 이미 "재시도 무의미"에 가깝지만, **credential refresh 상태 자체를 모델링하지 않는다**. Grok `cached_token`(STATUS:59)은 만료·refresh 실패가 언제든 올 수 있다.
2. **증거 3-상태**(`verified` / `not-verifiable` / `absent`) — TalkAbout의 `probe()`는 `provider_verified: False`와 `reason` 코드만 준다(`agent_runtime.py:444`). "검증 불가"와 "미인증"을 분리하면 `runtime_auth_mode_unsupported` 같은 오분류가 줄어든다.
3. **spawn 없는 상태 확인** 원칙(`auth.py:2082-2089`) — TalkAbout의 preflight는 CLI를 실제 실행해 help/version을 본다(설계상 필요, `agent_runtime.py:483-513`). Grok에서 10초+ 소요가 측정됐으므로(`:490-491`) 매 턴 반복하면 lease를 잡아먹는다 — **preflight 결과를 fingerprint로 캐시**(`_verified_executable` fingerprint, `:471-477`)하는 현재 방식은 옳다 `[대조]`. hermes의 `_ACP_PROBE_CACHE`(`copilot_acp_client.py:47`)도 같은 아이디어(바이너리 경로별 1회).

---

## 11. toolset gating (질문 7) — S4에 쓸 것

### 11.1 구조

- `TOOLSETS`는 **선언 dict**: `{"description","tools":[...],"includes":[...], **extra(posture 등)}`(`toolsets.py:47-49`). 번들은 `_bundle`/`_core_without`으로 조합(`:52-66`).
- `resolve_toolset(name, visited)`가 include를 재귀 해소(`toolsets.py:363`), `validate_toolset`이 존재 검증(`:486`), registry/plugin toolset도 합류(`:252-273, :410-430`).
- **세션 표면 축소**가 계층화: `session_platform_tool_drops(platform)`(`:449`), `session_disabled_toolsets(...)`(`:455`), `agent_tool_drops(agent)`(`:469`), `agent_tool_surface(agent, tools)`(`:476`). 클라이언트가 추가하는 toolset은 config가 아니라 코드 상수(`CLIENT_SURFACE_TOOLSETS`, `:68-71`) — "다른 표면이 그 도구를 안 준 것은 설정 선택이 아니다".
- **도구 단위 게이트**는 `check_fn`: registry에 `check_fn`/`requires_env`(`tools/registry.py:222-225`), 결과는 **30초 TTL 캐시 + 60초 실패 유예**(`:249-267`). 주석: "a flapping probe ... would silently strip a whole toolset" — 일시 장애로 도구셋이 통째로 사라지는 것을 막는다.
- `toolset_distributions.py`는 **배치 데이터 생성용 확률 분포**다(`:1-7`). `"browser+search": 97`처럼 묶음 1회 추첨으로 동시 출현을 보장(`:62-93`) — 게이팅이라기보다 샘플링. **TalkAbout S4와 무관** `[제외]`.

### 11.2 TalkAbout S4에 대한 판정

TalkAbout은 "S4 격리 명령/MCP 미연결, 내부 메모리 정책과 fake 어댑터만"(`docs/RELEASE_GAP_MATRIX.md:25`)이고 `ToolBroker`는 `profile ∈ {text_only, fake_tools}`, 어댑터는 `FakeToolAdapter`만 허용(`tool_broker.py:37, 96-97`). 즉 **게이팅 지점은 이미 정책 revision + fence**로 있다.

hermes에서 가져올 것은 두 가지뿐:

1. `[차용]` **표면 축소를 여러 계층으로 나누기**(platform / session / agent / client-surface). TalkAbout은 방 단위 `ToolPolicy` 하나뿐이라, "이 방은 read만", "이 실행은 default" 같은 축이 없다.
2. `[채택]` **`check_fn`의 TTL+플레이크 유예**(`tools/registry.py:249-267`). S4에서 외부 MCP/샌드박스 가용성을 매 호출 판정하면 flapping이 도구를 통째로 제거한다. TalkAbout `tool_broker._authorize`는 매 호출 fence 전체를 재검사하므로(`:120-127`) 유사 위험이 있다 — 단, TalkAbout은 **fail-closed가 설계 의도**이므로 캐시를 넣을 때는 "일시 장애로 도구가 사라지는 것"과 "정책 위반을 통과시키는 것"을 구분해야 한다. hermes의 유예도 **성공 이력이 있는 프로브**에만 적용된다(`:261-265` `_check_fn_last_good`/`_check_fn_ever_good`).

`check_fn` 자체를 S4 격리 명령 실행 승인에 쓰면 안 된다 — hermes에서 `check_fn`은 **가용성** 판정이고 승인은 `permissions.py`의 별개 경로다. TalkAbout도 이 분리를 유지해야 한다.

---

## 12. 라이선스 · 고지

| 저장소 | 라이선스 | 근거 |
|---|---|---|
| hermes-agent | **MIT** (Copyright (c) 2025 Nous Research) | `hermes-agent/LICENSE:1-3` |
| orca | MIT | `orca-analysis/LICENSE` (브리프 §4.7) |
| opencodex | 브리프 §4.7에 따라 담당 트랙에서 `opencodex/LICENSE`를 직접 확인해야 함 — **이 보고서에서는 미확인** | — |

hermes 코드를 TalkAbout에 **복사**할 경우 MIT 고지(저작권 표시 + 허가 문구)를 배포물에 포함해야 한다. 본 보고서의 권고는 대부분 **패턴 차용(재작성)** 이므로 고지 의무는 발생하지 않지만, `permissions.py`의 옵션 매핑표나 `_bounded_error_data`를 **문자 그대로** 가져오면 고지가 필요하다. 라이선스 충돌은 없다(TalkAbout은 이 저장소들을 링크·임베드하지 않고 read-only 분석).

---

## 13. 이식 로드맵 (근거 있는 순서)

| 순서 | 작업 | 태그 | 비용 | 부착 위치 | 완료 판정 |
|---|---|---|---|---|---|
| 1 | 실패 프레임 **구조 서술자** 기록(원문 아님) | `[채택]` | S | `agent_runtime.py:329-353` `diagnostics()` + `:673-705` | `runtime_protocol_mismatch` 발생 시 `{jsonrpc, id_type, id_len, method, bytes}`가 로그/영수증에 남는다 |
| 2 | `_rpc`/`_grok`의 불일치 id를 **skip + deadline 유지**로 | `[채택]` | S | `agent_runtime.py:683-705, :992-1004` | 불일치 프레임 1개가 런을 끝내지 않는다(회귀 테스트: 위조 프레임 주입) |
| 3 | 미해결 응답 **정산**(EOF/종료 시 전부 `outcome_unknown`) | `[채택]` | S | `agent_runtime.py:284-300` + `_pending` 구조 | EOF가 "대기"가 아니라 "미정산 정산"으로 끝난다 |
| 4 | lease 예산 3구간 분리(preflight / RPC 준비 / 모델 턴) | `[차용]` | M | `agent_runtime.py:403`(`timeout`), `:478-481` | 기본 20초 lease로 Grok 반복 턴 S1 재시험 가능 |
| 5 | 승인 상태기계에 **timeout 제3상태** + 효과 프리뷰 | `[채택]` | M | `tool_broker.py:45-62, 157` | `Invocation.state`에 `expired_timeout`, `ApprovalRequest.preview` |
| 6 | auth 증거 3-상태 + terminal refresh 분류 | `[차용]` | M | `agent_runtime.py:425-445`, `identity.py` runtime_check | `provider_verified`가 tri-state, `cached_token` 만료가 terminal로 분류 |
| 7 | capability raw 판정 + 폴백 | `[채택]` | S | `agent_runtime.py:952-962` | `session/load` 미지원 시 명시 폴백(현재 `runtime_resume_unsupported`로 하드 실패) |
| 8 | 표면 축소 계층 + check_fn TTL(성공이력 한정) | `[차용]` | L | S4 설계, `tool_broker.py` | S4 활성화 전 설계 문서 |

1~3은 **같은 파일·같은 함수**에서 끝나며 Grok 관문의 직접 원인 조사와 병행 가능하다. 4는 예산 승인이 필요하다(STATUS:66).

---

## 14. TalkAbout이 이미 우위인 것 (`[대조]` 요약)

브리프 §4.3("TalkAbout이 이미 더 잘하는 것은 `[대조]`로만")에 따라, hermes를 읽는 과정에서 **역방향으로 확인된 우위**만 모은다. 1라운드 Orca 대조와 중복되지 않는 항목만 적는다.

| 항목 | TalkAbout | hermes | 판정 |
|---|---|---|---|
| 증거 분리 원칙의 **코드 강제** | "The registry is metadata only. ... the server never trusts a provider name for evidence"(`providers.py:10-11`), `worker_reported_runtime` 같은 별도 증거 종류 | 없음(증거 종류 구분 없이 usage/status를 UI에 표시) | `[대조]` 설계가 더 엄격 |
| 자식 프로세스 **env allowlist + 값 마스킹** | `blocked_names`/`blocked_values`로 키 이름·값 모두 차단(`agent_runtime.py:404-407, 482`), native면 API key 전부 제거(`:640-643`) | `hermes_subprocess_env(inherit_credentials=True)`로 **자격증명 상속**(`copilot_acp_client.py:127-136`, 주석 "needs LLM provider credentials") | `[대조]` TalkAbout이 더 좁다 |
| Windows 격리 | Job Object containment, shell wrapper 거부(`probe()`, `:426-437`) | `windows_hide_flags()`로 콘솔만 숨김(`copilot_acp_client.py:365-372`) | `[대조]` |
| uncertainty 처리 | `call_started → uncertain` journal, 같은 인스턴스 재시도 금지(`agent_runtime.py:9` docstring, `worker.py`) | `settled() is None`(판독불가) 3-상태(`gateway_replay_gap.py:17-21`) — **개념은 동급, 구현은 TalkAbout이 트랜잭션적** | `[대조]`(동급 이상) |
| preflight 결과 캐시 | 바이너리 **fingerprint**(resolve/size/mtime_ns/inode)(`agent_runtime.py:471-477`) | 바이너리 **경로**만 캐시(`copilot_acp_client.py:47, 91-92`) | `[대조]` TalkAbout이 더 정확(같은 경로에 다른 바이너리 교체 감지) |
| 거부 응답의 **스키마 정합성** | grok 권한 거부 시 `{"outcome":{"outcome":"cancelled"}}`(`agent_runtime.py:660-661`) | 동일(`copilot_acp_client.py:496-497`) | 동급 |
| 툴 정책 fence | `ExecutionFence` + revision + 만료 + `_unknown` 재정합(`tool_broker.py:120-160`) | ACP 권한은 요청 단위, 세션 밖 fence 없음 | `[대조]` |
| 로깅 계층 | **0건**(round-1 결함) — hermes 압도 | 구조화 로깅 + redacting formatter(`entry.py:65-79`) | **hermes 우위** (이 항목만 역방향) |

요약: hermes가 이기는 축은 **"장기 실행·다표면 제품의 성숙도"**(재연결, 에러 귀속, 로깅, 카탈로그, auth 분류)이고, TalkAbout이 이기는 축은 **"증거·격리·불확실성의 엄격성"**이다. 두 축은 충돌하지 않으므로, TalkAbout은 hermes의 성숙도만 흡수하고 엄격성은 유지하는 것이 최적이다.

---

## 15. 미확인 · 모순 · 단정하지 않은 것

1. **Grok ACP id 불일치의 실제 원인: 미확인.** TalkAbout이 원본 프레임을 남기지 않았고(`docs/STATUS.md:59`, `agent_runtime.py:330-331`), hermes에 Grok ACP 코드가 없다. §4.2의 3경로는 **TalkAbout 코드에서 확인되는 mismatch 귀결 경로**이며 Grok이 어느 것인지 주장하지 않는다.
2. **hermes의 실제 ACP 프레임 규율: 미확인.** SDK(`agent-client-protocol==0.9.0`)는 저장소에 벤더링되어 있지 않다. 프레임 수준 규율은 Orca 1라운드 결론을 유지한다.
3. **`agent/agent_init.py`**: 122KB로 인증·provider 선택 부분만 보려 했으나, `init`의 인증 분기는 `hermes_cli/auth.py`의 registry/상태 함수로 위임되는 구조로 읽혀(§10) 별도 정독하지 않았다 — 이 파일에서 인증 로직을 **찾지 못했다**(파일 내 존재 여부는 미확인, 위임 구조만 확인).
4. **`agent/account_usage*.py` 전수**: 50KB 중 snapshot/window/identity 부분만 읽었다. quota 정책 전체 판정은 하지 않았다.
5. **모순 후보**: TalkAbout `agent_runtime.py:397-399`는 "stable account subject 없음"을 단정하지만, hermes는 JWT principal 기반 `identity`를 쓴다(`agent/account_usage.py:709-712`). 범위가 다르다(native 로그인 파일 vs 발급 토큰) — **모순이 아니라 범위 차이**로 판정했고 §7에 근거를 적었다.
6. **hermes `tools.py`(42KB), `commands.py`, `gateway_commands.py`, `content.py`, `gateway_media.py`, `gateway_session_meta.py`**: 슬래시 명령·미디어 staging·usage 메타는 이 트랙 범위 밖으로 판단해 정독하지 않았다. 이들에서 나올 수 있는 추가 발견은 미확인.
7. **`docs/STATUS.md:61-71`의 남은 관문 중 S2/배포/MFA/paging/POSIX**는 이 트랙 범위가 아니다(다른 트랙).
8. hermes의 `initialize`가 **요청 protocolVersion을 echo하지 않고 자기 버전을 반환**한다(`server.py:540`). TalkAbout은 `info.get('protocolVersion') != 1`이면 즉시 `runtime_protocol_mismatch`(`agent_runtime.py:956-957`) — ACP 스펙상 서버가 자기 지원 버전을 돌려주는 것이 정상이므로, **이 비교도 잠재적 mismatch 경로**다. Grok이 실제로 1을 돌려주는지 미확인(§4의 4번째 후보로 기록만 남긴다).
