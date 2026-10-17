# Opt-in tools and isolated agent workspaces

Status: **PROPOSED / NOT IMPLEMENTED**. Reviewed: **2026-10-17**.

이 상태는 실제 도구 연결과 실행 기능을 가리킵니다. 이번 브랜치에는 [내부 계약](INTERNAL_FOUNDATION_CONTRACT.md)에 따른 메모리 정책·승인·fake adapter만 추가합니다. 공개 API·GUI·runtime 연결과 실제 계정·명령 실행은 제공하지 않습니다.

이 문서는 결함 수정과 분리된 후속 설계다. 현재 text-only 프로필과 도구 실행 차단을 유지한다. 실제 계정 연결, 인증, 토큰 발급, 외부 쓰기, 명령 실행, 컨테이너 설정 변경은 이 문서로 활성화되지 않는다. 공개 API와 운영 DB 계약을 바꾸는 구현은 별도 계획 검토 후 진행한다.

## 목표와 경계

서로 다른 agent runtime이 방 안에서 자료를 교환하고, 명시적으로 허용된 도구로 작업하도록 한다. agent의 메시지·멘션·작업 인계는 기존 방 이벤트와 ACL을 이용한다. 메시지를 받은 사실이나 다른 agent의 요청은 도구 권한을 주지 않는다. 별도 임의 네트워크 통신 경로는 기본으로 열지 않는다.

공통 ToolBroker는 모델에 노출할 도구 목록과 실제 호출을 모두 통제한다. provider가 추가 도구·plugin·hook·shell을 직접 사용할 수 있다면 해당 runtime의 도구 프로필은 지원하지 않는다. provider의 자체 tool allowlist나 prompt만으로 플랫폼 권한 검증을 대체하지 않는다.

## 권한 모델

권한의 교집합은 사람 소유자의 승인, 연결 대상, agent 신원, 방 멤버십, 현재 task와 lease, 정책 revision, 호출 종류 및 자원 범위다. broker는 agent가 제출한 actor ID를 신뢰하지 않고 인증된 실행 주체에서 신원을 얻는다. 매 호출 직전에 최신 상태를 확인한다. pause·close·topic/membership 변경·lease 교체·정책 철회는 이전 승인을 무효화한다. 장시간 실행과 결과 제출 때도 fence를 다시 확인한다.

후속 내부 타입의 최소 초안은 다음과 같다. 아직 공개 REST 필드나 운영 테이블이 아니다.

| 내부 타입 | 주요 필드와 불변 조건 |
| --- | --- |
| ConnectionGrant | owner, connection ID, adapter 종류, 정확한 대상 allowlist, read/write 권한, 만료, 철회 revision. 비밀값 대신 별도 credential handle만 보유 |
| ToolPolicy | profile, policy revision, agent ID, room ID, 허용 tool/action/target, 시간·호출·결과 크기 한도. 기본 profile은 text_only이며 도구 목록이 비어 있음 |
| ExecutionFence | task ID, room generation, membership revision, attempt, lease 소유권과 만료, policy revision. 인증된 broker 내부에서 생성 |
| Approval | 실제 human actor, action과 대상·인자 digest, fence, 만료, 사용 여부. 채팅 메시지는 승인으로 간주하지 않음 |
| Invocation | invocation ID, request digest, fence, adapter, 상태, 승인 참조, 외부 결과 식별자 또는 unknown 이유. 같은 키에 다른 요청은 거절 |

다른 worker가 claim 응답을 공유하지 않게 하는 기존 수정과 별개로, 외부 도구 도입 전에는 서버가 발행·검증하는 명시적 lease 소유권 증명을 설계해야 한다. 현재 task attempt만으로 새로운 외부 부작용 권한을 만들지 않는다. 필요한 API/DB 변경, 기존 클라이언트 전환, 만료·회수·롤백 방안을 먼저 검토한다.

## 프로필과 도구별 분리

| 프로필/작업 | 범위와 승인 |
| --- | --- |
| text_only | 현재 기본값. 외부 도구와 shell 없음 |
| fake_tools | 테스트 전용 메모리 adapter. 네트워크·프로세스·계정 접근 없음 |
| Notion read | 승인된 workspace와 page/database 대상만. 읽기 권한과 결과 ACL 확인 |
| Notion write | 생성·수정·삭제를 각각 분리. 대상과 변경안에 결합된 별도 사람 승인 |
| Local Git read/edit/commit | agent별 격리 checkout 내부 작업. 읽기·파일 수정·로컬 commit을 별개 권한으로 취급 |
| Git remote push | 지정 remote와 ref만. 로컬 commit 승인으로 push를 허용하지 않음 |
| GitHub read / draft PR / merge | 저장소 읽기, draft PR 생성, merge를 각각 분리. PR 생성 승인은 merge 승인이 아님 |
| Command execution | 입증된 격리 실행 환경이 있을 때만 opt-in. 임의 shell 문자열 대신 검토된 실행 사양과 argv 사용 |

Git 로컬 실행과 GitHub API/MCP는 서로 다른 adapter다. GitHub MCP 연결은 로컬 Git 명령 실행 권한을 주지 않는다. Git remote를 통해 GitHub에 접근할 때도 push에 별도의 권한이 필요하다.

## 공식 연결 후보

