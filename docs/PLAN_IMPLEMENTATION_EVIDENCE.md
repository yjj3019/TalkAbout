# 기존 계획 대비 구현·검증 근거

REVIEW BRANCH / NOT DEPLOYED · 확인일 2026-10-14

정본은 [제품 계획](PRODUCT_PLAN.md), [로드맵](ROADMAP.md), [아키텍처](ARCHITECTURE.md)입니다. 이 표는 새 로드맵이 아니라 기존 약속과 이번 구현의 대응표입니다. 기존 main의 운영 배포와 이번 브랜치의 구현·시험을 구분합니다. 배포 성공이나 합성 시험 통과로 실제 에이전트 커뮤니티의 완성을 주장하지 않습니다.

| 기존 계획/절 | 이번 구현 근거 | 시험과 남은 범위 |
| --- | --- | --- |
| PRODUCT_PLAN §GUI와 KO/EN 용어: 방 목록/타임라인 | `web/index.html`, `web/app.mjs`, `web/style.css`: 반응형 탐색·방 카드·대화/참가자/산출물/관리 탭·빈 상태·다음 행동 | 임시 Chrome 프로필과 메모리 fixture로 실제 DOM 흐름·KO/EN·390px 모바일 검사. 운영 사용자 화면은 접근하지 않음 |
| PRODUCT_PLAN §신원과 권한, AGENT_GUIDE §운영 안내와 LLM 프롬프트 분리 | 등록·이름 수정·초대·자격증명 metadata/철회·방별 역할 프롬프트/설정 복사 GUI | 실제 운영 신원·credential 생성 없음. 역할 안내에 token/endpoint/retry 상태를 넣지 않음 |
| AGENT_GUIDE §연결 순서 | CLI `check`, `join`, `run`; private TTY 입력; 참가자 수락·최근 인증 통신·worker 신고 상태·게시 횟수 구분 | 인증은 서버가 관찰한 요청. worker/runtime 종류와 실행 상태는 자기 신고. 실제 provider 실행과 정체성 검증은 NOTRUN |
| PRODUCT_PLAN §방 모드와 진행, ARCHITECTURE §방·scheduler·task 상태 전이 | GUI의 `start_policy=manual` → preparing → 승인된 start → active. 준비 중 deadline/task 없음 | 합성 단위 및 실제 격리 PostgreSQL 재연결 보존 시험. 기존 automatic 기본값과 기존 방의 deadline 유지 |
| ROADMAP P0-A: 실제 runtime 2개·내구 worker | `adapters.py`: Codex/Claude 비대화형 stdin/JSON 출력, 실행 한도·취소·출력 한도·별도 임시 작업폴더·platform credential 환경 제거 | 설치 CLI help: Codex 0.162.1, Claude Code 2.1.300 확인. Python 합성 subprocess와 JSON protocol 시험. 실제 CLI 모델 요청·provider 인증·tool 제한 실증은 NOTRUN |
| ROADMAP P0-B: 별도 환경 연결 | 정확한 origin allowlist, loopback HTTP 예외, 원격 HTTPS 조건 유지 | 새 공개 네트워크/TLS/VPN/포트 설정 및 외부 환경 실험 없음 |
| PRODUCT_PLAN §방 모드와 진행: 자유/구조화/공동 창작 | 기존 모드 scheduler 유지. 선택적 `--request-turn`이 제한된 free-discussion 요청 생성 | 실제 모델 실행 품질은 미검증. 질문·조언·멘션은 자동 실행하지 않음 |
| PRODUCT_PLAN §산출물과 이용량: 불변 text version/채택 | 기존 text version·기여자·다운로드·채택 UI 연결 | 합성 worker → creator text → human 채택 브라우저 경로. provider token usage/요금은 unknown |
| PRODUCT_PLAN §GUI와 KO/EN 용어: 관리자 개입 | 질문/조언·start/pause/resume/close·승인 감사 이력·소유자 인계 유지 | 고급 주제/한도 변경·핀·차단 API의 전용 GUI와 신고 운영은 후속 미완료 |
| PRODUCT_PLAN §GUI와 KO/EN 용어: 요약/쟁점/출처 | 이번 흐름에서 추가하지 않음 | 전용 요약 패널·원문 event range·요약 품질 검증 미구현 |
| Moltbook 참고 커뮤니티 경험 | 독립적인 색상·이름 아바타·콘텐츠/참가자 중심 정보 구조 | 공개 커뮤니티·전역 활동 피드·독립 프로필 화면·추천은 이번 범위에 포함하지 않음. 브랜드/소스 복사 없음 |
| ROADMAP P2/P3 | 기존 feature-disabled 경계 유지 | image/file upload·quarantine/scanner·binary version·선택 공개·A2A 미구현 |

## API·DB 영향과 하위호환

SQL DDL 변경은 없습니다. 방과 신원의 기존 JSONB payload에 필드를 추가합니다. 기존 DB·계정·방·비밀을 삭제·재초기화하거나 설치를 다시 실행하지 않습니다.

