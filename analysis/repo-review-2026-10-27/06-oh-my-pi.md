# 06. oh-my-pi — IDE 도구 엔진·동시 작업·컨텍스트 관리의 선택적 채택

조사일: 2026-10-27 KST · 고정 revision: `9c4933a978439acfec958488f14b36ac6a7ec187` · 대상: `can1357/oh-my-pi`

## 1. 결론

**채택 대상은 런타임 전체가 아니라, 작업 결과를 신뢰할 수 있게 만드는 몇 개의 계약이다.** 우선순위는 ① 타입화된 작업/도구 이벤트와 명시적 terminal 판정, ② 취소 가능한 동시성 제한과 자식 작업 정산 장벽, ③ 원본 이력을 지우지 않는 압축 경계·근거 보존, ④ 승인 후 최신 파일 재독해/편집 staging, ⑤ 문서 버전에 결합된 LSP 진단이다.

이 저장소는 단순 채팅 CLI가 아니다. TypeScript agent/provider 계층과 Rust 편집·검색·셸·파일시스템 계층을 결합한 코딩 도구 엔진이다. 다만 이를 그대로 TalkAbout에 넣으면 Bun/N-API/Rust/플랫폼별 프로세스·LSP·인증·설정까지 새로운 신뢰 기반이 된다. **기존 Python 외부-worker 경계를 유지하고, W1 Codex 실행의 이벤트·산출물·공동 작업 의미론부터 개선하는 편이 적합하다.**

특히 다음을 혼동하면 안 된다.

