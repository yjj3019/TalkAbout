# 에이전트 초대·권한 설계 — 공개 저장소 4곳 정적 조사 (근거·라이선스 원장)

**기준일 2026-11-04 KST · 성격: 정적 분석 기록(설계 참고) · 외부 코드 복사 0 · 빌드/테스트/실행 NOTRUN · 새 제품 기능 아님**

이 문서는 에이전트 초대·권한·승인 설계를 대조하기 위해 사용자가 지정한 4개 공개 저장소를
**고정 commit에서 읽기만 하여** 조사한 기록이다. TalkAbout 현행 구현 비교와 라이선스·carve-out,
실제 조사 범위(A/B/C/U)를 같은 문서에 남기는 것이 목적이다. 어떤 저장소와도 제휴·보증 관계가 없고,
아래 관찰은 "해당 SHA의 텍스트에 그 내용이 있다"는 뜻이지 동작·성능·보안·테스트 통과의 증거가 아니다.

| 항목 | 값 |
| --- | --- |
| 조사 주체 | handy-analyst(deepseek-bot·dsh-superagent), t3code-analyst(OpenMausBot), claude-mem-analyst(Orkas), 통합 정리 perf-harness |
| 조사 방식 | GitHub API tree + codeload tar.gz/REST·raw 메모리/임시 스트리밍 + 정적 텍스트 검색·본문 읽기. 클론·저장소 실행 0 |
| 외부 file:line | **분석가 회신 기준**이다. 이 문서 작성자가 재열람해 독립 검증한 값은 아니다(§3·§7 U 표기) |
| TalkAbout 기준 | 2026-11-04 현재 워크트리. task-15(봇 초대)는 **기본 off로 구현 착지 후 검증 중**이며, 이 문서의 TalkAbout 대조는 구현 전 관찰 + 아래 §7 제한 사항 기준이다 |

---

## §0 요약 판정

