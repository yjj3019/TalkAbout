# 외부 프로젝트 비교와 선택 기록

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

본 문서는 지정 commit의 정적 코드·라이선스 검토 결과를 반영한 설계 비교입니다. 실행 검증이나 전체 보안 감사 결과가 아닙니다. 패턴을 참고하며 코드를 복사·설치하거나 라이브러리를 확정 의존성으로 채택하지 않았습니다. 멀티모델 지원은 여러 모델을 한 실행기에서 호출하는 능력일 수 있으며, 서로 독립된 외부 runtime이 자기 권한으로 참여한다는 증거와 다릅니다.

## 검증표

| 정확한 저장소 / commit / license | 구현·불완전·로드맵 구분 | 참고 패턴 | 미채택 사유 |
| --- | --- | --- | --- |
| [openagents-org/openagents](https://github.com/openagents-org/openagents) develop `0a0ab10ba89353aa595ea960c8c7a2bea088a5cb`, [Apache-2.0](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/LICENSE) | Workspace Node adapter의 Claude/Codex/OpenClaw/Gemini CLI, membership/channel/events/session rotation, 파일 API와 control polling 코드 존재 | 외부 실행기 bridge, targeted events, 협조적 cancel과 상태 확인 | 승인·sandbox 우회 launcher 기본값과 full-trust machine/shared token 경로를 TalkAbout에서 채택하지 않음; optional bridge 후보만 |
| [openagents-org/openagents-sdk](https://github.com/openagents-org/openagents-sdk) main `faf416fca40b1cf73f585636478587967bc9f6f5`, [Apache-2.0](https://github.com/openagents-org/openagents-sdk/blob/faf416fca40b1cf73f585636478587967bc9f6f5/LICENSE) | Python SDK와 workspace mods 존재; Workspace의 Node adapter와 동일 기능 집합은 아님. 문서 권한 검사 비활성화·파일 덮어쓰기·gRPC send TODO 경로 존재 | capability별 명시 계약과 adapter 검증 필요성 | room ACL·불변 버전·transport 완성도를 별도로 설계해야 함; 전체 SDK를 core로 채택하지 않음 |
| [InternScience/AgentPanel](https://github.com/InternScience/AgentPanel) main `980687cad0d9b09851ca9b98f610f0359ebd4c2c`, [MIT](https://github.com/InternScience/AgentPanel/blob/980687cad0d9b09851ca9b98f610f0359ebd4c2c/LICENSE) | 독립 bot 등록/display name/API key, forum 읽기·댓글 코드 존재. 공개 tree에 startup/import에 필요한 backend main/services 및 runtime main/executor가 없어 완성 실행을 주장할 수 없음 | 독립 bot 신원, 초기 참여 안내, forum 참여 | raw key 비교·demo fallback, membership와 locked 상태 검사 근거 부족; versioned shared artifact 없음 |
| [agentgram/agentgram](https://github.com/agentgram/agentgram) develop `d811f86fcb5fa8590484dcecc51bc26f3e859763`, [MIT](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/LICENSE) | hashed key·expiry·인증 주체 middleware, persisted comments, 서명 검증 코드 존재. README의 향후 표기와 구현이 일부 다름. unsigned 통과 가능, group chat rewind는 stub success | hashed token·source-derived actor, 이벤트 기반 참여 | public mutable media는 private versioned artifact가 아님; base64 invite config는 인증 토큰이 아님; UI를 room backend 완성 증거로 삼지 않음 |

## 지정 코드 근거

OpenAgents Workspace의 [models.py](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/workspace/backend/app/models.py#L105-L139), [files router](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/workspace/backend/app/routers/files.py), [targeted events](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/workspace/backend/app/routers/events.py#L547-L590), [base adapter](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/packages/agent-connector/src/adapters/base.js)는 참고 대상입니다. [Claude adapter](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/packages/agent-connector/src/adapters/claude.js#L595-L598)의 `--dangerously-skip-permissions`, [Codex adapter](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/packages/agent-connector/src/adapters/codex.js#L315)의 `--dangerously-bypass-approvals-and-sandbox`와 Gemini의 자동 승인 기본값은 미채택입니다. [access.py](https://github.com/openagents-org/openagents/blob/0a0ab10ba89353aa595ea960c8c7a2bea088a5cb/workspace/backend/app/access.py#L318-L348)의 full-trust token 경로는 개별 최소 권한 credential의 대체가 아닙니다. 위 옵션은 분석 대상 문자열이며 실행 지시가 아닙니다.

SDK의 [documents permission 경로](https://github.com/openagents-org/openagents-sdk/blob/faf416fca40b1cf73f585636478587967bc9f6f5/src/openagents/mods/workspace/documents/mod.py#L475-L500)는 쓰기 권한 검사가 주석 처리되고 incoming version이 실제 충돌 제어에 쓰이지 않습니다. [shared_artifact update](https://github.com/openagents-org/openagents-sdk/blob/faf416fca40b1cf73f585636478587967bc9f6f5/src/openagents/mods/workspace/shared_artifact/mod.py#L474-L485)는 파일 덮어쓰기이며 불변 버전과 다릅니다. [gRPC send](https://github.com/openagents-org/openagents-sdk/blob/faf416fca40b1cf73f585636478587967bc9f6f5/src/openagents/sdk/transports/grpc.py#L338-L354)는 TODO 경로에서 실제 송신 없이 success를 반환합니다. [MCP auth](https://github.com/openagents-org/openagents-sdk/blob/faf416fca40b1cf73f585636478587967bc9f6f5/src/openagents/sdk/transports/mcp.py#L169-L198)는 token 설정 조건을 확인해야 합니다. transport 이름만으로 동등한 capability를 가정하지 않습니다.

AgentPanel의 [bot endpoint](https://github.com/InternScience/AgentPanel/blob/980687cad0d9b09851ca9b98f610f0359ebd4c2c/backend/app/api/v1/endpoints/bot.py), [auth dependency](https://github.com/InternScience/AgentPanel/blob/980687cad0d9b09851ca9b98f610f0359ebd4c2c/backend/app/api/deps/auth.py), [forum model](https://github.com/InternScience/AgentPanel/blob/980687cad0d9b09851ca9b98f610f0359ebd4c2c/backend/app/models/forum.py), [runtime 문서](https://github.com/InternScience/AgentPanel/blob/980687cad0d9b09851ca9b98f610f0359ebd4c2c/doc/AGENT_RUNTIME.md)를 비교했습니다. incoming key 직접 비교와 X-Demo-User fallback은 production 인증으로 복사하지 않습니다. thread 읽기의 membership 검사, comment 쓰기의 locked 상태 강제는 별도로 필요합니다.

AgentGram의 [api-key](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/packages/auth/src/api-key.ts), [middleware](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/packages/auth/src/middleware.ts), [request signature](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/packages/auth/src/request-signature.ts)는 key hash·expiry·인증 ID와 Ed25519/timestamp/nonce 처리의 참고입니다. 서명은 unsigned pass-through 때문에 필수 인증이라고 표현하지 않습니다. [댓글 route](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/apps/web/app/api/v1/posts/%5Bid%5D/comments/route.ts)와 [chat messages route](https://github.com/agentgram/agentgram/blob/d811f86fcb5fa8590484dcecc51bc26f3e859763/apps/web/app/api/v1/chats/%5BchatId%5D/messages/route.ts)의 persisted 구현·stub 경로를 구분합니다.

## ADR-001: 공통 플랫폼 계약과 선택 adapter

상태: PROPOSED. 여러 framework 전체를 하나로 합치지 않습니다. TalkAbout은 신원·membership ACL·append-only Event/vector cursor·idempotency·generation/membership revision fencing·version conflict의 작은 API core를 유지합니다. P0는 text-only reference worker+최소 CLI이며 quarantine/binary는 P2입니다. GUI와 MCP는 같은 core를 사용하고 MCP가 지속 실행기를 대체하지 않습니다. 외부 실행기는 provider credential을 자기 환경에 유지합니다. OpenAgents는 pinned optional bridge 후보이며 필수 core가 아닙니다.

이유: 유용한 패턴과 서로 다른 완료 수준·권한 모델이 함께 있습니다. 라이브러리 이름이나 모델 목록만으로 검증할 수 없습니다. P0-A는 동일 소유자의 서로 다른 실제 runtime 2개, P0-B는 별도 환경 연결·재접속 증거를 요구합니다. 다른 소유자는 별도 동의 관문입니다. capability별 연결·stop race·worker crash·replay·spoof·artifact conflict를 검증합니다. 외부 라이선스 확인은 코드 채택 승인이나 안전성 증명이 아니며 TalkAbout 자체 라이선스는 사용자 선택이 필요합니다.

## ADR-002: 서버 정지와 외부 실행 중지 분리

상태: PROPOSED. pause/close는 room generation으로 전원을, block/권한 회수는 해당 membership_revision으로 해당 멤버만 무효화하고 새 write·claim·complete를 차단합니다. unblock도 old attempt를 복원하지 않습니다. remote cancel은 stop_requested → acknowledged → stopped이며 연락 불가·확인 기한 초과는 unreachable입니다. acknowledged는 종료 증거가 아닙니다. 재연결 후 서버가 상태·권한을 다시 확인합니다.

외부 runtime의 강제 종료나 이미 수행한 외부 side effect의 rollback은 플랫폼이 보장하지 않습니다. GUI는 서버 차단 완료와 runtime 중지 확인을 따로 표시합니다. 권한 승인·sandbox를 끄는 실행 옵션을 기본값으로 채택하지 않습니다. 외부 도구 권한과 cancel 구현은 각 runtime 소유자와 P0에서 검증해야 합니다.

## 추가 비교

| 정확한 저장소 / commit / license | 구현 범위 | 참고·제외 판단 |
| --- | --- | --- |
| [gyhdc/multi-agent-group-chat](https://github.com/gyhdc/multi-agent-group-chat) main `e6dab9bd8ab16e864ad1ef3b603515c35bb8c477` | 중앙 서버의 provider 호출, 독립 agent 자발 pub/sub와 다름 | 미응답·직접 질문·새 human 증거 우선, 발언 소외 보정과 연속 발언 penalty 참고. tree와 package metadata에서 license 확인 안 됨; 코드 복사·제품화 조건 미확인으로 미채택 |
| [Bluear7878/AI-Colosseum-Debate](https://github.com/Bluear7878/AI-Colosseum-Debate) main `70632630bf2c5907d2891e63e3b7e3868ea937bd` | 중앙 CLI의 초기안·비판·양보·수정·judge, 독립 네트워크 참여와 다름 | 동일 입력의 독립 초기안, 주장 기록, 최종답/judge 분리를 선택 패턴으로 참고. tree와 pyproject에서 license 확인 안 됨; 코드와 승인·sandbox 우회 옵션 미채택 |
| [OpenBMB/ChatDev](https://github.com/OpenBMB/ChatDev) main `4fb2db0ea90375ce1059f44fe03ffbd191a7a169`, [Apache-2.0](https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/LICENSE) | 2.0/DevAll YAML agent/python/human/subgraph 중앙 graph | attachment/artifact ID 분리, hash/MIME/size/time/event cursor provenance 참고. 다운로드 인증·upload 제한은 배포 추가작업이며 완성 보안으로 간주하지 않음 |

[Group Chat provider contract](https://github.com/gyhdc/multi-agent-group-chat/blob/e6dab9bd8ab16e864ad1ef3b603515c35bb8c477/docs/provider-contract.md), [orchestrator](https://github.com/gyhdc/multi-agent-group-chat/blob/e6dab9bd8ab16e864ad1ef3b603515c35bb8c477/backend/src/orchestrator.ts#L424-L492), [providers](https://github.com/gyhdc/multi-agent-group-chat/blob/e6dab9bd8ab16e864ad1ef3b603515c35bb8c477/backend/src/providers.ts#L1669-L1712)를 정적 비교했습니다. Codex exec의 ephemeral 호출은 매 발언 실행이며 지속 세션 증거가 아닙니다.

[Colosseum context bundle](https://github.com/Bluear7878/AI-Colosseum-Debate/blob/70632630bf2c5907d2891e63e3b7e3868ea937bd/src/colosseum/services/context_bundle.py#L273-L294)의 URL은 URI metadata 고정이며 실제 내용 snapshot과 다릅니다. [judge](https://github.com/Bluear7878/AI-Colosseum-Debate/blob/70632630bf2c5907d2891e63e3b7e3868ea937bd/src/colosseum/services/judge.py)의 evidence count/existence 휴리스틱은 사실 검증이 아닙니다. [CLI adapters](https://github.com/Bluear7878/AI-Colosseum-Debate/blob/70632630bf2c5907d2891e63e3b7e3868ea937bd/src/colosseum/providers/cli_adapters.py#L138-L247)의 승인·sandbox 우회는 채택하지 않습니다.

[ChatDev attachment guide](https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/docs/user_guide/en/attachments.md)와 [graph workflow](https://github.com/OpenBMB/ChatDev/blob/4fb2db0ea90375ce1059f44fe03ffbd191a7a169/workflow/graph.py)는 중앙 workflow 계층의 참고이며 TalkAbout의 참여 서버와 같은 계층이 아닙니다.

다음 세 대상은 얕은 후속 후보 검토입니다. 상세 코드 정밀검증과 pinned 실행 검증은 완료하지 않았습니다. license는 저장소 수준 참고이며 채택 전에 고정 commit에서 다시 확인합니다.

| 후보 | 참고 범위 | 현재 판단 |
| --- | --- | --- |
| [agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope), Apache-2.0 | [A2AAgent 문서](https://docs.agentscope.io/latest/en/building-blocks/a2a), interrupt/resume, TeamPipeline | optional adapter 실험 후순위 |
| [microsoft/agent-framework](https://github.com/microsoft/agent-framework), MIT | [공식 문서](https://learn.microsoft.com/agent-framework/), checkpoint/HITL | 승인·복구 개념 참고; 미채택 |
| [camel-ai/oasis](https://github.com/camel-ai/oasis), Apache-2.0 | 소셜 시뮬레이션 목적 | 협업 MVP와 목적이 달라 제외 |

## ADR-003: 선택 토론 패턴과 provenance

상태: PROPOSED. 공정한 발언 queue 안에서 미응답 직접 질문과 새 human 증거를 우선 검토하는 선택 정책을 제공합니다. 발언 소외 보정, 연속 발언 penalty와 종료 상한을 유지합니다. 새 질문이 한도를 끝없이 연장하지 않습니다.

제한 라운드의 선택 설정으로 동일 context bundle을 고정하고 다른 답안을 공개하기 전에 독립 초기안을 받는 단계를 검토합니다. 서버는 제출 기간에 다른 초안을 숨길 수 있지만 외부 runtime의 독립 사고·외부 통신을 강제하거나 증명할 수 없습니다. claim/rebuttal/concession과 미합의 쟁점을 기록하고 최종 산출물과 judge 평가를 분리합니다. 평가는 사실 인증이 아닙니다.

context provenance는 URI, 수집 시각, 허용된 실제 내용 snapshot hash, 원문 event range를 구분합니다. URI만 보유한 자료는 uri_only로 표시합니다. 임의 server URL fetch를 열지 않고 소유자 업로드도 quarantine·ACL을 유지합니다. 이 선택 패턴은 P0 실제 연결 관문을 대체하지 않습니다.
