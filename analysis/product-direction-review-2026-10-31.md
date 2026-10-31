# 첨부 조사 정밀 분석 — 가상 오피스 · IDE · 에이전트 오케스트레이션

**기준일 2026-10-31 · TalkAbout 기준 commit `98248cac1195ac7fd2117a01ffd5a61e2a06998f` · README/GitHub API 정적 확인 · 설치·실행·전체 소스 검증 NOTRUN**

이 문서는 사용자가 첨부한 저장소 조사 보고서를 **비판적으로 검증**한 결과다. 첨부는 방향성과 후보 목록은 유용하지만,
**라이선스 표·활동 상태·TalkAbout 기존 자산과의 중복**이 빠져 있어 그대로 실행하면 위험하다. 아래는 실측 표와 교정, 그리고 TalkAbout에 실제로 흡수할 순서다.

## 결론 (한 문단)

첨부의 핵심 통찰 — **"화면(IDE·가상 오피스)과 에이전트 실행 엔진을 분리하고, 같은 상태를 두 화면에 투영한다"** — 는 옳고 TalkAbout의 기존 구조와도 일치한다.
그러나 첨부는 (1) **라이선스 위험을 거의 검증하지 않았고**(The Cog는 라이선스 파일이 없고, DeskRPG·AgentRoom은 custom, Claude Squad는 AGPL, office-for-claude-agents는 일부 비상업, codex-acp는 archived), (2) **TalkAbout이 이미 가진 엔진·권한·증거·공간 UI를 신규 설계로 중복**하며, (3) 권한·승인·불확실성·멱등성 같은 실행 안전 계층이 아키텍처 그림에서 빠져 있다.
따라서 새 런타임/IDE를 통째로 만들지 말고 **① 코드 리뷰 표면(patch diff·승인·근거) → ② 로컬 worktree runner 옵션 → ③ A2 실행 DAG → ④ ACP adapter 설계** 순으로 흡수하는 것이 맞다. 3D 오피스 교체와 저장소 코드 혼합은 후순위/금지다.

### 후속 확인 — PR #54 병합 이후

기준 commit 이후 `81be1a2`에서 PR #54가 병합되었다. 아래 §3·§5는 **기준 commit 당시의 판단**이며, 코드 리뷰 표면을 현재도 전혀 없다고 해석하면 안 된다. 현재 patch 버전은 텍스트 기반 diff 표시, 입력 참조 증거 구분, 기존 토론으로 이동, 기존 revision/fence를 이용한 사람의 채택을 제공한다([구현](../talkabout/web/app.mjs), [합성 DOM 테스트](../tests/test_artifact_review_ui.mjs)).

이는 코드 편집기, 버전별 구조화 코멘트, 별도 리뷰 승인 워크플로 또는 patch 자동 적용이 아니다. 토론 이동은 메시지를 자동 작성하거나 게시하지 않는다. 브라우저 실사용과 실제 provider를 통한 해당 흐름은 이 후속 확인에서 실행하지 않았다. 다음 후보는 §5의 로컬 worktree runner이나, 구현 전 기존 work profile 경계와 생성·정리·실패 복구 계약을 검토해야 한다. worktree는 sandbox가 아니며 실제 provider 실행은 별도 동의·예산이 필요하다.

## 1. 검증 결과 — 첨부 주장 교정표

GitHub API(2026-10-31 조회)와 README/라이선스 파일을 정적으로 확인했다. `stars`·`pushed_at`은 스냅샷이며 품질 지표가 아니다.

