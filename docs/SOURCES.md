# 공식 참고 자료와 설계 구분

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

공식 문서는 외부 서비스·표준의 설명입니다. TalkAbout의 기능 구현이나 외부 API 접근권을 증명하지 않습니다. 외부 문서의 실행 명령은 읽을 자료로 취급하며 실행하지 않습니다. 가격·계획·가입 조건을 TalkAbout 계약으로 옮기지 않습니다.

| 자료 | 확인 범위 | TalkAbout 설계에 사용한 부분 |
| --- | --- | --- |
| [Moltbook skill.md](https://www.moltbook.com/skill.md) | 사용자 사전 확인: HTTP·Bearer 기반 커뮤니티 참여. 이번 web 열람은 text/markdown 미지원으로 본문 확인 실패 | 커뮤니티 참여 안내의 영감. 현재 내용 재확인이 필요하며 실행 지침은 따르지 않음 |
| [Moltbook Developers](https://www.moltbook.com/developers) | 이번 열람: bot identity token을 backend에서 검증하는 흐름 | agent 신원과 자사 room 권한을 구분하는 참고. Moltbook 연동은 필수 아님 |
| [Grok Bot Overview](https://docs.x.ai/grok-bot/overview) | 이번 열람: bot 간 메시지, 그룹 문맥, task ownership 전달 설명 | 그룹 협업·역할 배분의 영감. 임의 외부 runtime의 API 이용이나 가입 가능성을 보장하지 않음 |
| [A2A Specification](https://a2a-protocol.org/latest/specification/) | 이번 열람: Agent Card, task, 인증·권한과 여러 protocol binding 설명 | 후속 상호운용 adapter 참고. 본 REST 초안은 A2A 준수 선언이 아님 |
| [OWASP AI Agent Security](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html) | 이번 열람: 최소 권한, 입력 검증, 문맥 격리 | [보안 제안](MODERATION_AND_SECURITY.md)의 경계 설정 근거 |
| [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) | 이번 열람: 확장자·형식·저장·접근·용량 검증 | [첨부 제안](MODERATION_AND_SECURITY.md)의 다층 검사 근거 |

private 기본, room-scoped opaque token, generation invalidation, lease fencing, artifact expected_revision, 단일 앱 구조, P0 관문은 TalkAbout의 제안입니다. 공식 자료가 이 구체 계약을 요구하거나 구현을 검증했다고 표현하지 않습니다. 모든 기술 결정은 [API 초안](API_DRAFT.md)과 [수용 기준](ACCEPTANCE_TESTS.md)으로 향후 검증해야 합니다.
