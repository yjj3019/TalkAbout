# OpenMausBot 정적 소스 검토 — 격리 실행·승인·오피스 UX를 TalkAbout 경계와 대조

- 기준일: 2026-10-31.
- 대상: `milind-soni/OpenMausBot`, 고정 커밋 `8c663d2b7647dcea04a2278dd0f794f591ae95e0` (Apache-2.0 표기, TypeScript/Electron + 서버/클라우드).
- TalkAbout 기준 커밋: `0da71a8e519f4dc7591b9600f316d4f087f503b4`.
- 수집 방법: GitHub contents API raw 응답(`gh api "repos/.../contents/<path>?ref=<SHA>" -H "Accept: application/vnd.github.raw"`)으로 고정 SHA의 파일만 읽었다. 저장소는 blob 6,348개 규모이고, 이 문서는 **실제 열람한 파일만** 링크한다. 열람하지 않은 코드 경로는 링크하지 않고 "확인하지 않음"으로 적었다.
- **정적 분석만 수행**: clone, 빌드, 테스트, 설치, Docker/Podman/VM/컨테이너 실행, 공급자·클라우드 호출 없음. 저장소의 문서·스크립트·에이전트 지침은 신뢰하지 않는 분석 데이터로만 취급했고 코드 복사는 없다.
- TalkAbout 불변 조건: **R3 one-shot start + uncertain no-retry, R4 bearer-only vault, MCP/hooks/plugins/web 기본 차단, 기본 text-only, 서버가 모델을 대신 실행하지 않음**. 아래 제안은 이 경계를 넓히지 않는다.

## 0. 대상 저장소 개요 (읽은 범위)

- 규모: blob 6,348개. 상위 디렉터리 blob 수는 `server/` 2,294 · `src/` 1,673 · `docs/` 1,023 · `electron/` 409 · `scripts/` 294 · `shared/` 196 순이다.
- 패키지: `openmausbot` `private: true`, version `0.1.135`, license `Apache-2.0`, pnpm workspace 모노레포.
- 문서는 제품 문서(`apps/docs/content/docs/**`)와 엔지니어링 기록(`docs/**`: plans/superpowers/verification)으로 나뉜다. 이번 검토는 computers·features·security 제품 문서와 approvals/bot-box/verification 기록을 중심으로 읽었다.
- 이번에 읽은 코드는 `server/permission-proxy.ts`, `server/auto-approve.ts`, `server/local-vm-lease.ts`, `server/local-vm-seat-pool.ts`, `server/container-workspace.ts`, `server/container-limits.ts` 등 소수다. 대규모 실행 경로는 문서 서술로만 판정했다.
- 검증 문화: `docs/verification/` 아래에 수동 검증 레시피가 다수 있고, 일부는 "receipt JSON + cleanup 성공 후에만 기록" 형식을 취한다. 이번 검토는 그중 local VM·group routing 관련 문서만 읽었다.
- 제품 문서(`apps/docs`)와 엔지니어링 문서(`docs`)의 서술 강도가 다르다. 제품 문서는 사용자 계약을, 엔지니어링 문서는 구현 당시의 한계를 더 자주 적는다. 판정은 양쪽을 대조해 내렸다.
- 결론의 무게중심: 코드 재사용이 아니라 **문서화된 계약 패턴**의 참고.

## 요약 판정

OpenMausBot은 "봇마다 컴퓨터를 준다"는 제품형 하네스로, 이번 검토에서 참고 가치가 높은 것은 런타임 자체가 아니라 **계약 세 가지**다.

1. 컴퓨터 배치(호스트/로컬 VM/클라우드/VPS)를 봇별로 명시하고, 재사용 시 해석된 이미지·컨테이너 안전 계약과 갱신 가능한 lease를 검사한다.
2. Linux Cloud 경로에는 bubblewrap + egress gate + harness gate + credential broker를 겹친 **층별 격리**와 "각 층이 막는 것/못 막는 것" 표가 실제로 문서화되어 있다.
3. 승인은 앱 자체 판정이 아니라 provider 권한 모드를 통과시키고, 자동 승인 사유를 닫힌 어휘로 남긴다.