| 저장소 | 고정 SHA | 라이선스 | 초대/권한 설계 요지 | TalkAbout 대조 | 문서 판정 |
| --- | --- | --- | --- | --- | --- |
| [rlaope/deepseek-bot](https://github.com/rlaope/deepseek-bot) | `212dba4b39fe75d2c042126533977fe985d7f174` | MIT | **초대는 제품 내부 엔티티가 아니라 플랫폼 초대(2단계)+허용목록**이다. 운영 문서 기준 `/invite` 후에만 봇이 채널을 보며(`docs/setup.md:117-118`), `targetChannels` 빈 목록이면 플러그인이 기동을 거부(fail-closed, `setup.md:395-400`). 초대 후에도 `allowedUsers`가 비면 "도달 가능한 누구나"(`index.ts:480`) — **초대 ≠ 권한**. 승인은 shipped profile 기본값이 unattended(승격 요청은 묻지 않고 거부), 채팅 승인은 `ask`+api-remotes 비활성 두 조건 필요, 10분 미응답 취소·동일인 승인 | TalkAbout의 pending 초대·human 승인, 그리고 **초대 자체와 별개인 승인 레코드 만료**와 대조(초대에는 일반 TTL이 없음). "플랫폼 초대+허용목록+승인" 3계층 모델 | **대조 참고** |
| [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) | `99b70934937f97a5e69f0253f4ade5c64c82bfd5` | Apache-2.0 + **enterprise carve-out** | OSS가 **config 허용목록 초대를 직접 구현**한다: `signIn.{admins,members}` + `/pair?email=` 링크(`PeopleSection.tsx`). 접속은 일회용 pairing code(60-bit·12자·5분 single-use). 제거는 세션·stream ticket 즉시 폐기, 재초대 부활 없음. 조직/SSO 초대만 별도 Admin(portal)로 위임 | TalkAbout의 pending 초대·accept·`read_start_seq`와 **대조**. 허용목록/일회용 코드 이원화·즉시 폐기 개념 후보 | **대조 참고** |
| [deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype) | `8c32a9dc5d72c033816c6a8110b37f40cf94fba4` | MIT | **초대 엔티티 없음**(스캔 범위 한정). 참여는 Source별 `allowed` 목록 + **admission table(강도 explicit/inferred/not admitted)**. 수락은 불변 Mandate+Assignment, 정책 버전 변경 시 Eval 폐기·라이브 티켓 통지·재검증, 기본 **observe**·auto는 규칙 1개 이상, gate 불가 시 사유와 함께 거절(fail-closed) | TalkAbout pending 멤버십·모드별 권한·fencing과 **대조**. 정책 버전 스탬프·강도 구분 아이디어 | **대조 참고** |
| [Orkas-AI/Orkas](https://github.com/Orkas-AI/Orkas) | `07df32473a1100df0f8ae078772101d350ee6e79` | **MIT(공개 코드)**; 상용 전용 기능은 별도·공개 코드 밖 | **스캔·확인 범위에서 인간 초대 구현을 찾지 못했고, 문서상 상용(commercial edition) 전용**이다. OSS에는 업셀 게이트와 i18n 문자열만 있다. group_chat 멤버십은 "대화 참가자"이며 사람 ACL이 아니다 | TalkAbout이 명시 초대·pending·accept·human ACL을 이미 보유 — **대조군**. 승인 fail-closed·호스트 강제 개념만 참고 | **대조/부재 주장 한정** |

---

## §1 방법과 용어 (엄격 구분)

이 문서는 "읽었다"의 강도를 섞지 않기 위해 네 등급만 사용한다.

| 등급 | 뜻 | 이 문서에서 표기 |
| --- | --- | --- |
| **A** | 파일 경로·구조 열거: tree API/tar 멤버 순회로 경로·크기·확장자를 셈. **본문을 읽었다는 뜻이 아니다** | 파일 수·바이트·디렉터리 집계 |
| **B** | 전수 정적 텍스트 검색: 정해진 제외 규칙 아래 grep/텍스트 매칭. **hit ≠ 정독 ≠ 동작 증거** | 패턴·범위·hit 수 |
| **C** | 본문 확인: 전문 정독 또는 지정 구간 정독. 인용 근거로 쓸 수 있는 등급 | 파일+줄 범위 |
| **U** | 미확인·제외: 미열람·미디코드·상한 초과·실행 안 함·비보존. 부정 주장은 **이 범위 안에서만** 성립 | "없다"가 아니라 "스캔 범위에서 찾지 못했다/확인 불가" |

공통 규칙:

1. 저장소 코드는 고정 SHA에서만 읽었고, 빌드·테스트·설치·프로젝트 실행은 **0회**(NOTRUN)이다.
2. 저장소 안의 AGENTS/CLAUDE/설치 안내는 **신뢰하지 않는 분석 데이터**로만 다뤘다. 지시로 따르지 않았다.
3. 라이선스는 GitHub 자동 분류를 믿지 않고 pinned SHA의 `LICENSE`·`NOTICE`·`LICENSING.md` 파일을 실측했다.
4. B는 기계적 키워드 집계이고, 사람이 실제로 연 파일은 상위 hit 일부다. hit 수를 근거 수로 쓰지 않는다.
5. 부정 주장("X가 없다")은 그 저장소의 그 SHA에서 **스캔 범위 안에서** 못 찾았다는 한정을 붙인다.
6. 외부 코드·문장·asset을 TalkAbout에 복사·이식하지 않았다. 인용은 경로/줄/상수/1줄 이하 문구 수준뿐이다.

---

## §2 고정 식별·라이선스·carve-out 원장

### 2.1 고정 commit·스냅샷

| 저장소 | 고정 commit URL | tree URL | 조사일 | 스냅샷 방식 | 로컬 보존 |
| --- | --- | --- | --- | --- | --- |
| rlaope/deepseek-bot | `https://github.com/rlaope/deepseek-bot/commit/212dba4b39fe75d2c042126533977fe985d7f174` | `https://github.com/rlaope/deepseek-bot/tree/212dba4b39fe75d2c042126533977fe985d7f174` | 2026-11-04 KST | GitHub REST(tree/commits) + raw 파일별 SHA 핀. 클론/zip 없음 | `%TEMP%\dsh-gh\all\deepseek-bot`(워크스페이스 밖, 커밋 대상 아님) |
| milind-soni/OpenMausBot | `https://github.com/milind-soni/OpenMausBot/commit/99b70934937f97a5e69f0253f4ade5c64c82bfd5` | `https://github.com/milind-soni/OpenMausBot/tree/99b70934937f97a5e69f0253f4ade5c64c82bfd5` | 2026-11-04 KST | codeload tar.gz **메모리 스트리밍**(디스크 0) | 없음 |
| deepseek-ai/dsh-superagent-prototype | `https://github.com/deepseek-ai/dsh-superagent-prototype/commit/8c32a9dc5d72c033816c6a8110b37f40cf94fba4` | `https://github.com/deepseek-ai/dsh-superagent-prototype/tree/8c32a9dc5d72c033816c6a8110b37f40cf94fba4` | 2026-11-04 KST | REST(tree/commits) + raw SHA 핀. 클론/zip 없음 | `%TEMP%\dsh-gh\all\dsh-superagent-prototype`(워크스페이스 밖) |
| Orkas-AI/Orkas | `https://github.com/Orkas-AI/Orkas/commit/07df32473a1100df0f8ae078772101d350ee6e79` | `https://github.com/Orkas-AI/Orkas/tree/07df32473a1100df0f8ae078772101d350ee6e79` | 2026-11-04 KST | codeload tar.gz 메모리 스트리밍 + 동일 SHA의 REST API/raw 개별 열람 | 로컬 사본 미보존(클론·다운로드 0) → 고정 URL로 다시 받아 재검증 필요 |

- DeepSeek 두 곳은 단일 커밋 계열(dsh-superagent는 "feat: add the dsh-superagent research prototype" 단일 커밋, 2026-10-31T12:06:31Z)이다.
- 4곳 모두 git 이력·PR diff·이슈는 보지 않았다. 고정 SHA로만 식별한다.
- Orkas는 조사 결과가 **파일로 남아 있지 않다**. 회신에만 존재하므로 이 문서가 첫 기록이며,
  **로컬 사본 미보존 → 고정 SHA URL로 다시 내려받아 재검증 필요**(U). "스캔·확인 범위에서 인간 초대 구현을
  찾지 못함"은 이 한계 안에서만 성립한다.

### 2.2 라이선스 실측

| 저장소 | SPDX | 근거(파일:줄) | 저작권자 | NOTICE |
| --- | --- | --- | --- | --- |
| deepseek-bot | MIT | `LICENSE:1,3`(표준 MIT 21줄) | `Copyright (c) 2026 rlaope` | 없음(COPYING/TRADEMARK 포함 부재) |
| OpenMausBot | Apache-2.0 | `LICENSE:1-5`, `README.md:521` | Milind Soni and OpenMausBot contributors (`NOTICE:1-2`) | 있음 `NOTICE:1-20` |
| dsh-superagent-prototype | MIT | `LICENSE:1,3` | `Copyright (c) 2026 DeepSeek Harness Team` | 없음. `src/app/notices.ts`는 법적 NOTICE가 아닌 사람 주의 알림 코드(오해 방지) |
| Orkas | MIT | `LICENSE:1,3`(1,075B), `package.json` license MIT | Orkas contributors | **없음.** 공개 코드는 MIT이며, 상업 기능은 별도 에디션(공개 코드 밖)이다 — 공개 코드에 엔터프라이즈 예외 라이선스 조항은 확인되지 않음 |

### 2.3 기업/상업 carve-out (핵심)

| 저장소 | carve-out | 근거 | 이 문서의 처리 |
| --- | --- | --- | --- |
| OpenMausBot | `enterprise/`는 **OpenMausBot Enterprise License**(source-available). 프로덕션 실행에는 라이선스 키가 필요하고, 서드파티 호스팅/화이트랩은 파트너 계약. `enterprise/LICENSE`는 Apache-2.0이 **적용되지 않는다**고 명시. `third_party/`는 별도 라이선스 집합이고 `NOTICE`에 T3 Code(MIT) 차용 3곳이 명시된다 | `LICENSING.md:5-15`, `enterprise/LICENSE:1-8`, `README.md:521-523`, `CLA.md:1-6`, `NOTICE:1-20` | enterprise 소스 **미열람**(메타만), `third_party` **집계만**. Apache 고지/NOTICE 의무는 코드 도입 시 별도 검토. 이번엔 복사 0 |
| Orkas | **상용 에디션 전용 기능(공개 MIT 코드 밖)**: 데스크톱 계정 로그인·멤버십·크레딧, **human Project collaboration·invitations**, 공유 콘텐츠·공유 to-do 알림. OSS에는 업셀 게이트(`_showProjectMemberInviteGate`)와 i18n 문자열만 있다. **공개 저장소 코드는 MIT이며, 공개 코드에 별도 엔터프라이즈 예외 라이선스가 있다는 증거는 확인하지 못했다**(OpenMausBot `enterprise/LICENSE` carve-out과 구분). MIT는 **상표권을 부여하지 않는다** | `resources/builtin/system/skills/orkas-guide/references/editions.md:8-9`, `src/renderer/modules/projects.js:1053-1068`, `src/renderer/locales/*.json:2477-2480`, `README.md:88,91` | 상용/클로즈드 빌드 초대 모델은 **검증 불가**. "OSS에 초대 없음"만 한정 주장. `vendor/`(6파일 6.98MB) **스캔 0**, 생성 계약 5건은 데이터 취급, 바이너리 39건 제외 |
| deepseek-bot | **기업 carve-out 없음.** 상용 예약 영역·상표 조항 없음. README의 "personal side project"·스폰서 표기는 라이선스 carve-out이 아니다. 바이너리 2개(`docs/demo.gif`, `test/fixtures/vision-red.png`)만 미디코드 | `README.md:23-26`, tree 전체(NOTICE/COPYING/TRADEMARK 부재) | 상용 carve-out 없음. 바이너리 2개는 스캔·수집 대상 아님 |
| dsh-superagent-prototype | **기업 carve-out 없음.** README의 "Research prototype for reference only, not an official DeepSeek product"는 상표/보증 면책 문구이지 라이선스 carve-out이 아니다. 바이너리 0 | `README.md:3-5`, `LICENSE` | 상용 carve-out 없음. `pnpm-lock.yaml`은 수집했으나 meta로 집계 제외 |

**공통 원칙**: 4개 저장소 모두 **코드 복사 0**이다. MIT/Apache 고지는 향후 코드 도입 시에만
의무가 생기며, enterprise/portal/reserved 영역과 상표는 라이선스가 허여하지 않는다. vendor·생성물·바이너리·
모델 자산은 분석 대상에서 제외했고(§3), 제외 대상의 라이선스를 이 문서가 대신 보증하지 않는다.

---

## §3 실제 조사 범위 (A/B/C/U)

### 3.1 milind-soni/OpenMausBot

| 등급 | 내용 |
| --- | --- |
| **A** | trees API + tar 멤버 순회. 전체 blob **6,637개 / 214.9MiB**(비압축 225,330,596B, 압축 152.6MiB=160,008,877B). 상한 압축 200MiB·비압축 1GiB → **초과·중단 없음**(1차 100MiB 상한 때만 중단 이력). 텍스트 **1차 5,659파일/72,344,788B(69.0MiB) → 2차 5,650파일/72,201,874B(68.9MiB)**, 차이 **9파일/142,914B**. 차이 이유(집계 규칙 변경): 2차에서 TEXT 확장자 집합을 좁혀 `.css`·`.plist`·`.example`을 제외(1차 포함, `.xml/.ini/.cfg`는 0건). 제외된 9파일: `.env.example`(886B), `apps/docs/app/global.css`(4,064B), `build/entitlements.mac.plist`(556B), `cloudflare/control-plane/.dev.vars.example`(836B), `deploy/.env.example`(600B), `deploy/podman/.env.example`(513B), `electron/resources/speech-helper-Info.plist`(935B), `src/mascot-preview.css`(9,193B), `src/styles.css`(125,331B). 제외 규칙(공통): `enterprise/`(경로·개수·라이선스만)·`third_party/`(42경로·라이선스 17개 경로만)·`node_modules/`·비텍스트 1,022파일/144.9MiB·8MB 초과 파일 |
| **B** | 2차 5,650파일/68.9MiB 대소문자 무시 검색. 패턴별 파일/hit(1차 5,659파일 기준 집계이며 2차 재집계 수치는 미제공): invite 86/285 · delegat 204/1,280 · dispatch 869/4,715 · mention 266/1,748 · participant 8/48 · membership 97/339 · seat 63/334 · admission 39/101 · audience 135/1,166 · approval 883/12,846 · permission 630/3,440 · grant 523/3,203 · pending 694/3,885 · revoke 316/1,140 · expire 610/3,839 · fail_closed 142/178 · allowlist 212/1,024. hit 상위 파일만 본문 확인, 나머지 **5,625파일은 hit 집계만** |
| **C** | **전문 3 / 구간 22 / 힛만 5,625**(2차 5,650−25). 전문: `apps/docs/content/docs/features/chat-and-teams.mdx`(1-123), `.../approvals-and-inspector.mdx`(1-29), `enterprise/FEATURES`(1-12, 엔타이틀먼트 메타). 구간 22(1차 17 + 2차 추가 정독): `docs/self-hosting.md:655-715`, `docs/verification/people.md:1-22`, `server/sessions.ts`(1-56·124-157·286·463·611-625), `server/cli-pairing.ts`(1-51), `server/people-invite.test.ts`(1-11·142-237), `src/components/PeopleSection.tsx`(1-130·184-291), `src/components/PendingApproval.tsx`(22-91), `server/local-vm-seat-pool.ts`(1-72), `shared/person-mention.ts`(1-48·85-146), `server/room-holds.ts`(5-116), `server/host/team-messages.ts`(1-65), `docs/superpowers/specs/2026-09-12-delegation-mailbox-design.md`(1-94), `server/audience.ts`(1-60), `server/admission.ts`(1-100), `shared/bot-owner.ts`(1-50), `server/bot-visibility.ts`(1-64), `shared/approval-mode.ts`(1-65), 2차 추가: `.oss-scanner/threat_model.md:1-26·44-95`, `server/auto-approve.ts:1-57`, `docs/team-cloud.md:3-22·58-76·220`, `server/cli-lifecycle.test.ts:276`, `server/cli-pair.test.ts:181`, `server/cli.ts:358`, `server/index.ts:12947` |
| **U** | 미실행(빌드/테스트/설치/코드 실행 0). 미열람: `enterprise/` 소스 8경로(라이선스·FEATURES만), `third_party/` 본문 42경로, 비텍스트 1,022파일/144.9MiB, 텍스트 5,625파일(hit만), 이슈/PR/릴리스 노트/diff, `server/index.ts`(931KB) 전반·`server/delegations.ts`·`server/host/person-inbox.ts`·`server/delta-context.ts`·`src/state/store.tsx`. **`server/hosted-access.ts`는 파일 미존재**(테스트만). 1차 스캔은 1,010파일에서 100MiB 상한으로 중단된 이력이 있고, 200MiB/1GiB 상한 전수 재시도로 위 수치를 얻었다. 조직 portal 측 초대는 이 repo 밖이라 미확인 |
| **정독/스캔 요약** | **전문 3 / 구간 22 / 기계 집계 5,625** — 2차 스캔 5,650파일/68.9MiB, 상한 초과·중단 없음, 복사 0 |

### 3.2 Orkas-AI/Orkas

| 등급 | 내용 |
| --- | --- |
| **A** | REST tree(SHA 고정, `truncated:false`) **3,114 엔트리 / 2,769 blob / 비압축 244,423,647B**. tarball 멤버 순회와 함께 제외 규칙 적용: `vendor/`→NUL 바이너리→3MB 초과→그 외. 최종 스캔 **2,705파일 / 1,293,547줄**(디코드 106,084,897B). 상한 압축 67.9MiB/100MiB, 비압축 233MiB/500MiB(미도달) |
| **B** | 패턴 `invit|delegat|dispatch|mention|participant|membership` + CJK(邀请/成员/权限/审批/委托), 대소문자 무시. 범위·hit: `src/` 843파일/458,845줄(invite 132hit/35파일, member 960/115, delegat 1,677/209, permission 7,104/276) · non-vendor 2,705파일/1,293,547줄(invite 319/68, delegat 269/86, dispatch 2,593/421, mention 942/128, participant 88/21, membership 432/74) · `group_chat/` 본문 19파일/20,343줄(invite **0**) · `group_chat` 테스트 32파일/28,148줄(invite 1=오탐). 초대 관련 hit 319건은 유형 분류 완료, dispatch/permission 다수 hit는 미열람 |
| **C** | `src/main/features/group_chat/` 19파일(주석·구조 확인, `bus.ts`는 11,898줄 중 툴 정의·핸드오프부), `router.ts:16-54`(멘션 파서), `dispatch_planner.ts:1-24`, `actor-budgets.ts`, `locks.ts:1-20`, `visibility.ts:11-22·60-80`, `permissions.ts:1-34`, `operator-policy.ts:1-30`, `granted_roots.ts:1-20`, `connectors/action_policy.ts:1-25`, `local_agents/cli_permissions.ts:1-50`, `chats.ts:155-235`, `projects.ts:12-14·263`, `projects.js:1040-1068`, `README.md` 전문(25,491B), `LICENSE`, `package.json` 헤더, `docs/test/permissions-config-regression.md`·`conversations-regression.md` 서두 |
| **U** | 미실행(프로젝트 코드 실행 0, npm/electron/테스트 0). 미디코드 64파일(vendor 6·바이너리 39·3MB 초과 19). 미정독: `conversation.js`(825KB), `ipc/index.ts`(205KB), `agents.js`(208KB), `bus.ts` 약 11,000줄, `docs/test/*` 18개 문서 나머지. **클로즈드 빌드의 실제 초대 모델은 검증 불가**(문서·문자열 근거만). **로컬 사본 미보존** → 고정 SHA URL로 다시 내려받아 재검증 필요 |

### 3.3 rlaope/deepseek-bot

| 등급 | 내용 |
| --- | --- |
| **A** | tree recursive=1 → paths 202 / blobs 186 / trees 16. 확장자 화이트리스트(.ts/.mjs/.js/.json/.md/.sh/.yml/.yaml/.css/.html/.txt/.svg/.patch/.toml + 확장자 없는 실행 스크립트), 바이너리 확장자·3MB 초과·총 40MB 예산 적용. **184/184 텍스트 수집, 48,753줄, 3,039,451B, 실패 0** |
| **B** | 184파일 전체(src 101파일/25,844줄 · tests 80파일/21,130줄 · meta 3), 제외는 바이너리 2개. 4개 주제 정규식(대화/초대/권한/위임 + 한·중 키워드). hit: A_대화 4,683(전체)/2,230(src 63파일) · B_초대 1,848/365(57) · C_권한 693/339(44) · D_위임 388/208(28). 상위 hit 파일만 사람이 확인, **나머지 174파일은 기계 집계만** |
| **C** | 전문: `injection.ts`(1-93), `docs/limitations.md`(1-119), `LICENSE`(1-4) — `README.md`(1-40)·`SPEC.md`(50-100)는 부분. 구간: `transports/local.ts:430-470`, `netPolicy.ts` 주요 구간(1-11·13-26·82-107·109-130·132-149·160-181·194-201), `safeWrite.ts:1-14·28-60`, `index.ts:101-520·1820-3320` hit 구간, `service/gateway.ts:6-24·262-422·639-670`. 추가 구간 정독(확장 스캔): `docs/setup.md:110-125·390-400`(509줄 중), `docs/how-it-works.md:326-346`(1,257줄 중), `tui/app.ts:43,484`. `messages.ts`·`types/transport.ts`는 grep 확인 |
| **U** | 미실행(빌드/테스트/ts 실행 0). 바이너리 2개 미디코드. 미정독: `index.ts`(4,488줄), `service/gateway.ts`(1,828줄), `test/` 80파일(21,130줄), `tui/app.ts`(1,923줄 중 일부 구간만). 샌드박스 러너(Seatbelt)·브라우저·MCP 실동작 미검증. "앱 내부 데이터 모델 기준 초대 엔티티 없음"은 스캔 범위 한정이며, **운영 문서에는 플랫폼 초대(2단계) 흐름이 명확히 있다**(§4.3) |
| **정독/스캔 요약** | **정독 5파일(끝까지) + 구간 정독 7파일 추가 / 기계 집계 184파일** — 48,753줄/3,039,451B. 파일 수 변동 없음 |

### 3.4 deepseek-ai/dsh-superagent-prototype

| 등급 | 내용 |
| --- | --- |
| **A** | tree recursive=1 → paths 236 / blobs 216 / trees 20 → **216/216 수집, 37,068줄, 2,576,022B, 실패 0**. `pnpm-lock.yaml`은 수집했으나 meta로 집계 제외 |
| **B** | 216파일 전체(src 150파일/18,806줄 · tests 65파일/14,960줄 · meta 1), 동일 4주제 패턴. hit: A_대화 4,408/2,395(116파일) · B_초대 992/410(74) · C_권한 1,850/949(97) · D_위임 2,731/1,436(98). 실제 확인: `src/policy.ts`(B32/C65), `src/executor/dsh.ts`(D195), `src/core/tickets.ts`(D74), `src/core/assignments.ts`(D46), `src/core/evals.ts`(A112), `docs/007-architecture.md`, `prompts/*`. **나머지 약 190파일은 기계 집계만** |
| **C** | 전문: `README.md`(39), `docs/001-domain.md`(112), `docs/002-authorization.md`(155), `prompts/admission.md`(68), `prompts/team/communication.md`(97), `prompts/team/scope.md`(22), `prompts/team/delivery.md`(28), `src/policy.ts`(111-242, 1-110 선언부), `src/limits.ts`(11), `LICENSE`(1-4). 구간: `docs/003-execution.md:20-80`, `dsh.ts`·`tickets.ts`·`assignments.ts` hit 구간. 추가 구간 정독(확장 스캔): `docs/007-architecture.md:120-150`(617줄 중), `src/core/evals.ts:90-105`, `src/sources/feishu-read.ts:35-62`, `src/app/settings.ts:1-6`. 미정독: `docs/007`·`docs/008-cookbook.md`(425) 나머지, `src/client/locales.ts`(770, 번역 사전), `src/app/migration.ts`(616), tests 65파일(14,960줄) |
| **U** | 실행·테스트 0. Feishu/GitHub Source 어댑터 실동작 미검증. "초대 엔티티 없음"은 소스에서 invite 모델을 못 찾았다는 범위 한정 주장(전 파일 `/invit/` 1건·무관, 참여는 `allowed` 목록+admission table로 표현). 단일 커밋 프로토타입이라 이력·설계 의도 추적 불가 |
| **정독/스캔 요약** | **정독 10파일(끝까지) + 구간 정독 7파일 추가 / 기계 집계 216파일** — 37,068줄/2,576,022B. 두 저장소 합계 400파일/85,821줄/5,615,473B, 수집 실패 0. 전문 정독 파일 수는 이번 확장 스캔에서 늘지 않음 |

---

## §4 초대·권한·승인 핵심 관찰 (분석가 회신 기준 file:line)

### 4.1 OpenMausBot — 허용목록 초대 + 일회용 코드 + provider 위임 승인

- **정정: OSS는 초대를 직접 구현한다.** `signIn.{admins,members}` 허용목록 + `/pair?email=` 초대 링크가 OSS 코드에 있고
  (`src/components/PeopleSection.tsx:246-256`, `server/people-invite.test.ts:161-232`), 조직 단위 초대·SSO만 별도
  Admin(openmaus-cloud, `OMB_ADMIN_MEMBERSHIP=portal`)로 위임된다. **이전의 "초대 미구현" 기술은 파일명 문자열 검색
  아티팩트로 인한 오류**이며 폐기한다.
- 초대 링크는 편의일 뿐이며 "one-time code still goes to the address itself"라고 파일 서두에 적혀 있다(`:1`, `:30`).
  portal 모드에서는 readOnly + Admin 링크로 바뀐다(`:260`). 도메인 단위 초대·역할 변경·제거 시 신규 로그인 차단은
  `docs/self-hosting.md:664-678`.
- **pending 초대 레코드는 없다**: 초대는 `signIn.{admins,members}` 허용목록에 즉시 추가되는 config 방식이다
  (`PeopleSection.tsx:246-256`, `:30`, `:237`). **allow-list 항목 자체엔 TTL이 없다**(수동 제거까지 유효).
  만료되는 것은 **페어링 코드**뿐이다: 60-bit·12자·TTL 5분·single use(`server/sessions.ts:12-14`), stream ticket 5분
  (`:30`), 세션 30일·절반 남으면 갱신·최대 180일(`:21-25`), lockout 10회/60초·세션당 ticket 5개(`:32,35`).
- **철회 경로가 둘로 갈린다**: A(self-hosted) 제거·권한상실 시 세션·스트림·티켓 **즉시 폐기**, 재초대에도 부활 없음
  (`docs/verification/people.md:9-12`, `server/people-invite.test.ts:215`), member 자기 권한 상승 403(`:192`),
  와일드카드 도메인(`:232`). B(team/portal) `teamRole`+`membershipAuthority:"team"` 세션은 sign-in 목록 변경이
  재검증·철회하지 않는다(`docs/team-cloud.md:220`). 일괄 철회 API `revokeMember(email, keepRole?)`가 전 세션을 폐기하고
  `onSessionRevoked` 리스너가 있다(`server/sessions.ts:611-625`, `:286`).
- **승인은 provider 소유, OMB 자체 판정 없음(코드로 확정)**: "Nothing here decides whether an action is safe … A bot's
  question always reaches a human."(`server/auto-approve.ts:1-4`), "the engine's own permission system … is the only thing
  that asks"(`.../approvals-and-inspector.mdx:19`), "Approval cards are the provider's own … OpenMausBot adds none"
  (`docs/self-hosting.md:682-683`). 만료된 ask는 새 Allow/Deny 카드로 **부활 금지**(`auto-approve.ts:11-12`).
  Full access는 송신자로부터 상속되지 않고, 위임 시 `delegatedApprovalMode`가 낮은 등급(min)을 고른다(`:29-57`).
  5단계 랭크(`shared/approval-mode.ts:5,43,59-61`), 카드 응답 자격 4규칙(`self-hosting.md:680-701`), 허용목록
  3종(once/session/정확한 command)(`PendingApproval.tsx:47-57`), answered/dismissed/**expired** 제외(`:50`).
- **trust model(초대 권한의 하한)**: 브라우저는 루프백 신뢰 불가, 원격은 페어링 세션 필수(scope `admin|client`),
  루프백 `service` 트러스트는 people·settings·keys·sessions·pairing 라우트 접근 불가 + 카드 승인/응답 불가
  (`.oss-scanner/threat_model.md:44-95`, 특히 `:78-82`; 페어링 코드 single use `:66-67`). 턴 스코프 자격증명은
  턴 종료·새 턴 시작 시 폐기(`:73`).
- **fail-closed 기본값**: 미인식 audience 값은 `admins`로(`server/bot-visibility.ts:52-57`), 쓰기는 자기 audience로만
  (`server/audience.ts:40-42`), Admin도 타인 private 불가(`:51-54`), 동시성 거절 코드(`server/admission.ts:16-25`),
  좌석 affinity TTL 30분·만료 좌석 회수(`server/local-vm-seat-pool.ts:6,34-41`).
- **U**: 2차 스캔 완료(§3.1). 미정독: `server/index.ts`(931KB) 전반, `server/delegations.ts`, `server/host/person-inbox.ts`,
  `server/delta-context.ts`, `src/state/store.tsx`. enterprise/portal 초대는 이 repo 밖이라 미확인.

### 4.2 Orkas — 스캔 범위에서 인간 초대 미발견(문서상 상용 전용) + 대화 참가자 멤버십 + 호스트 강제

- **스캔·확인 범위에서 인간 초대 구현을 찾지 못했고, 문서상 상용(commercial edition) 전용**: 초대는 commercial edition 기능으로
  문서화되어 있고(`.../editions.md:8-9`),
  OSS UI는 `_showProjectMemberInviteGate()`로 commercial_only 확인창만 띄운 뒤 다운로드 페이지를 연다
  (`src/renderer/modules/projects.js:1053-1068`). i18n 문자열(`locales/*.json:2477-2480`)과 README 문구
  ("only in the packaged app — the open-source build doesn't support them yet", `README.md:88,91`)만 있다.
  invite 관련 hit 319건은 유형 분류 결과 실 초대 코드 0이었다.
- **membership = 대화 참가자**(사람 ACL 아님): `chats.ts:160-219`, `project_id`는 생성 시 고정(`chats.ts:168-173`).
- **위임 3종 구분**(비종결/종결/익명) `bus.ts:679,9682-9743,9944-9960`; 분해안은 모델이지만 검증·기록은 호스트(D12)
  `dispatch_planner.ts:1-24`; 멘션 파싱은 CJK 경계·longest-first `router.ts:16-54`.
- **권한·승인 fail-closed**: 권한 3모드 + 손상 시 안전 기본값 `permissions.ts:1-34`; 승인은 fail-closed,
  메모리 전용 grant, 10분 자동 deny `cli_permissions.ts:1-50`; 툴 라운드 예산 500·동시 LLM 10
  (`actor-budgets.ts`, `locks.ts:1-20`); 툴 실행 사실은 카운트만(인자·경로 미저장) `visibility.ts:60-80`.
- **U**: 클로즈드 빌드의 실제 초대 모델은 검증 불가. `conversation.js`·`ipc/index.ts`·`agents.js`·`bus.ts` 약 11,000줄 미정독.

### 4.3 rlaope/deepseek-bot — 플랫폼 초대(2단계) + 허용목록 + 승인

- **정정: "초대 엔티티 없음"은 앱 내부 데이터 모델 기준이었다.** 운영 문서에는 초대 흐름이 명확히 있다.
  `/invite`로 봇을 채널에 초대해야 하며 "A bot cannot see a channel it has not been invited to"가 가장 흔한
  조용한 실패라고 적혀 있다(`docs/setup.md:117-118`). `targetChannels`는 채널 id 또는 `'*'`("every channel the
  bot was invited to")이고 **빈 목록이면 플러그인이 기동을 거부**한다(fail-closed, `docs/setup.md:395-400`;
  `index.ts:528`). allowlist 밖에서는 아무것도 하지 않는다(`index.ts:3477`). 플랫폼별 안내: Slack `/invite @<bot>`
  (`service/gateway.ts:1508`), Discord "invite it to a server"→OAuth 초대 링크(`:1548-1552`), QA 스크립트의
  "the bot is not in #x — invite it first"(`tools/qa-slack.ts:77`). 초대된 봇+`'*'`+`replyMode: all`이면 모든 사람
  메시지에 답한다는 경고도 있다(`docs/limitations.md:81`).
- **초대 ≠ 권한**: 초대 후에도 `allowedUsers`가 비면 "empty: anyone who can reach the bot"(`index.ts:480`)이다.
  즉 **초대는 도달 범위**를 정하고, **권한은 허용목록·승인**이 정한다(타입·문자열 검증 `index.ts:476-480`,
  프로필의 platform/targetChannels/allowedUsers 쓰기·읽기 `service/gateway.ts:262-264,382-422`,
  `--channels/--users` `gateway.ts:639-670`, `README.md:137-138`).
- **승인 기본값의 뉘앙스(fail-closed)**: shipped profile `defaultPreset: unattended` = `{sandbox: workspace-write,
  approval: never}`이고, "an escalation is refused rather than asked, because there is nobody to answer"라고
  설명한다(`docs/how-it-works.md:341-346`). 채팅 승인은 `approval: ask` **그리고** api-remotes 비활성 두 조건이
  모두 필요하며, 아니면 요청이 영원히 pending이 된다. 버튼이 있으면 버튼, 없으면 `허용`/`거부` 텍스트, 기본은
  요청자 응답만 인정(`approvalRequireSameUser`), 10분 미응답 취소(`approvalTimeoutMs`), 턴 자체는 15분
  (`runTimeoutMs`)(`:328-339`). 파괴적 패턴 `rm `·`rmdir `·`sudo `·`dd if=`·`mkfs`·`shutdown`·`reboot`·`kill -9`
  (`index.ts:213`), 승인 대상 도구 `bash`,`pwsh`(`:214`), 만료 600,000ms(`:254`), 무멘션 응답은 승인 버튼 문구만
  (`:432`), 버튼은 대화·요청ID에 바인딩(`transports/local.ts:442-449`), 무응답 시 "denied after N min"(`messages.ts:19`).
- **권한 경계**: 에이전트 자체 명령은 fetch 게이트 밖(`commandNetwork` 소관, `index.ts:243`), MCP는 모든 게이트 밖
  (`docs/limitations.md:57-62`), 읽기 제한은 커널이 아닌 정책(`:30-40`).
- **대화 제어 어휘**: `cancel/quiet/usage/approve/deny`(`messages.ts:128-130`), quiet 대화는 멘션·답장·DM만 수용(`types/transport.ts:51`).
  위임: 하위 에이전트 스트림은 이 대화의 것이 아님(`index.ts:2109,3204`). 자동 처리 `maxConcurrency: 2`(`index.ts:158`).
- **U**: "초대 엔티티 없음"은 **앱 내부 모델 한정**. 운영 문서의 플랫폼 초대 흐름과는 충돌하지 않는다. 미정독 대형 파일
  반례 가능성 있음(§3.3). 샌드박스·브라우저·MCP 실동작 미검증.

### 4.4 deepseek-ai/dsh-superagent-prototype — admission 강도 + 불변 Mandate + 정책 버전 무효화

- **초대 개념 없음, 게이트로 대체**: 전 파일 `/invit/` 검색 결과 1건은 이 맥락과 무관했다. 참여는 Source별
  `allowed` 목록(`src/policy.ts:119-127`) + admission table 3셀(`policy.ts:135-141`, `docs/002-authorization.md:56-75`)이다.
  강도 explicit/inferred/not admitted(`002:46-54`), 봇은 inferred까지만 수용(`002:77-81`).
- **Mandate basis 4종**: `addressed`(허용 발신자) / `followup`(스레드 후속) / `admission`(표 셀, `observation`=inferred) /
  `bot`(목록된 봇의 inferred 후보)(`docs/007-architecture.md:127-132`).
- **gate는 trigger 단위 배치**: 가장 강한 strength를 채택하며(`src/core/evals.ts:17,96-100`), `admitsInferred`는
  **auto + 활성 규칙**일 때만 성립한다(`007:143`).
- **수락은 불변 Mandate+Assignment**(`docs/001-domain.md:34-36`), Executor는 **승인 없이** 운영자 OS 사용자로 명령 실행
  (`003:27`), blocked commands는 이름 호출만(`003:70,78`), 실행 변수는 패널만 변경(`003:76`), 위임 프롬프트 상한
  `ASSIGNMENT_CHARS=1500`·`TRIGGER_CHARS=600`(`executor/dsh.ts:113`).
- **pending/만료/철회**: Mandate를 바꾸는 연산 3개(accept·사람 stop·재검증 실패)(`002:129`), Eval 만료 2회 후 unavailable
  (`002:147-155`), **정책 버전 변경 시 실행 Eval 폐기·라이브 티켓 통지·재검증**(`002:107-117`; `policy.ts:111-118,213-239`).
  정책 편집은 `policyVersion`+`policyEffects` 기반 낙관적 동시성 저장이다(`src/app/settings.ts:1-6`).
- **소스 컨텍스트 앵커**: 소스 읽기는 **accepted trigger에 앵커**되며 chat_id가 불일치하면 거부한다
  ("Resource references are anchored to an accepted trigger instead of arbitrary chat or message IDs",
  `src/sources/feishu-read.ts:35,39,62`).
- **fail-closed**: gate가 `admissible:false`+사유(`policy.ts:173,185,210`), off 모드는 inferred 미수용(`002:97`).
  자동 실행/모델: 기본 observe(`002:98`), auto는 규칙 1개 이상 일치 필요(`002:101`), 평가 단계 read-only(`prompts/admission.md:3-4`).
- **위임 경계**: 상태-only 요청은 구현 재개 금지(`core/tickets.ts:21`), 정책 변경 시 "남은 작업이 assignment 범위 내인지 확인"(`dsh.ts:139-145`).
- **U**: 실행·테스트 0, Source 어댑터 실동작 미검증. 단일 커밋 프로토타입.

---

## §5 TalkAbout 현행 구현 비교 (권한 안전)

비교 기준은 2026-11-04 워크트리다. 인용은 TalkAbout 코드/문서에서 직접 확인한 값이다.

**초대 주체로 나눈 3유형**(비교축: 누가 초대 권한을 갖는가 / pending→active 전이 / 초대 후 권한 부여 시점):

| 유형 | 저장소 | 초대 주체 | 전이·권한 부여 |
| --- | --- | --- | --- |
| A. 플랫폼 위임형 | deepseek-bot | **플랫폼**(Slack `/invite`, Discord OAuth 초대)이 채널 도달 범위를 정하고, 제품은 허용목록·승인만 본다 | pending 상태기계 없음. 초대 ≠ 권한(`index.ts:480`), 권한은 `allowedUsers`+승인 |
| B. 게이트 승격형 | dsh-superagent | **초대 없음.** Source별 admission table이 receiving scope를 승격 | Mandate/Assignment 수락, strength explicit/inferred, 정책 버전 변경 시 재검증 |
| C. 제품 내부 상태기계형 | TalkAbout | **사람**(`room:manage_members`)이 초대 발행 | `status="pending"`→accept(human 확인)→active, epoch·`read_start_seq` 부여, `membership_revision` |

| 능력 | TalkAbout 현재 | 4개 저장소 관찰 | 판정 |
| --- | --- | --- | --- |
| 초대 발행 권한 | 사람만, `room:manage_members`(`talkabout/core.py:396-397,406-407`; `docs/API_DRAFT.md:29`; `docs/PERMISSIONS.md:26,49`). 초대는 `status="pending"` | OpenMausBot config 허용목록+pair code, Orkas 스캔 범위에서 미발견(문서상 상용 전용), deepseek-bot 플랫폼 초대(2단계)+허용목록, dsh invite 없이 admission 게이트 | **대조 유지** — pending 레코드+감사가 TalkAbout 강점 |
| pending의 권한 상승 방지 | `status="active"`로 직접 올리려면 기존 active/blocked 이력 필요, 자기승격은 `invitation_required` 422(`core.py:593-595`). 멤버 상한 16(`:585`), 재초대·상태 변경 시 epoch/`read_start_seq` 재설정(`:596-599`) | OpenMausBot 제거 즉시 세션 폐기·재초대 부활 없음(`people-invite.test.ts:215`); dsh는 admission 강도로 상한; deepseek-bot은 허용목록 | **대조 참고**: TalkAbout은 read_start_seq로 과거 이력 차단 — 4곳 중 가장 명시적 |
| 수락 확인 | `expected_revision`+`acknowledge_private` 필수, 실패 422(`core.py:733-735`), 성공 시 active·epoch·`read_start_seq=last_seq+1`(`:736-738`), replay 멱등(`:730-732`), 방 상태 제한(`:734`) | OpenMausBot 일회용 pairing code(5분·single-use, `sessions.ts:1,12-14`); dsh Mandate 수락 | **적용 후보**: pending 초대에 짧은 TTL/1회용 확인 개념 검토(현재 core에는 초대 만료 없음) |
| pending의 읽기 범위 | **현행 동작: pending 대상도 수락 전에 초대 응답으로 topic·mode를 이미 본다** — `invitations`가 `room_id`·`topic`·`mode`·`membership_revision`·고지를 반환한다(`core.py:202-209`, 특히 `:205-207`; `API_DRAFT.md:20`). 정식 room history는 `read_start_seq`로 차단(`core.py:85,105,136,144`) | Orkas membership=대화 참가자(사람 ACL 아님), OpenMausBot 세션 단위 | **대조 유지** — TalkAbout이 더 좁고 명시적. topic 노출은 "인간 동의"가 아님을 문구로 확인 필요 |
| 제거·차단·재가입 | status `blocked/left`, epoch 증가, 새 `read_start_seq`(`core.py:594-599`); handover만 전체 이력 특례(`:270-271`) | OpenMausBot 제거 즉시 세션·티켓 폐기·재초대 부활 없음(`people.md:9-12`) | **적용 후보**: "제거 즉시 세션 폐기"를 credential/session 계층에도 명시적으로 연결할지 검토 |
| 승인(control) | control은 pending → human `room:approve` 승인(`core.py:604-624`; `MODERATION_AND_SECURITY.md:15`), stale revision 409 | OpenMausBot provider 위임(OMB 추가 없음), Orkas fail-closed+10분 deny, deepseek-bot 600s 만료+동일인, dsh 정책 버전 무효화 | **대조 참고**: TalkAbout은 control 승인을 자체 보유, tool broker는 single-use/expiry(`tool_broker.py:179`; `TOOL_BROKER_PLAN.md:85,117`) |
| 승인 만료·철회 | `tool_broker.py:204-211`(approve), `:219-230`(revoke), `:232-236`(_approval_current), `approval.py:122,158`(만료·single-use). **도구 승인 broker의 현행 구현에는 정책 버전 스탬프가 없음**(신규 bot-invite 정책은 revision/generation 스탬프를 사용) | dsh: 정책 변경 시 Eval 폐기·재검증(`policy.ts:111-118,213-239`); deepseek-bot: 600초 만료·동일인 승인·무응답 denied(`index.ts:254,257`; `messages.ts:19`) | **적용 후보**: 승인 레코드에 정책/멤버십 revision 스탬프 — task-15의 revision 재검증 전제와 연결 |
| 봇/에이전트가 초대하는 경로 | **기본 off 구현 착지, 검증 중**(human `room:manage_members`가 정책 grant, agent는 room-bound credential로 pending 초대, 수락은 **대상 에이전트의 별도 수락**이며 인간 확인으로 간주하지 않음 — MCP `accept_invitation`을 모델이 호출할 수 있고 `acknowledge_private:true`는 인간 동의 증거가 아니다. 초기 read-only, 실행권 승격은 별도 사람 확인). 초대 자체에 TTL은 없고 정책 grant에 generation·expiry·사용예산이 붙는다(§7 제한) | **스캔 범위(§7)에서는** 4곳 모두 "에이전트 자율 초대"를 OSS 기본 기능으로 찾지 못했다. dsh 기본 observe, deepseek-bot 승인 ask, OpenMausBot 관리자 설정 | **대조**: task-15의 human 승인 전제가 관찰 범위와 일치 |

대조 결론(개념만, 구현 지시 아님):

1. **"초대"와 "접근 허용"은 다른 문제다.** deepseek-bot은 **플랫폼 초대(도달 범위)+허용목록(권한)**의 2계층이고,
   dsh는 초대 없이 admission 게이트로 승격한다. TalkAbout은 pending 초대+accept+read_start_seq로
   더 명시적이지만, **초대 만료·정책 버전 스탬프**는 4곳에서 관찰되는 보완 아이디어다.
2. **제거·권한 상실의 즉시성이 핵심이다.** OpenMausBot은 세션·ticket 즉시 폐기를 테스트까지 갖췄다. TalkAbout은
   membership status·epoch·read_start_seq로 막지만 credential/session 수명과의 결합은 별도 검토가 필요하다.
3. **조사 범위(§7)에서는 에이전트 자율 초대가 4곳 모두 OSS 기본 기능으로 확인되지 않았다.** dsh 기본 observe, deepseek-bot은 승인 정책 기본값이 unattended(채팅 ask는 조건부),
   OpenMausBot은 관리자 설정, Orkas는 상용 기능. TalkAbout task-15의 "사람 승인 기반 기본 비활성 위임" 전제와 방향이 같다.
4. **승인 위임 금지**: OpenMausBot은 승인을 provider에 위임하고 OMB가 추가 판단하지 않는다. Orkas·dsh는 승인을
   fail-closed로 호스트가 강제한다. TalkAbout은 control 승인과 tool broker 승인을 분리해 자체 판정한다.

---

## §6 라이선스 준수·복사 0 기록

| 저장소 | 라이선스 | 코드 도입 | 고지 의무·주의 | carve-out 준수 |
| --- | --- | --- | --- | --- |
| deepseek-bot | MIT | 없음(복사 0) | 도입 시 저작권·허가 문구 유지 | 상용 carve-out 없음. 바이너리 2개 미디코드 |
| OpenMausBot | Apache-2.0 + enterprise license | 없음(복사 0) | 도입 시 Apache 고지·NOTICE·변경 표시·특허 조항 검토. enterprise 코드는 별도 유료 라이선스 | enterprise 미열람, third_party 집계만, 비텍스트 제외 |
| dsh-superagent-prototype | MIT | 없음(복사 0) | 도입 시 고지 유지 | 상용 carve-out 없음. "research prototype"은 면책 문구(라이선스 carve-out 아님) |
| Orkas | MIT(공개 코드); 상용 전용 기능은 공개 코드 밖 | 없음(복사 0) | MIT 고지. 상표("Orkas"/orkas.ai)는 별도 허가 필요, 코드 라이선스가 상표를 허여하지 않음. **공개 코드에 엔터프라이즈 예외 라이선스 조항은 확인되지 않음** | vendor 스캔 0, 생성 계약은 데이터 취급, 상용 예약 영역 미열람 |

- **외부 코드·asset 복사 0**(코드·생성물·asset 재사용 없음)과 **출처를 밝힌 짧은 인용**은 구별한다. 이 문서의 인용은
  경로/줄/상수/1줄 이하 문서 문구와 구조 요약이며, 외부 원문 블록 전재는 없다. 분석가 회신도 같은 규칙을
  확인했다(handy·t3code·claude-mem-analyst). 짧은 인용은 비평·대조 목적의 범위로 한정했고, 제품 코드·asset에
  편입한 것은 없다.
- 기존 TalkAbout 라이선스 기록(`docs/SOURCES.md`, `THIRD_PARTY_NOTICES.md`)은 **수정하지 않았다**. 이 문서는
  analysis 세션 기록이며 제품 문서·인덱스를 갱신하지 않는다(lead 지시).
- 4곳 중 어느 저장소와도 제휴·보증 관계가 없고, "구현되어 있다"는 관찰은 동작·보안·성능의 증거가 아니다.

---

## §7 미확인·한계·재현

| 항목 | 상태 |
| --- | --- |
| OpenMausBot 수치 | 2차 전수 스캔: **5,650파일/68.9MiB**(초과·중단 없음, 상한 200MiB/1GiB). 1차 5,659파일/69.0MiB와 차이 **9파일/142,914B** — 2차에서 TEXT 확장자 집합에서 `.css`·`.plist`·`.example` 제외(집계 규칙 변경). 전문 **3** / 구간 **22** / 힛만 **5,625**(2차 5,650−25). 1차 1,010파일·100MiB 중단 이력은 당시 상한. 리뷰어 독립 재확인 대기 |
| OpenMausBot 미열람 | enterprise 소스 8경로(라이선스·FEATURES만), third_party 42경로, 비텍스트 1,022파일/144.9MiB, 텍스트 5,625파일(hit만), 이슈/PR/릴리스/diff, `server/index.ts`(931KB) 전반·`delegations.ts`·`host/person-inbox.ts`·`delta-context.ts`·`src/state/store.tsx`. `server/hosted-access.ts`는 파일 미존재(테스트만) |
| Orkas | **로컬 사본 미보존**(회신 기반) → 고정 SHA URL로 다시 내려받아 재검증 필요. 미디코드 64파일, 대형 파일 4개 미정독, 클로즈드 빌드 초대 모델 검증 불가 |
| deepseek-bot | 실행·테스트 0, 바이너리 2개 미디코드, 미정독 대형 파일(4,488줄 index.ts 등). **정독 5 + 구간 정독 7 / 스캔 184파일**(48,753줄/3,039,451B). "앱 내부 초대 모델 없음"은 스캔 범위 한정(운영 문서 플랫폼 초대와 별개). 샌드박스·브라우저·MCP 실동작 미검증 |
| dsh-superagent-prototype | 실행·테스트 0, Source 어댑터 실동작 미검증, 미정독 docs 2편·locales·tests 65파일. **정독 10 + 구간 정독 7 / 스캔 216파일**(37,068줄/2,576,022B). 단일 커밋 프로토타입 |
| DeepSeek 2곳 합계 | 400파일 / 85,821줄 / 5,615,473B 수집 실패 0. **정독 15파일(끝까지) + 구간 정독 7파일 추가 / 스캔 400파일** — 나머지는 기계 집계 |
| 고정 URL | §부록 B의 blob URL은 분석가 제공값이며 이 문서 작성자는 열람·검증하지 않았다(U) |
| 공통 | 4곳 모두 빌드·테스트·설치·실행 NOTRUN. 성능 수치 없음. "없음" 주장은 해당 SHA·스캔 범위 한정. B는 기계 집계이며 사람이 연 파일은 상위 hit 일부. 외부 file:line은 분석가 확인값이며 이 문서 작성자가 재열람하지 않았다 |
| TalkAbout 비교 | 2026-11-04 워크트리 기준. task-15는 기본 off로 구현됐고 memory+격리 PG 검증 중이라 라인 번호·동작이 바뀔 수 있음. 봇 초대의 **의도적 제한**은 아래 세 행 참조 |
| task-15 의도적 제한(2) | 현재 **유일 human 관리자의 탈퇴/차단**은 기존 계약상 허용되어 방이 관리 불능이 될 수 있다. 이번 task-15 수정에서 막지 않았고, 관리자 상실 복구·이전(handover) 자동화도 보장하지 않는다 |
| task-15 검증 범위 | memory+격리 PG에서 happy·projection·`/me` ACL·topic/generation 변경·credential/policy revoke·grant/revoke replay(만료·stale generation 포함)·동일 key weak-credential replay·cross-workspace/self/human/기존 멤버/room-binding/scope 거절·read-only 수락 후 scheduler tick 무-task·동시 중복 accept(memory) 통과. **headroom rollback, 실제 스케줄러/pool 장기 경합, X-프로세스 동시성은 미검증** — 해당 보장을 주장하지 않는다 |

재현 절차(후속 검증용):

1. `https://codeload.github.com/<org>/<repo>/tar.gz/<SHA>`를 메모리로 스트리밍(디스크 저장 금지).
2. `https://api.github.com/repos/<org>/<repo>/git/trees/<SHA>?recursive=1`로 경로·blob 크기 열거(A).
3. 제외 규칙(§3 저장소별) 적용 후 텍스트만 대상으로 정해진 패턴을 대소문자 무시 검색(B). hit와 정독을 분리 기록.
4. 인용할 파일만 본문 읽기(C)로 승격하고 줄 범위를 남긴다. 읽지 않은 영역은 U에 적는다.
5. `LICENSE`·`NOTICE`·`LICENSING.md`를 pinned SHA에서 직접 열어 SPDX·저작권자·carve-out을 확인한다.

---

## 부록 A. 회신·출처 기록

| 일시(KST) | 출처 | 내용 |
| --- | --- | --- |
| 2026-11-04 | lead | 4개 저장소 범위 확정(deepseek-bot·OpenMausBot·dsh-superagent·Orkas), 신규 문서 1파일 승인, DeepSeek 두 곳 고정 SHA·MIT 확인, OpenMausBot 초대 구현 정정(이전 "초대 없음"은 오류) |
| 2026-11-04 | handy-analyst | deepseek-bot(`212dba4…`, MIT) · dsh-superagent(`8c32a9d…`, MIT): A/B/C/U·초대/권한/승인 사실·미확인 회신. 두 저장소 모두 전수 텍스트 스캔 완료 |
| 2026-11-04 (2차) | handy-analyst | 고정 blob URL 목록·최종 수치: deepseek-bot 184파일/48,753줄, superagent 216파일/37,068줄, 합계 400파일/85,821줄/5,615,473B. 정독 15파일/스캔 400파일 구분 권장 |
| 2026-11-04 (3차) | handy-analyst | **정정**: deepseek-bot "초대 엔티티 없음"은 앱 내부 모델 기준이고 운영 문서에는 플랫폼 초대(2단계) 흐름이 있음(`setup.md:117-118,395-400`). 승인 기본값 unattended=fail-closed 뉘앙스. superagent 초대 없음(`/invit/` 1건·무관)·Mandate basis 4종·gate·정책 버전. 확장 스캔: 정독 끝까지 수 불변, 구간 정독 7파일 추가 |
| 2026-11-04 | t3code-analyst | OpenMausBot 회신+정정 패킷: SHA `99b7093…`, Apache-2.0+enterprise carve-out, 정정 문장, blob URL, A/B/C/U |
| 2026-11-04 (2차) | t3code-analyst | OpenMausBot 2차 전수 스캔: 5,650파일/68.9MiB(1차 5,659/69.0MiB와 차이 9파일/142,914B — `.css`·`.plist`·`.example` 제외), 전문 3/구간 22/힛만 5,625. 철회 경로 A(self-hosted 즉시 폐기)/B(portal 위임)·trust model(`threat_model.md:44-95`)·승인 자동화 없음 확정(`auto-approve.ts:1-4`) |
| 2026-11-04 | claude-mem-analyst | Orkas 회신 2회: SHA `07df324…`, MIT(공개 코드)·상용 전용 기능은 공개 코드 밖, A/B/C/U, 초대 미발견 근거, 로컬 사본 미보존 |
| 참고 | — | claude-mem/handy/t3code R3 성능 조사는 이 문서 범위 아님 — 별도 `analysis/r3-*.md` |

## 부록 B. 고정 blob URL 색인 (분석가 제공, SHA 핀)

URL 패턴: `https://github.com/<owner>/<repo>/blob/<SHA>/<path>#Lx-Ly`. 아래는 분석가가 제시한 SHA 고정 URL이며,
이 문서 작성자가 직접 열어 확인하지는 않았다(§7 U).

### B.1 rlaope/deepseek-bot — `212dba4b39fe75d2c042126533977fe985d7f174`

| 주장 | URL(경로 + 라인) |
| --- | --- |
| LICENSE(MIT) | `LICENSE#L1-L3` |
| 참여 기본값 "도달 가능한 누구나" | `index.ts#L151-L158`, `#L476-L480` |
| 승인 정책 ask·파괴적 명령·도구·600s 만료·동일인 승인 | `index.ts#L195-L214`, `#L254-L257` |
| 승인 버튼 문구 외 무멘션 응답 불가 | `index.ts#L432` |
| fetch 허용 호스트 검증 | `index.ts#L518-L520` |
| 채널/사용자 목록 기록·읽기 | `service/gateway.ts#L6-L24`, `#L262-L264`, `#L382-L422` |
| 승인 버튼 대화 바인딩 | `transports/local.ts#L442-L449` |
| quiet 대화는 멘션·답장·DM만 | `types/transport.ts#L51` |
| 제어 어휘(cancel/quiet/usage/approve/deny, ko/zh) | `messages.ts#L128-L130` |
| 무응답 = 거부 문구 | `messages.ts#L19` |
| MCP는 모든 게이트 밖·읽기 제한은 정책 | `docs/limitations.md#L30-L40`, `#L57-L62` |
| 주입은 "경계 아닌 완화" | `injection.ts#L4-L13` |
| 플랫폼 초대 후에만 채널 가시 | `docs/setup.md#L117-L118` |
| `targetChannels` `'*'`·빈 목록이면 기동 거부 | `docs/setup.md#L395-L400`, `index.ts#L528` |
| allowlist 밖 무동작 | `index.ts#L3477` |
| Slack `/invite` 안내 | `service/gateway.ts#L1508` |
| Discord OAuth 초대 링크 | `service/gateway.ts#L1548-L1552` |
| QA "the bot is not in #x — invite it first" | `tools/qa-slack.ts#L77` |
| 초대된 봇+`'*'`+`replyMode: all` 경고 | `docs/limitations.md#L81` |
| 승인 기본 unattended(fail-closed)·채팅 ask 두 조건·10분/15분 타임아웃 | `docs/how-it-works.md#L328-L346` |

### B.2 deepseek-ai/dsh-superagent-prototype — `8c32a9dc5d72c033816c6a8110b37f40cf94fba4`

| 주장 | URL(경로 + 라인) |
| --- | --- |
| admission table 3셀·강도 순서 | `src/policy.ts#L135-L141` |
| gate: 봇은 inferred까지만·explicit 셀 우선 | `src/policy.ts#L167-L211` |
| 재검증(revalidate) | `src/policy.ts#L213-L239` |
| 정책 버전 해시 = rules+mode+sources | `src/policy.ts#L111-L118` |
| admission table·강도·봇 규칙 | `docs/002-authorization.md#L46-L81` |
| 수용모드 off/observe/auto | `docs/002-authorization.md#L91-L101` |
| 정책 변경은 회수 아닌 통지·재검증 | `docs/002-authorization.md#L107-L117` |
| Mandate 불변·범위확장=새 티켓 | `docs/001-domain.md#L34` |
| Executor는 승인 없이 OS 사용자로 실행 | `docs/003-execution.md#L25-L29`, `#L78-L80` |
| 평가는 read-only·무효 승인 replay 금지 | `prompts/admission.md#L3-L4` |
| 자동 리뷰어 3라운드 상한 | `prompts/team/communication.md#L92-L94` |
| 자기작업 머지·승인 대행 금지 | `prompts/team/delivery.md#L10-L16` |
| inferred 티켓 추가 문구·프롬프트 상한 | `src/executor/dsh.ts#L107-L113`, `#L139-L145` |
| 상태-only 요청은 구현 재개 금지 | `src/core/tickets.ts#L21` |
| Mandate basis 4종(addressed/followup/admission/bot) | `docs/007-architecture.md#L125-L147` |
| gate 강도 채택·admitsInferred = auto+활성 규칙 | `src/core/evals.ts#L90-L105` |
| 정책 편집 낙관적 동시성(`policyVersion`+`policyEffects`) | `src/app/settings.ts#L1-L6` |
| 소스 컨텍스트는 accepted trigger 앵커·chat_id 불일치 거부 | `src/sources/feishu-read.ts#L35-L62` |

### B.3 milind-soni/OpenMausBot — `99b70934937f97a5e69f0253f4ade5c64c82bfd5`

| 주장 | URL(경로 + 라인) |
| --- | --- |
| 초대 링크는 편의·코드는 주소로 발송 | `src/components/PeopleSection.tsx#L1` |
| `invite()` = `PUT /api/config` `signIn:{admins,members}` | `src/components/PeopleSection.tsx#L246-L256` |
| `inviteLink()` = `/pair?email=…` | `src/components/PeopleSection.tsx#L30` |
| portal이면 readOnly | `src/components/PeopleSection.tsx#L260` |
| Settings→People 도메인 초대·역할 변경·제거 시 로그인 차단 | `docs/self-hosting.md#L664-L678` |
| 관리자 초대·member 자기 승격 403·제거 즉시 폐기·와일드카드 | `server/people-invite.test.ts#L161-L232` |
| 페어링 코드 60-bit·12자·5분 single-use·세션 30일·ticket·lockout | `server/sessions.ts#L1-L35` |
| 제거·권한 상실 시 세션 즉시 revoke | `docs/verification/people.md#L9-L12` |
| 철회 경로 B(portal): sign-in 목록 변경이 세션 재검증·철회 안 함 | `docs/team-cloud.md#L220` |
| 일괄 철회 API(`revokeMember`·`onSessionRevoked`) | `server/sessions.ts#L611-L625` |
| trust model: 원격 페어링 필수·service 트러스트 제한 | `.oss-scanner/threat_model.md#L44-L95` |
| 승인 판정은 provider 위임(자동 판정 0) | `server/auto-approve.ts#L1-L4` |
| 만료 ask는 새 카드로 부활 금지 | `server/auto-approve.ts#L11-L12` |
| Full access 상속 금지·위임 시 min 등급 | `server/auto-approve.ts#L29-L57` |

### B.4 Orkas-AI/Orkas — `07df32473a1100df0f8ae078772101d350ee6e79`

Orkas는 로컬 사본 미보존 회신이라 분석가 제공 blob URL 목록이 없다. 인용 경로는 §4.2에만 남기며, 같은 SHA의
`https://github.com/Orkas-AI/Orkas/blob/07df32473a1100df0f8ae078772101d350ee6e79/<path>` 패턴으로
유도할 수 있으나 **이 문서에서 URL을 확인·검증하지 않았다**(U).

## 부록 C. 보완 체크리스트

- [x] OpenMausBot 2차 스캔(초대·pending·철회 수명주기 + 서버 집행 코드) 완료 반영 → §3.1·§4.1·§7
- [x] handy-analyst 3차 정정(플랫폼 초대·승인 fail-closed 뉘앙스·Mandate basis·확장 스캔) 반영
- [ ] 리뷰어 독립 재스캔 수치 확인 → §3.1 OpenMausBot 최종 수치(5,650/5,659·9파일 차이) 확정
- [x] TalkAbout 비교 라인 재확인: task-15(기본 off) 반영, task-20의 정책 GET event-free read·feature-off 기존 invite scope 보존도 워크스페이스 코드에 반영(memory+격리 PG 검증). controller/API 문서의 최종 배포 문구만 별도 확인
- [ ] 4곳 코드 복사 0·상표/enterprise 미사용 재확인 후 종료