| 저장소 | 첨부 주장 | 실측 | 판정 |
|---|---|---|---|
| Claw-Empire | 픽셀 오피스에서 Claude Code·Codex·Gemini 등 조직화, 1순위 | **Apache-2.0**, README상 CLI/OAuth/API provider·가상 회사, 다국어. **마지막 push 2026-03-16 → 약 7개월 정체** | 형태 참고는 유효, 유지보수 최신성은 주의 |
| The Cog | IDE형 다중 에이전트·메시지, 2순위 | **라이선스 파일 없음**(API license 비어 있음), stars 20, 마지막 push 2026-07-29 | **코드 사용 불가(기본 all-rights-reserved)**. 아이디어 참고만 |
| DeskRPG | 3D 사무실 + Hermes 실행 엔진 | **license NOASSERTION**(root `LICENSE.md` custom 추정), stars 198, 2026-10-19 활동 | 라이선스 원문 확인 전 도입 금지 |
| Agent Orchestrator | 작업별 브랜치·worktree·CI·리뷰 | **Apache-2.0**, stars 13.9k, 2026-10-31 활발. 데스크톱·모바일, 35+ agent | 첨부 주장 대체로 정확. 참고 가치 높음 |
| ChatDev | 역할 대화, 2.0과 1.0 구분 | **Apache-2.0**, branches: `chatdev1.0`·`main`·`macnet`·`puppeteer` | **브랜치 구분 주장 정확** |
| Vibe Kanban | README에 종료 예정 | **Apache-2.0**, README "Vibe Kanban is sunsetting." (repo는 아직 archived 아님) | 주장 정확, 채택 기반으로 부적합 |
| Claude Squad | tmux 기반, AGPL 주의 | **AGPL-3.0**, stars 8.7k | **주장 정확**. 네트워크 결합 시 소스 공개 위험 |
| Theia | 확장형 IDE 프레임워크 | **EPL-2.0**(root에 `LICENSE-EPL`, `LICENSE-GPL-2.0-ONLY-CLASSPATH-EXCEPTION`, MIT·vscode 라이선스 병존), stars 21.7k | "GPL 계열" 단정보다 **EPL-2.0/GPL-2.0-CP 이중**으로 표기해야 정확 |
| Zed | 협업 편집기, 구성요소별 라이선스 주의 | **NOASSERTION**이지만 root `LICENSE-APACHE`·`LICENSE-GPL` 병존, stars 92k | 주의 주장 정확. crate별 확인 필요 |
| code-server | 브라우저 VS Code 참고 | MIT, stars 79.7k | 정확 |
| Pixel Agents | VS Code 픽셀 상태 시각화 | MIT, stars 10k | 정확 |
| Claude Office | Claude Code 상태를 오피스로 시각화 | MIT, stars 595 | 정확 |
| Agent Virtual Office | 여러 agent 상태 한 화면 | MIT, stars 63 | 정확(소규모) |
| AgentRoom | 세션 모니터링 | **NOASSERTION**, stars 52 | 라이선스 확인 전 금지 |
| pixel-agent-office(pruthiviraj) | 픽셀 오피스+실행 | MIT, stars 1 | 정확하나 매우 소규모 |
| pixel-agent-office(vmayfuture) | 로컬 협업 환경 | MIT, stars 3 | 정확 |
| AI Town | 가상 세계·캐릭터·기억 | MIT, stars 10.7k | 정확 |
| Generative Agents | 기억·계획·행동 연구 | Apache-2.0, stars 22.3k, 2024-08 이후 정체 | 정확 |
| office-for-claude-agents | 일부 파일 비상업 조건 | **혼합 라이선스**: upstream/파생은 MIT, 명시된 저자 추가분은 **Source-Available Noncommercial 1.0** | **주의 주장 정확**. 파일별 확인 필수 |
| MetaGPT | 역할 기반 소프트웨어 회사 | MIT, stars 71k, 2026-01 이후 정체 | 정확 |
| Microsoft Agent Framework | 순차·병렬·인계·그룹, 상태 저장 | MIT, stars 14.4k, 활발 | 정확 |
| AutoGen | 유지보수 모드, 후속 안내 | MIT, **archived**. README: production 비권장, MAF로 이관 | **주장 정확** |
| CrewAI | 역할 팀·이벤트 흐름 | MIT, stars 60k, 활발 | 정확 |
| LangGraph | 상태 유지·human-in-the-loop | MIT, stars 44k, 활발 | 정확 |
| OpenHands Software Agent SDK | agent/tool/workspace/server 분리 | MIT, stars 1.3k, 활발 | 정확 |
| Symphony | 이슈 기반 실행, 신뢰 환경 초기 구현 | **Apache-2.0**, stars 27.9k. README 경고: "low-key engineering preview for testing in trusted environments" | **주장 정확** |
| ACP(agent-client-protocol) | 에디터↔에이전트 공통 통신 | Apache-2.0, stars 4.5k | 정확 |
| ACP Registry | ACP agent 목록 | Apache-2.0, stars 444 | 정확 |
| Claude ACP Adapter | Claude→ACP 사례 | Apache-2.0, stars 2.7k | 정확 |
| Codex ACP Adapter | Codex→ACP 사례 | **NOASSERTION + ARCHIVED(2026-07-22)**, stars 871 | **첨부의 adapter 예시로는 부적합**. 현재 대체 경로 확인 필요 |
| VibeTree | worktree·터미널·변경사항 | MIT, stars 272 | 정확 |
| Agetor | worktree·로컬 칸반 | MIT, stars 108, 활발 | 정확 |
| Ordewell | 목표→계획→할당 | Apache-2.0, stars 208, 활발 | 정확 |
| conduct | 터미널 병렬 worktree | MIT, stars 0 | 정확하나 사실상 무사용 |