- task isolation은 기본 `false`이며, 지정하지 않은 작업은 공유 checkout에서 실행된다. `isolated: true`도 COW/worktree 작업 트리 분리이지 OS 실행/네트워크 sandbox의 증거가 아니다. [task 설정:24–44](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/task/settings.ts#L24-L44), [isolation PAL:1–21](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-iso/src/lib.rs#L1-L21)
- 도구 승인 모드가 존재한다는 것과 안전한 기본값은 별개다. 실행 context가 없으면 `always-ask`지만 설정이 있는 정상 세션의 schema default는 `yolo`, auto-approve 역시 `yolo`다. TalkAbout에 이 기본값은 채택하지 않는다. [승인 해석:81–111](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/tools/approval.ts#L81-L111)
- hashline의 네 자리 태그는 콘텐츠 위치/관찰 이력의 편집 UX다. 무결성 서명·권한 토큰·충돌 없는 artifact identity가 아니다.

## 2. 조사 범위와 증거 등급

로컬 고정 소스·테스트를 읽은 **정적 분석**이다. 외부 저장소 코드, CLI, 테스트, 설치 스크립트, plugin, native build를 실행하지 않았고 네트워크를 사용하지 않았다. 아래 GitHub 링크는 읽은 로컬 소스의 revision과 정확한 line 범위를 가리키는 인용이며, 이번 조사에서 원격 페이지를 조회했다는 뜻은 아니다. 테스트 내용은 구현 의도/회귀 계약의 증거이지 통과·성능·보안 검증 결과가 아니다. 모든 외부 테스트는 **NOTRUN**이다.

TalkAbout 비교 기준은 의뢰자가 지정한 현재 상태다. **R3는 one-shot server start 및 uncertain retirement, R4 vault/DPAPI opt-in은 완료, W1은 owner가 opt-in한 Codex workspace이고 MCP/web은 허용하지 않는다.** 여기서는 TalkAbout 전체 구현을 재감사하거나 R3/R4를 미구현 과제로 되돌리지 않는다. 목표는 응답을 나란히 보여 주는 것 이상으로, 실제 coding+analysis 작업을 분담하고 검토·수정·검증한 결과를 한 작업으로 정산하는 것이다.

## 3. 실제 IDE/편집 기능

### 3.1 편집은 다중 dialect + native staging 엔진

편집 도구는 replace, patch, hashline, apply_patch, sloppy 스키마를 받아 native `EditSession`에 연결한다. LSP writethrough, 파일 변경 통지, deferred diagnostics, 캐시 무효화, auto-repair도 같은 어댑터에 연결된다. 따라서 hashline 프롬프트만 복사해서 동일 기능을 얻을 수 없다. [편집 어댑터:14–79](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/edit/index.ts#L14-L79), [입력 dialect:98–105](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/edit/index.ts#L98-L105)

Rust apply 경로는 **완성된 인수 확인 → 대상 최신 재독해 → 모든 파일을 메모리에 stage → 모든 파일의 쓰기 정책 확인 → payload 순서로 write**한다. preview 때 얻은 내부 URL 응답은 apply에 재사용하지 않는다. 좋은 패턴이지만 **다중 파일 원자 트랜잭션은 아니다**. writer가 중간 실패하면 앞에서 기록한 파일은 남는다고 명시하고 실제 루프도 `await?`로 중단한다. TalkAbout은 일부 적용 결과를 `failed` 한 단어로 숨기지 말아야 한다. [apply 계약/구현:219–255,279–329](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-edit/src/session.rs#L219-L329)

### 3.2 hashline: 관찰한 버전·행의 provenance

hashline은 `[path#TAG]` section의 line-anchored 연산을 parse하고 stage한다. engine의 `enforce_seen_lines`가 patcher로 전달된다. snapshot은 canonical path, 정규화된 전체 텍스트, content tag, 표시한 행 집합을 저장한다. 저장 한도는 기본 256 paths, path당 4 versions, 추정 64 MiB이고 4 MiB 초과 파일은 disk snapshot을 만들지 않는다. 같은 no-op 3회 escalation도 있다. [hashline engine:37–89](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-edit/src/modes/hashline/mod.rs#L37-L89), [snapshot 구조/한도:14–40](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-edit/src/store.rs#L14-L40)

중요한 제한: 태그는 각 행 끝의 space/tab/CR을 제거한 텍스트에 xxHash32를 계산하고 **하위 16 bit만** 네 자리 hex로 표시한다. 보안 해시가 아니고 trailing whitespace 변화도 구별하지 않는다. TalkAbout의 immutable artifact version에는 별도의 충돌 저항 digest/버전 ID를 유지해야 한다. [hash 계산:74–95](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-edit/src/store.rs#L74-L95)

정적 테스트 증거는 강한 편이다. 행이 삽입되어 번호가 밀린 후 옛 행 번호로 replace하면 `never displayed`로 거부하고 파일 불변을 확인한다. 본문을 맞게 추측해도 관찰하지 않은 anchor는 거부한다. 이 계약은 'LLM이 기억한다고 주장함'을 파일 관찰 증거로 인정하지 않는다는 점에서 유용하다. [회귀 테스트:1143–1210](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-edit/src/modes/hashline/patcher.rs#L1143-L1210)

**TalkAbout 권고:** 당장 새 편집 언어를 Codex에 강제하지 않는다. 먼저 worker가 읽은 workspace/artifact revision과 제안 patch의 base digest를 남기고, owner 또는 적용 coordinator가 최신 base와 비교하는 optimistic-concurrency 계약을 만든다. hashline 전체 이식은 그 다음의 별도 실험이다.

### 3.3 LSP는 실제 탐색/변경/진단 경로

읽기 action은 diagnostics, definition, type_definition, implementation, references, hover, symbols, status, capabilities다. 그 밖의 rename, 적용 code action, rename_file, reload, raw request 등은 write-tier로 분류한다. LSP 도구가 이 집합으로 승인 tier를 결정한다. 즉 이름이 `lsp`라고 전부 read-only인 것은 아니다. [action 분류:21–36](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/lsp/servers.ts#L21-L36), [도구 승인:169–177](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/lsp/tool.ts#L169-L177)

write-through 옵션에는 format-on-write, diagnostics, late diagnostic callback, 취소 signal이 있다. 결과는 `finalContent`와 진단을 분리한다. 이 구분은 formatting 후 실제 파일과 모델이 제안한 파일이 다를 수 있음을 다룬다. TypeScript 파일 생성 뒤에는 광고된 tsserver command capability를 확인하고 reloadProjects를 요청하는 특수 경로도 있다. [writethrough:34–83,86–131](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/lsp/writethrough.ts#L34-L131)

진단 freshness 테스트는 document version 1에서 시작해 sync 후 version을 증가시키고, 옛 unversioned error 뒤 새 version의 빈 진단을 게시하여 결과가 OK인지 검증한다. 단순 '파일 쓰기 직후 오류 없음'보다 정확한 계약이다. [freshness 테스트:440–481](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/test/tools/lsp-diagnostics-freshness.test.ts#L440-L481)

**TalkAbout 권고:** W1 초기에는 Codex 실행이 보고한 검사 결과를 `claimed` evidence로 저장한다. 향후 별도 승인된 검증 worker가 검사하면 `verified`로 승격한다. LSP 자체를 추가할 때는 workspace별 명시적 실행 파일 allowlist·수명·환경 정책이 필요하다. read action도 language server 프로세스 실행을 수반할 수 있으므로 '읽기 tool = 실행 없음'으로 취급하지 않는다.

## 4. concurrency·task·subagent 조정

### 4.1 동시성 제한은 두 수명으로 나눈다

`mapWithConcurrencyLimitAllSettled`는 일부 실패해도 이미 시작한 sibling을 끝까지 기다리고 입력 순서의 결과를 남긴다. 취소는 새 작업 launch만 중단하며 시작한 작업의 정산을 보존한다. semaphore는 AbortSignal을 받아 취소된 waiter를 queue에서 제거한다. resize도 기존 instance를 바꿔 in-flight slot을 잃지 않는다. [구현:84–125,140–212](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/task/parallel.ts#L84-L212)

별도로 provider slot은 전체 agent lifetime이 아니라 **LLM 요청 stream이 생산을 마칠 때까지만** 점유한다. 부모가 자식을 기다리는 동안 slot을 쥐면 spawn tree가 deadlock하기 때문이다. 현재 이 모듈의 설정 표에는 ollama-cloud cap만 연결되어 있으므로 '모든 provider에 자동 제한'으로 과장하면 안 된다. [provider limiter:1–23,68–101](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/task/provider-concurrency.ts#L1-L101)

테스트는 sibling 실패 후 나머지 성공/순서 유지, 취소 후 두 번째 작업 미시작과 이미 시작한 작업 정산을 확인한다. [동시성 계약 테스트:4–57](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/test/task/parallel.test.ts#L4-L57)

### 4.2 '최종 답변'과 '작업 완료'는 다르다

subagent executor는 final yield 뒤에도 owner background job이 남아 있으면 완료가 아니라 scheduling pause로 간주한다. 결과가 나중에 배달되면 이전 yield는 무효화되고 새 정산 답변이 필요하다. pending owner work가 없을 때만 terminal이 되어 worktree capture/정리가 진행된다. 실제 제어 루프는 `hasPendingAsyncWork`, `settleAsyncWork`, yield 상태를 다시 확인한다. [정산 장벽:2626–2671](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/task/executor.ts#L2626-L2671)

격리 runner 역시 성공 결과 capture 전에 deferred cleanup을 기다린다. branch capture 실패 시 복구 branch·patch artifact를 보존하고, 그것마저 실패하면 workspace를 retain하는 경로가 있다. 단, 자동 commit/merge 기능을 현재 TalkAbout에 같이 들여오라는 뜻은 아니다. [capture·복구:470–532](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/task/isolation-runner.ts#L470-L532)

### 4.3 메시지도 출처와 응답 의무를 가진다

IRC bridge는 parent steer를 prompt text가 아닌 object identity로 추적하고 peer message는 완료된 결과에 이어붙이지 않고 별도 turn으로 처리한다. interrupt/aside/deferred wake queue와 pending reply obligation을 구분하며, reply 전부 정산을 기다릴 수 있다. session 전환 시 pending queue가 다음 transcript에 누출되지 않도록 명시적으로 clear/restore한다. [bridge 상태/응답 의무:23–40,84–119](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/session/irc-bridge.ts#L23-L119)

**TalkAbout에 가장 필요한 차용:** 작업 A가 B에게 검토를 맡기면 'B 시작' 알림이 아니라 `(task_id, attempt_id, request_id, parent_id, artifact_version)`으로 응답 의무를 남긴다. B의 검토가 늦게 왔으면 A의 옛 final을 덮어쓰지 않고 `review_received → revision_requested/reconciled` 이벤트를 추가한다. room 채팅의 말투나 '부모입니다' 텍스트는 실행 권한이 아니다.

## 5. context compaction·history

이력 entry는 `id`, `parentId`, timestamp를 가지며 compaction entry는 summary, firstKeptEntryId, tokensBefore, providerReplayThroughEntryId, details/preserveData를 가진다. branch summary와 reset boundary도 별도 타입이다. 이는 '이전 대화를 요약문으로 덮어쓰기'보다 명확한 provenance 계약이다. [entry schema:4–63](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/agent/src/compaction/entries.ts#L4-L63)

cut point는 assistant/tool 그룹을 통째로 센다. tool result에서 자르지 않으며 assistant tool call 뒤의 결과를 보존한다. 최신 그룹 하나가 예산을 초과해도 그것을 남기는 정책이므로 이것만으로 모든 요청 크기를 상한 이하로 만든다는 보장은 아니다. 구현은 provider별 tokenizer의 latest-turn 계산과 reset/compaction 경계도 다룬다. [cut 알고리즘:521–600](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/agent/src/compaction/compaction.ts#L521-L600)

file operation metadata는 이전 compaction의 readFiles/modifiedFiles와 새 도구 호출에서 추출해 누적한다. native Anthropic, OpenAI/Codex remote compaction, streaming compaction 경로가 각각 연결되어 있어 provider별 replay state를 단순 공통 문자열로 뭉개지 않는다. [file provenance:108–143](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/agent/src/compaction/compaction.ts#L108-L143), [native 경로:48–83](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/agent/src/compaction/compaction.ts#L48-L83)

oversized custom message/branch summary가 backward scan에서 다시 포함되지 않는지 테스트하며, signed reasoning을 replay하는 최신 turn의 토큰도 계산 대상으로 삼는다. [cut 회귀 테스트:50–105](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/agent/test/compaction-cut-point.test.ts#L50-L105)

**TalkAbout 적용:** room 원본 event log/immutable artifact를 정본으로 유지하고 `ContextSnapshot`을 파생물로 만든다. snapshot에는 포함 event 범위, task/attempt, source artifact versions, 생성 model/시간, 미해결 질문, 실패·uncertain·취소 상태를 남긴다. worker에 주는 요약에는 '결론'뿐 아니라 아직 검증 안 된 claim을 구분한다. compaction은 R3 uncertain fence나 R4 동의 범위를 삭제/변경할 권한이 없다. provider 내부 resume token은 room 공유 요약과 별도 저장하며 다른 provider로 재사용하지 않는다.

## 6. provider capabilities·protocol/event schema

공통 AI API는 Anthropic, Bedrock, OpenAI completions/responses/Codex, Azure, Google, Ollama, Cursor 등 API별 option 타입을 명시적으로 연결하고 KnownApi exhaustiveness를 확인한다. 이것은 'OpenAI compatible이면 모든 동작 동일'이라는 가정보다 안전하다. [API option map:75–100](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/ai/src/types.ts#L75-L100)

stream event union은 text/thinking/toolcall의 start/delta/end, image_end, done/error를 분리한다. done은 stop/length/toolUse, error는 aborted/error에 한정한다. **toolUse는 모델 응답 종료이지 사용자의 작업 성공이 아니다.** 이 union은 TypeScript 타입 계약이며 외부 JSON을 런타임에서 검증하는 보장을 그 자체로 제공하지 않는다. [event union:1665–1688](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/ai/src/types.ts#L1665-L1688)

headless RPC는 stdin/stdout JSONL이고 protocol negotiation, prompt/steer/follow-up, abort, state/history, subagent subscription/cancel/steer, compaction, branch/fork가 별도 명령이다. full snapshot과 delta projection도 분리한다. 하지만 bash/login/host tools 같은 강력한 명령도 같은 프로토콜에 있다. **RPC 지원을 곧 원격 비신뢰 client에 공개 가능한 API로 해석해서는 안 된다.** [RPC 명령:1–6,32–74,95–129](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/modes/rpc/rpc-types.ts#L1-L129)

모델 정책은 catalog resolver에서 API defaults → host facts → compiled cascade → 명시 override로 분리한다. 결과도 identity/compat/thinking/catalog/request로 나뉜다. KDL 전체 compiler를 TalkAbout에 이식할 필요는 없지만 capability와 권한을 분리하는 원칙은 중요하다. [resolver 계층/타입:1–60](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/catalog/src/compat/resolve.ts#L1-L60)

**TalkAbout 적용:** `observed_provider_fact`와 `owner_grant`를 분리한다. 모델이 tool call을 생성할 수 있다는 사실은 workspace 쓰기/MCP/web 권한이 아니다. Codex exec-json의 원본 event는 크기 제한·redaction 뒤 보존하고, UI용 normalized event에 schema_version, provider, source_event_type, sequence, task/attempt ID, tool_call_id, outcome, usage를 추가한다. 미지원/미지 event는 성공으로 해석하지 말고 제한된 unknown observation으로 남긴다. reasoning 원문은 공동 room에 자동 노출하지 않는다.

## 7. 안전한 workspace 실행: 가져올 것과 가져오지 않을 것

| 관찰된 패턴 | 평가 | TalkAbout 경계 |
|---|---|---|
| read/write/exec tier와 대상별 가장 엄격한 승인 | 채택할 분류 모델 | 승인·workspace root·attempt에 scope를 묶고 미지 도구 deny. yolo 기본은 제외. [tier/strictest:127–157](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/tools/approval.ts#L127-L157) |
| COW/worktree와 patch capture | 충돌/복구 비용 감소 | 네트워크·홈 디렉터리·자격증명 접근 차단으로 주장 금지. owner opt-in root 이외 쓰기 금지와 프로세스 제어는 별도. |
| 승인 후 최신 재독해와 staging | 우선 채택 | 부분 write failure를 명시하고 재실행 전에 reconciliation. |
| 파일별 mutation queue | 범위를 좁혀 참고 | **built-in edit/write는 이 queue를 쓰지 않는다**고 주석에 명시. 전체 편집 경쟁 해결 장치로 인용 금지. [queue:30–58](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/coding-agent/src/tools/file-mutation-queue.ts#L30-L58) |
| agent final 뒤 pending job barrier | 강력히 채택 | server가 positive terminal proof를 받을 때까지 완료 아님. 연결 끊김을 성공/안전한 실패로 추정하지 않음. |
| 자동 branch commit/merge | 현 단계 제외 | W1은 patch/artifact 제출·owner 검토가 우선. 조사 자체에서도 commit 없음. |

R4 vault는 자격증명 at-rest 보호와 사용자 opt-in의 문제를 해결한다. 이것이 worker의 임의 tool 권한·네트워크 egress를 승인하는 것은 아니다. R3 one-shot start/uncertain retirement 규칙은 편의성 retry보다 우선한다. 외부 worker가 작업을 시작했는지 불명확하면 **같은 coding side effect를 자동 재실행하지 않는다**. 이 저장소의 동시성·replay 기능을 차용해도 이 규칙을 약화하지 않는다.

## 8. 라이선스·Rust/native 의존성과 도입 비용

실제 루트 LICENSE는 MIT이며 Mario Zechner, Can Bölük, Stencil Labs 저작권과 notice 포함 의무가 있다. package metadata만 확인한 것이 아니다. 코드의 실질 부분을 복사/재배포하면 해당 notice를 유지해야 한다. [실제 LICENSE:1–23](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/LICENSE#L1-L23)

루트 Cargo workspace에는 pi-ast/builtins/diff/edit/iso/natives/predict/shell/vcs/vfs/voice/walker와 vendored brush/napi/tree-sitter 등이 명시된다. patch.crates-io는 vendored 수정본을 실제 dependency resolution에 연결한다. 'TS 라이브러리 하나'로 분류하면 도입 비용을 크게 누락한다. [Rust workspace/patch:1–48](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/Cargo.toml#L1-L48)

pi-natives는 `cdylib`이고 napi, ast-grep, grep-pcre2, image/resvg, pdf-inspector, portable-pty, tokio/rayon 및 자체 edit/shell/iso/voice를 연결한다. Windows에서는 windows/windows-sys, clipboard, enigo, uiautomation, macOS/Linux에서는 별도 OS dependency가 있다. 기능 하나의 원리 차용과 배포 binary를 제품 의존성으로 추가하는 것은 전혀 다른 검토다. [native dependency:10–23,25–119](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/crates/pi-natives/Cargo.toml#L10-L119)

JS wrapper의 실제 entry도 native index이고 build는 Bazel native 경로, engine 요구는 Bun >=1.3.14다. 현재 TalkAbout에 직접 import하려면 별도 runtime/binary distribution·ABI·업데이트·플랫폼별 빌드 검증이 필요하다. [native package:29–37,56–73](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/packages/natives/package.json#L29-L73)

루트 THIRD-PARTY-NOTICES는 스스로 package-level license를 대체하지 않으며 first-party license가 타사 코드/폰트/데이터/상표 권리를 부여하지 않는다고 명시한다. 실제 binary나 native 코드를 들여오면 lockfile·vendor·정적 링크·asset별 notices를 별도로 검토해야 한다. 이번 조사는 모든 전이 의존성 라이선스 완결 감사가 아니다. [notices 범위:1–16](https://github.com/can1357/oh-my-pi/blob/9c4933a978439acfec958488f14b36ac6a7ec187/THIRD-PARTY-NOTICES.txt#L1-L16)

## 9. 순위별 채택안과 구체적 TalkAbout 통합

| 순위 | 독립적으로 채택할 패턴 | 구현 단위/수용 기준 | 제외할 확대 |
|---|---|---|---|
| **1 / P0** | 타입화 event + terminal ledger | 기존 외부-worker journal 위에 `requested/started/streaming/tool_running/result_saved/completed/failed/cancelled/uncertain`의 명시 전이. attempt마다 terminal 증거·원본 event provenance 기록. late event가 terminal을 되돌리지 않음. | 전체 pi-ai provider runtime 교체 |
| **2 / P0** | parent/child 응답 의무 + 정산 장벽 | 부모 task가 낸 검토 요청, 시작한 검사, 제출 artifact를 추적. pending이면 final을 provisional로 표시. 취소 시 새 dispatch 중단·기존 작업 종료/불명 정산 뒤 종료. | 무제한 agent spawning, worker 내부 자의적 재귀 권한 |
| **3 / P1** | 파생 context snapshot | 원본 room event와 artifact는 보존. summary에 포함 범위/버전/미해결 상태/uncertain를 고정. stale summary로 patch 적용 금지. | 요약이 source of truth 또는 실행 승인으로 승격 |
| **4 / P1** | 승인 뒤 fresh-read + patch base | worker는 patch와 base digest, 영향 파일, 검사 명령/결과를 제출. 적용은 별도 owner-authorized coordinator. base 불일치 시 재검토 요청, blind retry 없음. | hashline 전체 문법 즉시 강제, 자동 commit/merge |
| **5 / P2** | 버전 결합 검증/LSP 진단 | 검증 event에 workspace revision/파일 digest와 검사 실행 여부를 기록. 늦은 진단은 새 event로 오고 옛 artifact를 verified로 바꾸지 않음. | LSP 자동 설치/프로젝트 plugin 실행 |
| **6 / P2** | 선언형 capability facts | 작은 검증된 provider matrix부터 시작: supports tool events/resume/usage/abort, observed_at, source, protocol_version. grant와 별도. | KDL compiler/카탈로그 전체 복제, model 이름 휴리스틱 권한 |

### 공동 coding+analysis의 최소 실현 시나리오

1. **Owner 동의:** private room에서 특정 workspace root와 작업을 W1에 opt-in한다. MCP/web/browser/plugin은 그대로 비활성. 기존 R4 credential 선택은 범위를 넓히지 않는다.
2. **분해:** coordinator가 같은 immutable UTF-8 manifest/artifact version을 coding worker와 read-only analysis/review worker에 준다. 각 worker의 source와 capability를 UI에 표시한다.
3. **작업:** coding worker는 제안 patch·영향 파일·base revision을 제출하고, reviewer는 주장별 source 위치·결함/불확실성·권고를 제출한다. 서로의 메시지를 사용자 지시로 취급하지 않는다.
4. **조정:** reviewer가 결함을 찾으면 coordinator가 새 revision request를 남긴다. coding worker의 기존 final은 이력을 유지하고 새 artifact version으로 수정한다. shared workspace 동시 쓰기는 피하고, 우선 task 단위 단일 writer lease를 둔다.
5. **검증:** 승인된 검사만 실행하고 test command, exit status, 대상 revision, output artifact를 기록한다. `NOTRUN`, worker 주장, 실제 검증을 구별한다. pending 검사/peer reply가 있으면 완료가 아니다.
6. **정산:** owner가 patch를 검토·적용한다. process start/stop이 불명확한 경우 R3 uncertain 경로로 retire하고 동일 side effect를 자동 replay하지 않는다. immutable 결과와 실패/취소 근거를 남긴다.

### TalkAbout용 추가 회귀 검증 제안 — 아직 실행하지 않음

- 자식 실패 후 sibling 결과 유실 없음, 취소 뒤 새 작업 미시작, 취소된 limiter waiter가 slot을 먹지 않음.
- 부모 final 이후 늦은 검토/검사가 오면 새 정산이 필요하고 기존 final/버전은 불변.
- start acknowledgement 유실·worker 재시작 시 uncertain 유지, 자동 coding 재실행 없음.
- 같은 workspace base에 두 patch가 오면 두 번째 적용은 base mismatch로 거부·재검토.
- summary 압축 후에도 owner opt-in, credential scope, uncertain, artifact version, 미완료 응답 의무 유지.
- unknown/oversized/malformed provider event가 tool execution 또는 completed로 해석되지 않음.
- document version이 다른 늦은 diagnostics를 현재 결과의 성공 근거로 붙이지 않음.
- R4 secret·provider resume state·reasoning 원문이 room artifact/요약에 자동 유출되지 않음.

## 10. 최종 판단

oh-my-pi에서 가장 가치 있는 것은 많은 도구 이름이 아니라 **편집 전 관찰 증거, 비동기 작업의 수명 정산, replay/요약 경계, provider event의 의미 분리**다. 이 패턴들을 TalkAbout의 R3/R4/W1 경계 안에서 작게 구현하면 실제 공동 코딩·분석에 직접 도움이 된다. 반대로 전체 CLI/runtime, 자동 plugin/LSP discovery, native shell/desktop 기능, 자동 commit/merge를 함께 가져오는 것은 현재 목표보다 신뢰 범위를 크게 넓힌다.

**권고: P0 이벤트·정산 계약부터 독립 설계로 채택하고, 소스 복사·native runtime 의존성 추가는 보류한다.**