Notion의 공식 hosted MCP 후보는 `https://mcp.notion.com/mcp`다. 공식 문서는 OAuth 승인 후 사용자가 접근할 수 있는 내용을 읽고 수정하는 remote MCP로 설명한다. TalkAbout의 agent별·방별 대상 제한은 별도 broker가 추가로 강제해야 한다. [Notion MCP 개요](https://developers.notion.com/guides/mcp/overview), [공식 연결 안내](https://developers.notion.com/guides/mcp/get-started-with-mcp).

GitHub 후보는 공식 [github/github-mcp-server](https://github.com/github/github-mcp-server)다. 프로젝트는 tool allowlist와 read-only 모드를 제공한다. 이 옵션만으로 TalkAbout의 방 ACL·human 승인·task lease 검증이 생기지는 않는다. 정확한 지원 버전·도구 스키마·인증과 대상 범위를 고정하고, 사용자 계정 연결을 별도로 승인받은 뒤 검증한다.

현재 단계에서는 두 서비스에 접속·인증하지 않는다. 런타임 설치나 제공자별 MCP 지원을 완료된 것으로 간주하지 않는다.

## 명령 격리의 출시 관문

Windows Job Object는 후손 프로세스 수명과 정리를 통제하는 수단이며, 파일·네트워크·비밀정보 접근을 제한하는 sandbox가 아니다. 현재 Windows containment를 shell 실행 허가 근거로 확대하지 않는다.

실제 명령 프로필은 agent/작업별 독립 임시 작업공간, 호스트 파일·계정·credential store·SSH agent 분리, 최소 OS 권한, CPU/메모리/프로세스/시간/디스크/출력 제한, 기본 egress 차단과 대상 allowlist가 입증돼야 한다. Git hooks·외부 helper·전역 설정·submodule·symlink·경로 탈출과 임의 실행을 통한 우회도 검증한다. 격리를 제공할 VM/container/OS 정책의 선택과 설정 변경은 별도 승인 대상이다. 검증되지 않은 플랫폼에서는 실행 전 거절한다.

## 상태와 사용자 안내

KO/EN 연결 화면은 profile, agent/room, 대상, 읽기·쓰기 scope, 승인 대기, 연결/실행/실패/철회 상태와 구체적인 다음 행동을 표시한다. 로그인과 도구 승인, runtime 실행, 게시 완료를 구분한다. 지원되지 않는 환경에는 실행 명령을 제시하지 않는다. 기존 작업의 종료를 확인하기 전 새 worker를 자동 실행하지 않는다.

Invocation 상태는 proposed → awaiting_approval → ready → executing → succeeded/failed/unknown/cancelled다. 준비 단계에서도 최신 fence와 승인을 다시 검증한다. 철회 후 새 실행은 거절한다. 이미 요청된 외부 쓰기가 취소됐다고 단정하지 않는다. 결과 불명 쓰기는 unknown으로 보존하고 사람이 외부 결과와 대조하기 전 자동 재시도하지 않는다. provider idempotency가 검증된 작업만 해당 계약 안에서 재시도할 수 있다.

도구 결과와 Notion/GitHub 문서·댓글·agent 메시지는 untrusted data다. 그 안의 지시로 policy·scope·대상·승인·접속 설정을 바꾸지 않는다. 감사 이력에는 human actor와 정책/fence, 인자 digest, 승인 및 외부 결과 참조를 남긴다. 비밀값을 prompt·URL·로그에 넣지 않으며 결과와 다운로드에도 방 ACL·보존·회수 정책을 적용한다.

## 다음에 구현할 최소 수직 단위

첫 단위는 **내부 스키마 + 순수 정책 검증 + 메모리 fake adapter**다. 도구 실행 기능을 운영 API에 연결하지 않는다. `ToolBroker.authorize`가 인증된 실행 문맥과 grant/policy/approval/fence를 대조하고, 허용된 fake tool만 나열·호출한다. fake read는 고정 fixture를 반환하고 fake write는 메모리 ledger에만 기록한다. provider adapter와 실제 프로세스 runner는 등록하지 않는다.

성공 기준은 기본 프로필 도구 0개, 다른 agent/room/task/attempt의 호출 거절, 만료 lease와 철회/변경된 policy 거절, 승인 없는 쓰기 거절, 메시지 기반 권한 확대 거절, 같은 요청 중복 쓰기 0회, 결과 불명 쓰기 자동 재시도 0회다. 잘못된 target, path traversal, 큰 결과, fence 변경 중 결과 반환도 fake adapter로 검증한다. 이 단계의 성공을 실제 계정 연결이나 명령 격리 성공으로 보고하지 않는다.

이 단위 검토 후에만 API/DB 제안과 마이그레이션·롤백, UI 승인 화면, 비밀정보 저장 경계, 실제 adapter 통합 계획을 반환한다. 실제 연결 시험은 사람이 지정한 테스트 계정·대상과 별도 승인으로 진행한다. 명령 실행 출시는 격리 검증을 독립 관문으로 둔다.

## English summary

This is a proposed follow-up, not an enabled tool platform. Keep text-only defaults. A shared ToolBroker must bind every tool call to authenticated agent identity, room, task, lease ownership and policy revision; expose only approved broker tools. Separate local Git from GitHub MCP, reads from writes, push from PR creation and merge. Treat all retrieved content as untrusted. Unknown external writes require reconciliation rather than automatic retry. Windows Job containment is not a sandbox. The next implementation slice is internal schemas, pure authorization and fake adapters only; public API/DB changes and real authentication/execution require a reviewed plan first.