**첨부의 자기 한계 고지("설치·실행·전체 소스 미검증")는 정직하지만, 그 결과로 (a) 라이선스 미확인 저장소를 2순위로 추천하고, (b) 고정 revision이 없어 재현이 불가능하다.** 이 문서는 그 공백을 실측으로 메운다.

## 2. 가장 중요한 교정 — 라이선스

| 위험 등급 | 저장소 | 의미 |
|---|---|---|
| **사용 불가(라이선스 없음)** | The Cog | 라이선스 파일이 없으면 기본적으로 **모든 권리 유보**. 코드 복사·수정·배포 불가 |
| **원문 확인 전 금지(custom)** | DeskRPG, AgentRoom, codex-acp | GitHub 분류 NOASSERTION. custom 조건을 읽기 전에는 어떤 사용도 단정 불가 |
| **강한 copyleft** | Claude Squad(AGPL-3.0) | 서버/네트워크 서비스에 결합하면 소스 공개 의무. TalkAbout(MIT)에 코드 혼합 부적합 |
| **약한/이중 copyleft** | Theia(EPL-2.0 또는 GPL-2.0-CP), Zed(Apache+GPL 이중) | 프레임워크/에디터 코드를 가져올 때 구성요소별 조건 선택·고지 필요 |
| **부분 비상업** | office-for-claude-agents | 표시된 추가분은 비상업. 상업 배포 시 파일 단위 검토 |
| **비교적 안전(Apache/MIT)** | Claw-Empire, Agent Orchestrator, ChatDev, MetaGPT, MAF, CrewAI, LangGraph, OpenHands SDK, Symphony, ACP 3종, VibeTree, Agetor, Ordewell, AI Town, Pixel Agents 등 | 그래도 TalkAbout 원칙은 **코드 복사 대신 독립 구현**. [독립 구현 정책](../docs/CLEAN_ROOM_IMPLEMENTATION.md) |

**첨부의 "Claw-Empire + The Cog + Agent Orchestrator + ChatDev 조합"은 코드를 합치자는 뜻이 아니라 해석해야 한다.** The Cog는 라이선스가 없어 **코드 기준 참여 자체가 불가**하고, 나머지도 설계 참고만 해야 한다.

## 3. TalkAbout 현 자산 대비 — 기준 commit 당시

이 표는 `98248ca` 기준이다. PR #54 이후의 리뷰 표면은 위 후속 확인을 따른다.