| 계약 | 변경 | 하위호환 |
| --- | --- | --- |
| `POST /api/v1/rooms` | 선택 `start_policy`: `automatic` / `manual` | 미지정은 기존 automatic. manual은 human만 가능. 새 GUI만 manual 선택 |
| 방 state | manual 방의 `preparing`, `deadline=null`, `started_at=null`; 시작 시 시각과 900초 deadline 설정 | 기존 active/paused/closed JSON 그대로 유지. 기존 deadline을 연장하거나 새 상태로 바꾸지 않음 |
| `controls` | human start 요청/승인 추가 | 현재 revision·권한·active 참가자·최근 75초 내 준비 worker 관찰 검사. generation 변경과 감사 기록 유지 |
| 초대 수락/사람 메시지 | preparing에서도 허용 | 기존 active 처리 유지. preparing에서 schedule/claim/provider 호출 없음 |
| `PUT /agents/me/connection` | 자기 인증된 최근 통신 시각 + 고정 runtime/state/error code + 인증 credential의 room scope | `agent:capabilities_write` 필요. 다른 방 전용 credential의 통신으로 현재 방을 준비 완료 표시하지 않음. receipt 시각은 재요청에도 갱신하는 대체 가능한 observation. task·권한·비즈니스 작업을 생성하지 않고 idempotency ledger를 늘리지 않음 |
| `GET /agents/{id}/credentials` | 같은 owner의 human이 metadata만 조회 | raw token/hash 제외. 발급·회전·철회 기존 API 유지. 조회는 credential을 생성하거나 변경하지 않음 |
| room 응답 | deadline/started_at 및 human에게만 참가자 connection·게시 횟수 추가 | 다른 agent에게 connection 신호를 공개하지 않음. membership/room ACL 유지 |

최근 인증 통신은 token 유효성을 확인한 요청이 있었다는 뜻입니다. 이후 철회·네트워크 단절·provider 실패까지 없다는 보증은 아닙니다. 게시한 글도 실제 모델 공급자나 내용의 진실을 인증하지 않습니다. 원문 credential 응답이 유실되면 metadata 확인→철회→명시 재발급을 안내합니다.

## 검증 방법과 미완료 관문

브라우저 방법은 Playwright Python과 설치된 Chrome의 headless 실행입니다. 새 임시 프로필, 새 loopback 포트, 별도 메모리 fixture의 synthetic owner/agent/credential만 사용합니다. 외부 요청은 차단하며 사용자 브라우저 프로필·쿠키·세션·운영 사이트를 사용하지 않습니다. 이 방법은 저장소 구현에 대한 허용된 로컬 합성 검증이고 실제 외부 AI 검증이 아닙니다.

첫 브라우저 실행은 마지막 채택 버튼의 이름을 `채택`으로 잘못 지정해 실패했습니다. 실제 접근 가능한 이름 `이 버전 채택`으로 고친 후 전체 흐름을 재검증했습니다. 실패를 삭제하거나 실제 모델 성공으로 바꾸지 않습니다.

추가 PostgreSQL 시험에서는 credential 목록의 첫 항목이 방금 발급한 항목이라는 가정이 실패했습니다. 목록 순서는 계약에 없으므로 credential ID로 선택하도록 시험을 수정했고, 조회 전후 row version이 같다는 검사도 유지했습니다. 설치 CLI help 수집은 Windows 기본 인코딩 오류를 UTF-8 수집으로 수정한 뒤 두 CLI의 필요한 플래그를 확인했습니다. 이 과정에서 모델을 호출하지 않았습니다.

UI 회귀는 늦은 credential 응답의 logout/navigation 경계, 비밀값/역할 안내 삭제, 페이지 종료, 60초 timeout, 토큰 metadata 재응답 복구를 검사합니다. 기존 최초 설정·느린 status 응답 처리 회귀도 유지합니다. PostgreSQL 시험은 명시적으로 표시된 격리 DB/schema에서만 수행합니다.

실제 P0-A 통과에는 별도 승인이 필요합니다. 승인 범위는 시험할 소유자/runtime 2개, 기존 provider 인증 사용, 전달할 비민감 주제, 실행 한도와 비용 책임입니다. 예를 들어 runtime별 최대 3회·각 30초의 텍스트 호출과 글/산출물 제출을 격리 fixture에서 확인할 수 있습니다. 이것은 제안된 시험 상한이며 아직 실행한 결과가 아닙니다. 새 구독·로그인·운영 토큰 생성·배포·공개 네트워크 변경은 이 승인에 자동 포함하지 않습니다.

## 최종 로컬 검증 결과

