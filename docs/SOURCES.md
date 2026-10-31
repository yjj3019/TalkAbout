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

## PostgreSQL 공식 근거

2026-10-13 공식 문서를 확인했습니다. [LISTEN](https://www.postgresql.org/docs/current/sql-listen.html)은 등록이 commit 시 유효해지고 등록 commit 뒤 최초 DB 조회가 필요함을 설명합니다. [NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html)는 commit과 notification 전달, table 재조회의 사용 패턴을 설명합니다. [Sequence functions](https://www.postgresql.org/docs/current/functions-sequence.html)는 nextval 값이 transaction rollback 시 회수되지 않는다고 설명합니다. 이 사실에서 Event를 영속 기준으로 두고 vector cursor를 쓰는 [아키텍처 제안](ARCHITECTURE.md)을 도출했습니다. LISTEN/NOTIFY를 내구 event queue나 global commit cursor로 설명하지 않습니다.

private 기본, agent별 opaque credential과 membership ACL 교집합, generation/membership_revision fencing, artifact expected_revision, TurnScheduler와 reference worker 분리, P0-A/P0-B 관문은 TalkAbout의 제안입니다. 공식 자료가 이 구체 계약을 요구하거나 구현을 검증했다고 표현하지 않습니다. [API](API_DRAFT.md)·[worker](REFERENCE_WORKER.md)·[수용 기준](ACCEPTANCE_TESTS.md)으로 향후 검증해야 합니다.

## 2026-10-31 검토 저장소 (설계 참고만 · 코드 미포함)

에이전트 상주형 제품 7개를 고정 revision에서 **정적 분석**했습니다. 아래 저장소의 코드·문장·asset은 TalkAbout에 **포함하지 않았고**, 설계 패턴과 경계 관찰만 참고했습니다.
상세 판정과 후보는 [2차 분석](../analysis/repo-review-2026-10-31/README.md)에 있습니다.

| 저장소 | 고정 revision | 라이선스 | 사용 방식 |
| --- | --- | --- | --- |
| [oraios/serena](https://github.com/oraios/serena) | `ede329aaf0c824bea75455a2926f01420d7aa620` | GPL-3.0-or-later(앱) + MIT(SolidLSP) | **코드 도입 불가**. 도구 descriptor·경로 봉쇄 설계만 참고 |
| [openai/codex](https://github.com/openai/codex) | `67f0994b532b8740f14b5b738af7b7b2a915b1d6` | Apache-2.0 | exec item schema·승인 응답 계약 관찰. 코드 미포함 |
| [nightly-labs/openbot](https://github.com/nightly-labs/openbot) | `fde3251234ed63a584b20321373ee34d5b01532a` | PolyForm Noncommercial 1.0.0 | **코드 도입 불가**. 보류 사유·자원 직렬화 설계만 참고 |
| [elie222/rakazo](https://github.com/elie222/rakazo) | `1ab3a840ace0a7b16d395db53090549dc4c705e3` | Apache-2.0 | run 상태·taint 승인 설계만 참고 |
| [Anil-matcha/open-duet](https://github.com/Anil-matcha/open-duet) | `87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd` | Apache-2.0 | 진단·승인 3분리 설계만 참고 |
| [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) | `8c663d2b7647dcea04a2278dd0f794f591ae95e0` | Apache-2.0 | placement·격리 계약 설계만 참고 |
| [HarnessMD/munder-difflin](https://github.com/HarnessMD/munder-difflin) | `f797048cc88ca2ff94bfc6c8c93b16f47a201f15` | MIT(코드) + LimeZu 아트 에셋 별도 | 경로 containment·관찰 실패 규칙만 참고. **asset 미사용** |

- 이 표는 **라이선스 준수 기록**이며, 어떤 저장소와도 제휴·보증 관계가 없습니다.
- 실제 빌드·테스트·설치·VM 실행은 하지 않았습니다(NOTRUN).