| 첨부가 말하는 구성요소 | TalkAbout 현 상태 | 판단 |
|---|---|---|
| 가상 오피스(캐릭터·공간) | **이미 있음**: 아이소메트릭 방 + 카메라 3단 + 방 목록 평면도([docs/UI.md](../docs/UI.md)) | 새로 만들 필요 없음. 3D 교체는 후순위 |
| 방·참여자·초대·연결 | 있음(identity·membership·초대 도우미 `connect-agent.ps1`) | 중복 금지 |
| 작업 배분·상태 | 있음(room mode·task·reservation·start·uncertain·관찰) | 첨부보다 **더 강한 실행 계약** 보유 |
| 작업 계획(역할·단계) | 비실행 계획 초안(A2)까지 구현 | 실행 DAG는 **미구현·다음 후보** |
| IDE/코드 편집·diff 리뷰 | **없음** | 가장 가까운 실질 격차 |
| worktree 기반 에이전트별 작업 분리 | **없음**(provider workspace만) | runner 옵션 후보 |
| ACP 공통 연결 | **없음**(MCP 최소 stdio adapter만) | 설계 후보. ACP≠MCP≠오케스트레이션 |
| 권한·승인·근거·불확실성 | 있음(ACL·fence·approval 방향·evidence·no-retry) | 첨부 그림의 **누락분** |
| 다중 에이전트 회의/역할 모델 | room mode·phase로 부분 구현 | ChatDev식 역할 확장은 필요성 검증 후 |

즉 첨부의 아키텍처는 TalkAbout과 **반대 방향이 아니라 같은 방향의 부분집합**이다. TalkAbout은 이미 "화면=상태 투영" 구조와 실행 안전 계층을 갖고 있고, 없는 것은 **IDE/리뷰 표면·worktree·ACP·실행 DAG**다.

## 4. 첨부 아키텍처 그림의 결함

1. **권한/승인 계층이 없다.** "공통 이벤트·상태"만으로는 누가 어떤 작업을 승인했는지·fence·멱등성이 표현되지 않는다. TalkAbout은 이 계층을 이미 별도로 갖고 있으며, 첨부 그림대로 합치면 다시 허술해진다.
2. **화면과 상태의 단일 출처가 불명확하다.** IDE와 오피스가 각자 상태를 만들면 불일치가 발생한다. 화면은 **서버 권위 상태의 projection**이어야 한다(TalkAbout의 A4 관찰 계약과 동일).
3. **worktree는 격리가 아니다.** 첨부도 마지막에 언급했지만, 그림에서는 worktree가 보안 경계처럼 보인다. 파일·네트워크·credential·OS 권한은 별도 설계다(TalkAbout `WORK_PROFILE.md`의 "sandbox 아님" 유지).
4. **"3D를 나중에"는 옳지만, 3D 교체 비용을 과소평가.** TalkAbout은 이미 2D 아이소메트릭 stage가 있으므로 3D는 제품 차별화가 아니라 표현 교체다. 우선순위 최하.
5. **provider 연결 계층이 ACP로 단순화되지 않는다.** ACP는 에디터↔에이전트, MCP는 에이전트↔도구, 오케스트레이션은 별개. 첨부도 이 구분은 맞게 적었다.

## 5. 권고 도입 순서 (TalkAbout 기준)