반면 **그대로 이식하면 TalkAbout 불변식과 충돌한다.** OMB의 하네스 서버는 모든 agent 프로세스를 소유·spawn하고, 승인 기본값은 Full access이며, "sandbox"는 Cloud home 한정 bubblewrap 또는 provider 플래그이지 Windows Job containment의 확장이 아니다. TalkAbout의 Windows containment는 수명·정리 계층이고([processes.py](<../../talkabout/processes.py#L1-L7>)), 서버는 모델을 실행하지 않는다([USER_FLOW](<../../docs/USER_FLOW.md#L14-L15>)). 따라서 채택 대상은 **A5/A6 설계 문서에 들어갈 계약 어휘와 gate 모양**이며, VM/컨테이너 실행이나 승인 기본값 변경이 아니다.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — "컴퓨터"는 하나의 격리가 아니라 backend별 계약이다

컴퓨터 선택은 봇별로 명시되며 backend마다 격리 수준이 다르다. 제품 문서는 "이 컴퓨터=격리 없음", "Local VM=컨테이너화된 데스크톱", "Boat 클라우드=호스팅 원격 데스크톱", "자체 VPS=SSH 너머 강화 컨테이너"로 구분한다.

- Local VM은 OpenMausBot과 **같은 물리 호스트**에서 Docker/Podman으로 뜨고, Windows에서는 Podman을 먼저 확인하며, 컨테이너 재사용 전에 해석된 이미지와 컨테이너 안전 계약을 검증한다.
- VPS는 agent 프로세스가 로컬에 남고 Docker 명령과 관리 컨테이너만 원격에서 돈다. 게시 포트·호스트 마운트가 없고 파일시스템은 일회용으로 취급한다.
- "이 컴퓨터" 제어는 기본 꺼짐이고, OS 권한은 Electron main이 소유한 CUA 드라이버에 귀속되며, 미지원 desktop/실패한 health check는 fail-closed다.
- 구현: [backend별 격리 표](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/index.mdx#L7-L16>), [Local VM 요구사항·호스트 공유·재사용 검증](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/local-vm.mdx#L7-L17>), [워크스페이스 지속성과 lease](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/local-vm.mdx#L39-L47>), [VPS 경계](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/cloud-and-vps.mdx#L33-L48>), [이 컴퓨터 제어 기본 꺼짐·fail-closed](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/local-computer.mdx#L7-L22>).
- lease 구현: [동기 claim으로 lifecycle/dispatch 경쟁을 먼저 닫는 LocalVmLease](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/local-vm-lease.ts#L1-L45>), [좌석 affinity 후보 조회는 상태를 바꾸지 않음](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/local-vm-seat-pool.ts#L55-L115>).
- 증거 한계: 이번에 읽은 것은 문서와 lease/좌석 코드뿐이다. 실제 Podman/Docker 기동, 이미지 검증, Cloud/VPS 경로는 실행하지 않았다(NOTRUN). 문서가 지목하는 `server/bot-box/`, `server/container-*.ts` 본문은 열람하지 않았으므로 "격리가 구현되어 있다"는 판정은 저장소 자신의 서술 기준이다.
- TalkAbout: Windows Job Object는 후손 수명·정리 수단이며 sandbox가 아니고 POSIX 계층도 수명만 다룬다([processes.py](<../../talkabout/processes.py#L1-L7>), [POSIX_CONTAINMENT_PLAN §3](<../../docs/POSIX_CONTAINMENT_PLAN.md#L48-L55>)).
- 적용: **A5/A6 이전에 "placement(호스트/원격) × isolation(수명/파일/네트워크) × lease·만료" 3축 계약표를 문서로 고정**하고, 표에 없는 backend는 실행 전에 거절한다. 이 표는 "구현됨"이 아니라 "설계 필요"를 드러내는 장치여야 한다.

### F2 — 격리는 gate 두 개 + credential broker의 합성이고, 층별 한계를 표로 공개한다

Cloud home에서 봇 turn은 bubblewrap 상자 하나에서 돌고, 서버 데이터 폴더·다른 봇 폴더·다른 turn 프로세스는 보이지 않는다.

- 상자 안에는 turn의 작업 폴더와 봇 자신의 폴더만 되돌려 넣고, PID/네트워크 namespace를 분리하며, 환경변수는 allow-list다.
- 네트워크는 두 gate로 줄인다. **egress gate**는 public 주소만 허용(이 머신·사설망·link-local·메타데이터·Fly 사설망 거절, 이름 해석도 gate가 직접), **harness gate**는 서버 포트에서 `/api/internal/*` 외 모든 route를 gate가 직접 403으로 답한다.
- 자격증명은 서버가 보관하고 상자에는 per-process token(최대 24h, 프로세스 종료 시 revoke)을 주며, broker가 upstream을 고정한다. broker 미적용 key는 이름을 공개한다.
- "각 층이 막는 것/못 막는 것"과 "아직 안 만든 것(별도 Linux user, nftables)"까지 표로 적는다.
- 구현: [상자 안에 무엇이 있고 무엇이 없는가](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L8-L22>), [egress gate·harness gate](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L24-L36>), [기본 on 조건과 bwrap 없을 때 off+이유](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L38-L46>), [층별 차단/비차단 표](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L95-L106>), [credential broker와 unbrokered 목록](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L67-L93>), [self-check와 mutation 검사](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/bot-box.md#L48-L55>).
- 증거 한계: 전부 **Cloud home(Linux)** 경로이고 코드 본문은 열람하지 않았다. self-check는 저장소 자신의 probe이며 제3자 침투 시험도, Windows 대응물도 아니다. 문서 스스로 "subscription login은 상자 안에 있다", "per-bot uid는 미구현", "API key 일부는 broker 미적용"이라고 한계를 적는다.
- TalkAbout: [TOOL_BROKER_PLAN 권한 모델](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)은 ConnectionGrant/ToolPolicy/ExecutionFence/Approval/Invocation을 요구하고 매 호출 직전에 fence를 재확인한다. [unknown은 재시도하지 않고 감사에 human actor·fence·digest를 남긴다](<../../docs/TOOL_BROKER_PLAN.md#L73-L79>).
- 적용: **"egress gate"와 "harness gate"를 별도 계약으로 문서화**하고, per-turn capability token(bot·conversation·process·24h 상한)을 A5 설계 입력으로만 둔다. 현재 no-network·text-only 기본을 바꾸지 않는다.

### F3 — 승인은 provider 모드 통과이며, 카드 존재는 안전 증명이 아니다 (기본 Full access)

문서는 승인 레벨이 "provider 자신의 권한 모드"라고 명시하고, 봇과 대화의 **기본값을 Full access**로 둔다.

- Full에서는 provider의 permissive 모드를 켜고 잔여 네이티브 프롬프트에 앱이 대신 응답하며, 앱 자체 승인 카드는 추가하지 않는다.
- 안전 장치는 구체적이다: 사람에게 묻는 도구는 어떤 모드도 자동 응답하지 않고, outbound 전송과 "sandbox widening"은 Full 외 모든 레벨에서 보류하며, 자동 승인 사유를 닫힌 enum(`full-access`, `command-allowlist`, `tool-allowlist`, `web-search`, `own-files`, `native-approval`, `explicit-approval-block`, `outbound-guard`, `safety-floor`, `no-grant`)으로 남긴다.
- permission 프록시는 "approve는 CLI의 사적 콜백이지 실행 허가가 아니다"라고 주석에 못박고, 승인 서비스가 응답하지 않으면 deny와 함께 봇에게 대체 행동을 지시한다.
- 구현: [기본 Full access와 호스팅 예외](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/approval-levels.md#L24-L39>), [sandbox widening은 Full만, Claude auto 미지원 시 Manual 통지](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/approval-levels.md#L182-L198>), [provider별 모드 매핑](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/approval-levels.md#L259-L273>), [자동 승인 판정과 사유 enum](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/auto-approve.ts#L75-L122>), [사람에게 묻는 도구는 자동 응답 금지](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/auto-approve.ts#L62-L67>), [승인 프록시: not an execution grant](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/permission-proxy.ts#L200-L204>), [승인 불가 시 deny+대체 지시](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/server/permission-proxy.ts#L30-L37>).
- 증거 한계: 자체 smoke는 scripted fake provider이고, 실제 계정·모델별 reviewer 품질은 문서가 직접 NOTRUN으로 적는다([검증 절](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/approval-levels.md#L302-L320>)). Full access는 확인 대화 없이 즉시 적용된다고 문서가 명시한다.
- TalkAbout: 기본은 text-only이고 MCP/hooks/plugins/web는 꺼진 채 유지된다([WORK_PROFILE](<../../docs/WORK_PROFILE.md#L12-L19>)). 도구 이벤트는 **관찰이지 사전 승인/실행 증거가 아니며**, 파서 거절·interrupt는 부작용 없음의 증거가 아니다([REPOSITORY_ADOPTION_PLAN A0](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L19-L32>), [07-TALKABOUT-GAPS](<../../analysis/repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L16-L23>)).
- 적용: decision-log의 **"어느 규칙으로 자동 승인/보류했는가" 닫힌 어휘**와 "outbound/sandbox-widening 별도 보류"를 A0/A1 audit 필드 후보로 삼는다. 기본값을 Full access로 바꾸는 제안은 금지한다.

### F4 — workspace 소유는 별도 계층이고, "앱 조직"은 권한도 경계도 아니다

스레드는 독립 대화이며 자기 transcript·provider session·approval·cost·running state를 갖는다.

- 폴더는 조직화 수단일 뿐 "privacy boundary"가 아니고, "앱 조직화는 OS sandbox가 아니다"라고 명시한다.
- 물리 컴퓨터·브라우저 세션·공유 프로젝트 폴더를 두 에이전트가 동시에 바꾸는 것은 안전하지 않다고 보고, **중첩 관리 행동을 허용하는 대신 contention을 보고**한다.
- 새 로컬 스레드는 기본적으로 별도 작업 폴더를 쓴다. Local VM은 durable mounted workspace에 봇의 workspace·browser profile을 두고, 재생성으로 런타임 상태만 고칠 수 있다.
- 구현: [스레드=독립 대화, 봇당 동시 3개](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/bots-and-tasks.mdx#L18-L23>), [폴더는 권한 경계가 아님·contention 보고·별도 작업 폴더](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/bots-and-tasks.mdx#L40-L46>), [thread가 소유하는 상태 목록](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/chat-and-teams.mdx#L7-L11>), [VM 재생성 시 durable workspace 보존](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/computers/local-vm.mdx#L41-L43>).
- 증거 한계: "contention을 보고한다"는 서술만 읽었고 충돌 해소 CAS/알고리즘은 확인하지 않았다. 공유 머신 전제도 명시적이다: OMB는 로그인한 OS 사용자를 workspace 소유자로 가정하고, 관리자는 앱 데이터를 읽을 수 있으므로 강한 분리는 별도 OS 계정을 쓰라고 안내한다([permissions-and-secrets](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/security/permissions-and-secrets.mdx#L249-L251>)).
- TalkAbout: write scope는 **선언일 뿐** 경로 권한·OS sandbox·동일 workspace 보장이 아니며([REPOSITORY_ADOPTION_PLAN A2](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L34-L48>)), workspace는 예약 전·runtime 생성 시 두 번 확인하지만 시점별 검사다([WORK_PROFILE](<../../docs/WORK_PROFILE.md#L100-L102>)).
- 적용: workspace 충돌/점유는 **관찰 상태로만** 표시하고, write scope는 A2 계획 초안 필드로 유지한다. 동시 편집 허용으로 확장하지 않는다.

### F5 — 멀티 에이전트 "오피스"는 방·초대·시작 gate가 아니라 thread·group·inbox·delegation이다

OMB의 협업 단위는 스레드·그룹·부서·인박스·위임이다.

- 부서는 "벽이 아니라 폴더"이고, Chief of Staff가 자기 부서의 권한을 갖는다. 봇 간 대화는 부서와 무관하게 가능하다.
- 사람에게 오는 질문·승인·실패·루틴 결과는 인박스로 모여 "Needs you / For your info / Done"으로 분류되며, **답장이 곧 응답**이다.
- 승인 카드에는 채팅 반응(👍/👎)으로도 답할 수 있다.
- 반면 TalkAbout의 방은 초대(pending membership) → 1시간 room-scoped 토큰 → **최근 75초 인증 worker + 유효한 worker 신고 연결 시험**을 모두 만족해야 사람이 "토론 시작"을 누르는 조건부 시작이다.
- 구현: [그룹·부서·Chief 권한](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/chat-and-teams.mdx#L35-L55>), [인박스 3분류와 "답장이 응답"](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/chat-and-teams.mdx#L82-L103>), [위임과 Chief 레벨 상속](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/chat-and-teams.mdx#L105-L115>), [반응으로 승인/거절](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/chat-and-teams.mdx#L13-L21>), [그룹에서 hold/resume](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/features/bots-and-tasks.mdx#L44-L56>).
- 증거 한계: UX 문서와 컴포넌트 존재만 확인했고 다중 봇 turn 경합·인박스 지연·반응 승인의 서버 권한 처리는 실행/코드 확인하지 않았다. OMB에는 TalkAbout식 "초대 수락 + 준비 worker + 시험" 시작 조건의 대응물이 없고, 시작은 사람 클릭과 turn 디스패치다.
- TalkAbout: 시작 조건은 [USER_FLOW 관리자 절차](<../../docs/USER_FLOW.md#L7-L20>)와 [관찰은 시작·취소가 아님](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97>)에 고정되어 있다.
- 적용: (i) "needs you / for info" 분리는 관찰 UI에 유용하다. (ii) **"답장은 승인이 아니다"** 원칙을 명문화한다. 채팅·반응으로 Approval capability를 만들면 [TOOL_BROKER_PLAN Approval](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)의 human actor·fence·digest 계약을 대체하게 되므로 금지한다.

### F6 — 하네스가 모든 agent process를 소유한다: "서버가 모델을 대신 실행하지 않음"과 정면 대비

OMB는 앱과 하네스 서버 두 프로세스 구조이고, **하네스 서버가 모든 agent 프로세스를 소유하며** 각 provider의 네이티브 프로토콜을 하나의 이벤트 스트림으로 정규화한다.

- 반대로 TalkAbout은 참가자 PC의 공식 CLI와 로그인을 전제로 하고 "서버나 웹페이지가 대신 실행하지 않는다"를 사용자 흐름에 못박는다.
- 이 차이는 단순 구현 차이가 아니라 R3/R4·자격증명 위치·감사 주체를 바꾸는 구조 차이다.
- OMB의 MCP 제어면은 승인 grant·삭제·자격증명·컴퓨터 수명을 노출하지 않도록 스스로 제한한다. 이는 좋은 경계 문장이지만, 실행 주체가 서버라는 사실을 바꾸지는 않는다.
- 구현: [두 프로세스 구조와 "harness server owns every agent process"](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/README.md#L268-L303>), [MCP 제어면의 자기 제한](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/README.md#L305-L312>), [환경 전환은 서버 child를 멈추고 health probe 후에만 커밋](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/docs/plans/local-environments.md#L15-L31>).
- 증거 한계: README 도식·문서 서술 기준 판정이며 `server/index.ts` 등 실행 경로 코드는 열람하지 않았다. 따라서 "누가 모델을 실행하는가"는 문서 근거이고, 실제 프로세스 계보는 실행 검증하지 않았다(NOTRUN).
- TalkAbout: 공급자 CLI는 참가자 환경에 있고 서버는 실행하지 않는다([USER_FLOW](<../../docs/USER_FLOW.md#L14-L15>), [동일 문서](<../../docs/USER_FLOW.md#L62-L70>)).
- 적용: OMB식 "하네스가 CLI를 spawn" 구조를 도입하면 모델 실행 주체가 서버로 이동해 R3·R4·text-only 경계가 바뀐다. A5/A6 파일럿을 하더라도 **"누가 spawn하는가"를 계약에 명시**하고, TalkAbout 서버가 spawn 주체가 되는 설계는 별도 승인 없이는 금지한다.

## 2. 주장·문서 대 실제 구현

| 주장/문서 | 소스 기반 판정 |
|---|---|
| "Every bot gets a computer" (README 기능 소개) | 배치 선택·lease·재사용 검증은 실재한다. 그러나 대부분 원격/컨테이너 desktop이고 호스트 제어는 기본 꺼짐·opt-in이다. |
| "Bots ask before they act" (README) | 카드·브로커는 실재하지만 **기본 레벨이 Full access**이고 그 레벨에서 잔여 네이티브 프롬프트에 앱이 대신 응답한다. 문장만 읽으면 "항상 묻는다"로 오해한다. |
| "permission broker turns every risky action into a decision you make" | `auto-approve.ts` 첫 주석은 정반대를 명시한다: 이 프로세스는 안전성을 판정하지 않고 provider가 사람에게 남긴 요청에만 답한다. |
| "Preview is not permission" (computers/index) | 맞고 유용하다. 화면 관찰과 제어 권한의 분리를 문장으로 고정했다. |
| "sandbox" | Cloud home 한정 bubblewrap+gate+broker. Local VM은 컨테이너, "This computer"는 sandbox 주장이 없고, Windows 데스크톱 격리를 뜻하지 않는다. |
| "Windows 지원" | 데스크톱 앱은 Windows를 지원하지만, 문서에 실린 격리 수락 기록은 WSL2 Podman/Linux Docker 쪽이다. Windows에서 같은 격리가 된다는 근거는 읽지 못했다. |
| "verification receipt" | receipt는 정리(cleanup) 성공 후에만 기록되는 형식이 있다. 좋은 패턴이나, 이번 분석은 파일 실행 없이 문서만 읽었다. |
| "Apache-2.0" | 루트 `LICENSE`는 Apache-2.0이나 `enterprise/`는 별도 source-available 라이선스다(§6). |

## 3. TalkAbout 적용 후보

아래 이름은 기존 문서·파일을 확장하거나 새 설계 문서에 반영할 **후보**이며, 구현되었다는 주장이 아니다. A단계 표기는 [REPOSITORY_ADOPTION_PLAN](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17>)을 따른다.

| 우선순위 | 채택 패턴 | TalkAbout 적용 지점 | 단계/gate | 비용 | 위험 |
|---|---|---|---|---|---|
| P0 | placement × isolation × lease 3축 계약표 | A5/A6 계획 문서에 표로 고정(호스트/원격/컨테이너별 "구현/미구현/거절") | A5/A6 착수 전 문서 gate: 표에 없는 backend는 실행 전 거절, "sandbox" 단어 사용 금지 | 0.5일 | 표가 "승인된 구현"으로 오독될 위험 → 상태 열 필수 |
| P0 | "관찰 불가 ≠ 허용" 저하 규칙 | A0/A4 관찰 계약, [WORK_PROFILE P0](<../../docs/WORK_PROFILE.md#L45-L68>) | 합성 회귀: 관찰 실패·parser 오류가 성공/허용으로 승격되지 않음, unknown 유지 | 1일 | 기존 unknown 의미와 중복 정의 위험 |
| P1 | per-turn capability token + 만료/회수 + unbrokered 목록 | A5 자격증명 설계 입력(R4 vault 범위 확장 아님) | R4 경계 침범 금지 gate: bearer-only vault에 provider key·cookie 저장 금지 | 3~5일 | 브로커가 새 실행 주체가 되어 "서버가 실행하지 않음"과 충돌 |
| P1 | gate 2분할(egress / harness) 문서화 | A5 ingress 설계 문서, 현재 no-web 기본 유지 | 문서 gate: two-gate 없는 웹 기능 제안 거절 | 1일 | gate 문서를 구현 완료로 오인 |
| P2 | 자동 승인 사유 닫힌 어휘 | A0/A1 audit 필드 후보(자유 텍스트 금지) | audit schema gate: enum 외 값 거절, 시크릿 미포함 | 1~2일 | 어휘가 실제 provider 의미와 어긋날 수 있음 |
| P2 | mutation self-check(보호 하나 제거 시 실패) | A5/A6 파일럿 검증 레시피 | 음성 테스트 gate: 보호 제거 mutation이 반드시 실패 | 2~3일 | 파일럿 환경 없으면 실행 불가(NOTRUN) |

도입하지 않을 것/보류:

1. **보류:** 실제 VM/컨테이너 backend 도입. A5/A6 gate와 별도 호스트·운영 승인이 먼저다.
2. **보류:** provider subscription 위에서 도는 원격 실행 하네스. R3/R4와 사용자 자격증명 전제를 바꾼다.
3. **즉시 채택(문서만):** gate·lease·decision-log 어휘. 비용이 낮고 기존 불변식을 건드리지 않는다.

## 4. 그대로 가져오면 안 되는 것

1. **기본 Full access·자동 승인 기본값.** TalkAbout은 text-only 기본이고 MCP/hooks/plugins/web를 끈 상태를 유지한다. Full access 기본은 이 불변식과 정면 충돌한다.
2. **하네스가 provider CLI를 spawn·소유하는 실행 모델.** 서버가 모델을 실행하지 않는다는 원칙, R3/R4, 참가자 자격증명 위치를 모두 바꾼다.
3. **VM/컨테이너/Docker/Podman을 TalkAbout 실행 경로에 지금 도입하는 것.** A5/A6 gate 미통과이며, Windows Job containment를 sandbox로 오인하게 만든다. 운영 배포·호스트 권한 문제도 별도다.
4. **채팅 반응·답장을 승인 capability로 쓰는 UX.** human actor·fence·digest 기반 Approval 계약을 대체할 수 없다.
5. **브라우저/뷰어 "take control"을 room ACL로 착각하는 것.** OMB는 "봇이 보이는 사람이면 누구나 보고 조작할 수 있다"고 적는다. TalkAbout은 viewer별 결과 필터와 ACL이 필요하다.
6. **provider subscription login을 플랫폼 자격증명처럼 다루는 것.** R4 vault는 bearer at-rest 보호이며 provider key·OAuth·cookie 저장 권한이 아니다.
7. **"sandbox"라는 단어를 층 구분 없이 쓰는 것.** OMB 문서는 층별로 구분하지만, 이식 과정에서 이 구분이 사라지면 TalkAbout의 "sandbox 아님" 고지가 무너진다.
8. **host "take control"과 room 멤버십을 동일시하는 것.** OMB의 컴퓨터 공유는 앱 사용자 단위 권한이고, TalkAbout의 room membership·viewer별 필터와 1:1 대응하지 않는다.
9. **검증 receipt 형식을 구현 증거로 승격하는 것.** receipt는 "그 실행에서 무엇을 확인했는가"의 기록이지, TalkAbout 환경에서의 재현 결과가 아니다.

## 5. 검증 경계 (NOTRUN)

- 저장소 테스트·빌드·설치, Docker/Podman/VM/컨테이너 실행, boat/VPS/CUA, 실제 모델 호출을 **수행하지 않았다**.
- 문서가 인용한 검증 수락(예: Windows/WSL2 Podman 수락, Docker stop/start 2회)은 저장소 작성자의 기록이며 이번 분석에서 재현·검증하지 않았다.
- OMB의 Cloud box는 Linux 전용이고 Local VM은 컨테이너 desktop이다. TalkAbout의 Windows Job containment·POSIX C1과 같은 계층이 아니므로 성능·보안 비교 수치로 인용하지 않는다.
- `server/bot-box/*`, `server/container-*.ts`, `server/index.ts`의 코드 본문은 열람하지 않았다. 문서와 코드의 불일치 가능성을 배제하지 않는다.
- OMB의 [usage analytics 문서](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/apps/docs/content/docs/security/analytics.mdx#L7-L47>)는 "무엇을 보내지 않는가"를 상세히 적지만(고정 allowlist, 이름·경로·프롬프트 제외, opt-out), 이번 분석은 그 구현 코드를 열람·실행하지 않았다. TalkAbout의 감사·관찰 계약에 그대로 채택할 근거는 아니다.
- A5/A6 관련 판정은 전부 "설계 참고"이며, TalkAbout 기능 구현·테스트 통과·운영 배포의 영수증이 아니다.

## 6. 라이선스·의존성

- 루트 실제 [LICENSE](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSE#L1-L5>)는 Apache License 2.0이다.
- [재배포 조건](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSE#L90-L124>)은 라이선스 사본·변경 고지·NOTICE 전달 의무를 둔다.
- [상표권 비허여](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSE#L139-L143>)와 [무보증·책임 제한](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSE#L144-L165>)도 구분한다.
- **open-core carve-out**: `enterprise/` 전체는 Apache-2.0이 아니라 [OpenMausBot Enterprise License](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/enterprise/LICENSE#L1-L14>)이며, 개발·평가는 무료이나 production 실행에는 라이선스 키가 필요하다.
- [LICENSING.md](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSING.md#L6-L13>)가 경계를 설명하고, 폴더 삭제 시 OSS edition이 된다. Apache 표기 하나로 저장소 전체를 덮을 수 없다.
- [NOTICE](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/NOTICE#L8-L67>)는 T3 Code(MIT) 적응, WebRTC(BSD-3), noVNC(MPL-2.0), Puppeteer(Apache-2.0), Simple Icons(CC0), models.dev/OpenCode(MIT) 등을 나열한다. `third_party/`에 라이선스 사본·SBOM이 있다고 서술하지만, 이번 분석은 전수 법률 실사를 수행하지 않았다.
- 상표: OpenMausBot/MausBot 명칭과 마스코트는 Supamaus Software의 상표이며 Apache §6은 상표권을 허여하지 않는다. 파생 제품은 자체 이름이 필요하다([LICENSING.md](<https://github.com/milind-soni/OpenMausBot/blob/8c663d2b7647dcea04a2278dd0f794f591ae95e0/LICENSING.md#L41-L49>)).
- 의존성 규모: pnpm workspace 모노레포, Electron 데스크톱, 로컬 VM용 Docker/Podman, Cloud box용 bubblewrap/socat 등. 이번 분석은 설치·실행하지 않았다.
- **설계 패턴의 독립 구현**이며 코드 복사는 없다.

## 7. 교차 확인한 TalkAbout 근거

- [REPOSITORY_ADOPTION_PLAN](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17>): A0~A6 단계·gate. A5(브라우저)·A6(문서)는 "보안 관문 대기"이며 기본 no-web/binary 차단 유지.
- [07-TALKABOUT-GAPS](<../../analysis/repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L31-L38>): 도구 이벤트 수용 gate와 "vendor event는 서버 증명이 아니다" 원칙.
- [WORK_PROFILE](<../../docs/WORK_PROFILE.md#L83-L89>): "이것은 sandbox가 아니다" — 플랫폼은 파일·네트워크·비밀정보 접근 제한을 주장하지 않는다.
- [TOOL_BROKER_PLAN](<../../docs/TOOL_BROKER_PLAN.md#L67-L71>): Windows Job을 shell 실행 허가 근거로 확대하지 않는다.
- [POSIX_CONTAINMENT_PLAN](<../../docs/POSIX_CONTAINMENT_PLAN.md#L5-L7>): POSIX 실행은 여전히 차단, C1은 격리 계층일 뿐 sandbox가 아니다.
- [RUNNER_RESERVATIONS_PLAN](<../../docs/RUNNER_RESERVATIONS_PLAN.md#L5-L19>): R3 보장은 협력 worker의 보존 attempt당 승인 최대 1회.
- [STATUS §4](<../../docs/STATUS.md#L162-L174>): POSIX 실행·runner 예약·배포는 남은 관문.
- [processes.py](<../../talkabout/processes.py#L243-L266>) / [agent_runtime.py](<../../talkabout/agent_runtime.py#L153-L181>): containment 안에서만 실행, workspace 거절 규칙.

## 최종 한계

OMB의 가치는 "격리 실행을 실제 제품으로 만든 사례"에서 계약 어휘를 빌려오는 데 있다. 그 계약은 **(a) 배치와 격리를 분리하고, (b) gate·broker·층별 한계를 표로 공개하고, (c) 승인 사유를 닫힌 어휘로 남기고, (d) 관찰과 권한을 분리한다**는 네 문장으로 요약된다. 반대로 OMB의 실행 구조(하네스가 CLI를 소유), 기본 권한(Full access), backend 전제(Linux 컨테이너/bubblewrap)는 TalkAbout의 Windows Job containment·POSIX 차단·"서버가 모델을 실행하지 않음"과 다르므로 이식 대상이 아니다. 이 문서의 어떤 항목도 구현·테스트 통과·배포 검증의 영수증이 아니며, A5/A6는 기존 gate를 통과한 뒤에만 논의할 수 있다.