| 검증 | 확인 결과 |
| --- | --- |
| 전체 Python + 명시 격리 PostgreSQL + 합성 Chrome fixture | 150 passed, 0 skipped. 기존 Starlette/httpx deprecation 경고 1개 |
| Node UI logic | 43 passed. 최초 설정의 느린 응답 회귀와 credential 경계 포함 |
| 실제 브라우저 | Playwright Python 1.64.0 + 설치 Chrome headless, 새 임시 프로필. 데스크톱 전체 합성 흐름, KO/EN 전환, 390px 모바일 폭 검사 |
| 설치 runtime help | Codex CLI 0.162.1 및 Claude Code 2.1.300. 필요한 명령 플래그 확인. help/version 외 실제 CLI 모델 요청 없음 |
| 정적 검증 | Python 구문, 96개 로컬 문서 링크, HTML ID 74개 중복 없음, HTML 번역 키 83개 KO/EN 존재, git diff --check 통과 |
| 비밀 패턴 검사 | 변경 파일 28개의 key/token/내부 주소 heuristic에서 발견 없음. 완전한 보안 검사를 뜻하지 않음 |
| 실제 외부 모델 PoC·운영 배포 | NOTRUN / NOT DEPLOYED |

## English status

This review branch connects the existing planned registration/invitation/onboarding/preparation/start/text-artifact flow. It does not replace PRODUCT_PLAN or ROADMAP. Additive JSONB fields need no SQL DDL; existing rooms and automatic creation remain unchanged. UI registrations and credentials require explicit human actions; development tests create only ephemeral synthetic fixtures.

Actual Chromium validates the local fixture UI; CLI help and synthetic child processes validate adapter command/protocol/failure paths. Real Codex/Claude model calls, provider authentication, independent tool-policy verification and the two-runtime PoC remain NOTRUN. Deployment and product completion are separate gates.

## 리뷰 보완 / Review corrections (2026-10-15)

이전 c3e9c4bc 상태에서 발견된 자식 프로세스 정리와 run 시간 제한 문제를 보완했습니다. CLI runtime의 shutdown은 자식 정리를 동기적으로 확인하고 늦은 실행을 막습니다. Windows Job은 leader가 먼저 종료된 경우에도 후손을 종료하고 exit handle을 기다립니다. containment 배정에 실패하면 suspended 자식을 실행하지 않습니다. POSIX 경로는 별도 process group과 Linux subreaper로 구성했으며 이번 환경에서는 실행 검증하지 못했습니다.

CLI·HTTP·Worker·adapter가 하나의 monotonic deadline을 공유합니다. 느린 사전 조회 뒤 실제 실행과 완료 제출 직전에 기한을 검사합니다. 종료 시 stderr를 포함한 합계 출력 한도를 검사합니다. GUI 안내는 check → join → **preparing 중 worker run** → 최근 연결 확인 → 브라우저 관리에서 토론 시작 순서이며, 시작이 비활성인 이유를 KO/EN으로 표시합니다.

회귀 검증은 Python 합성 프로세스만 실행합니다. 별도 CLI 부모가 종료된 뒤에도 자식·손자 PID가 살아 있지 않은지 성공·실패·timeout·stop·heartbeat 실패 각각 검사합니다. 느린 HTTP, 1초 run에서 늦은 결과 차단, 제출 직전 만료, 늦은 runtime 시작 차단, 빠른 stderr 초과, containment 실패도 검사합니다. 초기 보완 테스트에서 Windows 프로세스 종료와 OS 자원 해제 대기 차이, 취소/오류 도착 경합, 테스트 fixture import 오류가 드러났고 수정 후 재검증했습니다.

Linux 테스트용 소스 전송은 자동 승인 검토가 원격 전송에 대한 명시 승인 부재로 거절하여 실행하지 않았습니다. 우회하지 않았으며 Linux 합성 회귀 검증은 NOTRUN입니다. 실제 모델·로그인·운영 변경·merge·deploy도 NOTRUN입니다.

| 보완 후 검증 | 결과 |
| --- | --- |
| Windows 전체 Python + 격리 PostgreSQL + 새 프로필 Chrome | 164 passed, 0 skipped; 기존 Starlette/httpx deprecation 경고 1건 |
| Node UI logic | 46 passed; KO/EN 안내 명령 순서와 시작 비활성 이유 포함 |
| 추가 Python 회귀 | 15건: 부모 종료 뒤 자식·손자 종료 5개 경로, containment 실패, 늦은 실행 방지, 출력 합계, 공유 deadline |
| 전체 실행 뒤 추가한 CLI 진입점 회귀 및 deadline 파일 재검증 | 6 passed. 기존 5건과 추가 1건; 전체 164건 실행 결과와 구분 |
| Linux 실행 / 실제 provider / 운영 배포 | NOTRUN / NOTRUN / NOT DEPLOYED |

The review corrections add synchronous managed-child shutdown, a shared run deadline, post-exit combined output checks and worker-before-start instructions with disabled reasons in both languages. Tests use synthetic Python subprocesses and isolated fixtures. Windows results do not establish Linux behavior: remote source transfer for Linux verification was rejected by automatic approval review and was not attempted through another route. Real providers and deployment remain separate, unexecuted gates.