| 순서 | 항목 | 이유 | gate |
|---|---|---|---|
| 0 | 계약 문서: "화면=투영", "worktree≠sandbox", "ACP≠MCP≠오케스트레이션", 저장소별 라이선스 표 | 오해 방지, 비용 0 | 링크·문구 리뷰 |
| 1 | **코드 리뷰 표면**: patch artifact의 diff 뷰 + 코멘트/승인 + evidence 배지 | 첨부의 IDE 그룹이 실제로 제공하는 가치는 "편집기"보다 **변경 검토**다. TalkAbout은 patch를 이미 보관·채택하므로 가장 짧은 경로 | 서버는 diff 적용 안 함, 사람 승인, ACL, 합성 DOM 테스트 |
| 2 | **로컬 worktree runner 옵션**: work profile workspace를 worktree로 생성·정리, branch/base/충돌 표시 | Agent Orchestrator·VibeTree·Agetor의 공통 교훈. 단, 격리 아님을 명시 | local-only, 정리·실패 복구, sandbox 주장 금지 |
| 3 | **A2 실행 DAG**: 계획 노드 실행(별도 승인)·의존성·실패/uncertain 전파 | 계획 초안(A2)은 이미 있음. 첨부의 "회의→작업"을 실제 실행으로 연결하는 지점 | approval·fence·no-retry, 예산 승인 |
| 4 | **ACP adapter 설계**: provider CLI 연결 공통화 | 로컬 에이전트 연결 코드 중복 감소. 단 codex-acp는 archived/custom이므로 독립 구현 또는 대체 경로 | ACP는 인증·권한을 대신하지 않음 |
| 5 | 역할/회의 모델 고도화(ChatDev식) | 필요성이 검증된 뒤 | 기존 mode/phase와 충돌 없이 |
| 6 | 3D 오피스 교체 | 표현 교체, 가치 낮음 | 후순위 |

**첨부의 "첫 시나리오"를 TalkAbout식으로 다시 쓰면:** 요청 → 계획 초안 → human 승인 → 로컬 runner가 worktree에서 수정 → patch artifact + evidence → 두 번째 agent가 리뷰 task에서 검토 → human adopt/reject → (선택) 머지. 실제 provider 실행은 별도 예산·동의 후, 합성으로 먼저 검증.

## 6. 그대로 채택하면 안 되는 것

- **The Cog 코드**: 라이선스 없음. 설계 아이디어만.
- **Claw-Empire·DeskRPG·AgentRoom 코드 혼합**: Apache라도 제품 형태·UI·asset을 그대로 합치는 것은 독립 구현 원칙과 충돌. DeskRPG/AgentRoom은 라이선스 원문 확인 전 금지.
- **Claude Squad(AGPL)·office NC·Theia/Zed 코드**: copyleft/비상업/구성요소 조건 때문에 TalkAbout 코드베이스에 혼합 부적합.
- **"3D부터 시작"**: 이미 stage가 있고, 3D는 표현 교체다.
- **worktree를 보안 경계로 표기**: TalkAbout은 계속 "sandbox 아님"이어야 한다.
- **첨부 그림 그대로의 "공통 이벤트·상태" 단일 계층**: 권한·승인·fence·멱등성·불확실성 계층을 빼면 회귀한다.

## 7. 검증 경계 (NOTRUN)

- 외부 저장소 설치·빌드·실행·전체 소스 감사·성능/보안 평가는 **NOTRUN**. README·API 메타데이터·라이선스 파일만 정적 확인했다.
- The Cog(라이선스 없음), DeskRPG·AgentRoom·codex-acp(custom/NOASSERTION)의 라이선스 원문은 **미확인**. 도입 전 파일 단위 확인이 필요하다.
- stars·pushed_at은 2026-10-31 스냅샷이며, "정체/활발" 판정은 이 시점 비교일 뿐 유지보수 보장이 아니다.
- 첨부의 기능 세부(예: The Cog 25+ MCP tools, 3DS client, DeskRPG Hermes 연동)는 **README 주장**이며 이 문서가 독립 검증하지 않았다.

## 8. 참고

- 외부 저장소의 코드를 쓰지 않고 기능을 재구현하는 절차·기록: [독립 구현 정책](../docs/CLEAN_ROOM_IMPLEMENTATION.md)
- TalkAbout의 공간 UI 규칙: [UI.md](../docs/UI.md)
- 작업 도구·격리 경계: [WORK_PROFILE.md](../docs/WORK_PROFILE.md), [TOOL_BROKER_PLAN.md](../docs/TOOL_BROKER_PLAN.md)
- 2차 저장소 분석(설계 참고): [repo-review-2026-10-31](repo-review-2026-10-31/README.md)
