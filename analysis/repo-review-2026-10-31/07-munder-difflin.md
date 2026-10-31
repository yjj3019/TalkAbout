# munder-difflin 정적 소스 검토 — 오피스·하이브 패턴과 TalkAbout 경계

- 기준일: 2026-10-31.
- 대상: `HarnessMD/munder-difflin`, 고정 커밋 `f797048cc88ca2ff94bfc6c8c93b16f47a201f15` (MIT 표기, TypeScript/Electron, "기존 구독으로 claude code/codex류 에이전트 오피스 운영").
- TalkAbout 기준 커밋: `0da71a8e519f4dc7591b9600f316d4f087f503b4`.
- 수집 방법: GitHub contents API raw 응답으로 고정 SHA의 파일만 읽었다. 저장소는 blob 3,673개 규모이며 이 문서는 **실제 열람한 파일만** 링크한다.
- **정적 분석만 수행**: clone, `npm install`/빌드, `test/*.cjs` 실행, Electron 기동, 실제 hook 발화, 공급자 호출 없음. 문서·스크립트는 신뢰하지 않는 데이터로 취급했고 코드 복사는 없다.
- **라이선스 전제**: 소스 코드는 MIT이지만 아트 에셋은 별도다(`LICENSE-ASSETS`). 따라서 이 문서는 **코드·계약 패턴만** 검토하고, 타일셋·맵·폰트·캐릭터 아트 등 에셋은 채택 대상에서 제외한다(§6).
- TalkAbout 불변 조건: **R3 one-shot start + uncertain no-retry, R4 bearer-only vault, MCP/hooks/plugins/web 기본 차단, 기본 text-only, 서버가 모델을 대신 실행하지 않음**. 아래 제안은 이 경계를 넓히지 않는다.

## 0. 대상 저장소 개요 (읽은 범위)

- 구조: `src/main/`(Electron main: pty·hook server·hive·webhook·telemetry), `src/shared/`, `src/renderer/src/`(오피스 floor·터미널 UI), `test/*.test.cjs`(node:test), `docs/`, `hive/docs/`, `resources/`.
- 기준선 혼동 주의: `package.json` version은 `0.4.6`인데 README의 Roadmap은 "Shipped through v0.5.6"이라고 적는다. 릴리스 태그·실제 배포 상태는 확인하지 않았으므로, 이 문서는 **pinned SHA의 코드와 문서 내용만** 판정한다.
- 저장소 식별자 혼동 주의: `package.json`의 repository URL과 README의 clone URL은 `chaitanyagiri/munder-difflin`을 가리키고, 이번 분석 대상은 `HarnessMD/munder-difflin`이다. 리네임/이전으로 보이나 확인하지 않았다.
- 이번에 읽은 코드: `src/main/hooks.ts`(부분), `control.ts`, `procKill.ts`, `hiddenClaude.ts`(부분), `pty.ts`(부분), `hive.ts`(부분), `fs.ts`(부분), `webhook.ts`(부분), `worktreeDeps.ts`, `src/shared/agentProvider.ts`(부분), 테스트 `auto-mode-sandbox`, `fs-path-containment`.
- 읽지 않은 것: Electron 렌더러 157개 파일 대부분, `test/` 100여 개 중 대부분, `blog/`, `docs/blog/` HTML, 에셋.
- 저장소 언어·런타임: TypeScript + Electron, 테스트는 `node --test test/*.test.cjs` 형식이고 `postinstall`이 pty를 Electron ABI로 리빌드한다.
- 문서군: `docs/` 아래 설계 문서(SPEC/HIVE/DESIGN/MEMORY_GRAPH), `blog/src/posts/`, `docs/hires/spec/`, `hive/docs/`가 있고, blog는 사이트 소스와 빌드 산출 HTML이 섞여 있다(이번 분석은 blog markdown 일부만 읽음).
- 고정 SHA는 이 시점의 스냅샷이며 이후 main 변경은 반영하지 않는다. 문서에도 "awaiting sign-off" 같은 시점 표기가 남아 있다.

## 요약 판정

Munder Difflin은 기존 CLI 구독을 그대로 쓰는 Electron 데스크톱 하네스로, 실제 `claude`/`codex`/`grok` 등을 `node-pty`로 띄우고 Sims 스타일 office floor와 파일 기반 "hive"(mailbox·blackboard·git)로 멀티 에이전트를 조율한다.

참고 가치가 있는 것은 제품 외형이 아니라 **경계를 좁게 만든 세 가지 구현**이다.

1. 파일 IPC의 containment guard: workspace root + 상대 경로, `realpath` + 구성요소별 symlink 검사 + `O_NOFOLLOW`로 symlink 탈출을 막는다.
2. 프로세스 트리 종료: PID 재사용을 start-time으로 대조하고, 지연 sweep에서 "원래 멤버가 남아 있을 때만" group kill을 한다.
3. hire manifest: untrusted JSON이 실행 파일·임의 flag·임의 MCP를 지정할 수 없도록 default-deny 스키마로 설계했다.

동시에 **그대로 이식하면 안 되는 기본값**이 분명하다. 자율 모드의 기본은 `--permission-mode bypassPermissions`이고, hook socket이 죽으면 "모든 hook이 허용"되는 fail-open이며, HITL을 별도 승인 큐 없이 god agent와 provider 프롬프트에 맡긴다. 문서(SPEC/HIVE/MEMORY_GRAPH)는 코드보다 앞선 설계 계약이어서 드리프트가 있고, 웹훅은 공개 터널을 연다. 따라서 채택 대상은 **관찰 실패 처리·containment·스키마 default-deny 패턴**이며, 실행 기본값·hook fail-open·에셋은 제외한다.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — "오피스"의 실행 격리는 플랫폼이 아니라 provider 플래그이고, Windows에서는 sandbox 부재도 spawn한다

이 저장소의 격리 주장은 앱이 만든 sandbox가 아니라 provider CLI의 sandbox 옵션에 의존한다. 테스트는 "auto mode가 OS sandbox를 켠 채로 유지된다"고 단언하지만, 그 증거는 **spawn 인자와 settings 파일 내용**이다.

- Codex auto mode는 `-a never -s workspace-write`를 쓴다. 과거 `--dangerously-bypass-approvals-and-sandbox`를 쓰던 이유는 hive worker가 cwd 밖 agent 폴더에 써야 했기 때문이고, 그 해법을 sandbox 제거가 아니라 `--add-dir` 경로 추가로 바꿨다.
- Claude는 per-session settings에 `sandbox.enabled=true`, `allowWrite=[agentDir, hiveRoot, palace]`를 넣고, 두 번째 layer(`permissions.additionalDirectories`)도 같은 목록으로 맞춘다(둘 중 하나만 있으면 Edit/Write는 되는데 `mv … .done/`이 거부되어 교착).
- 그러나 같은 테스트가 `failIfUnavailable`을 **true로 두지 않는다**고 단언하며 이유를 "Windows must still spawn"이라고 적는다. 즉 sandbox를 쓸 수 없는 환경에서도 세션은 뜬다.
- 구현: [auto mode가 OS sandbox를 유지하는 이유와 경로 추가 해법](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/test/auto-mode-sandbox.test.cjs#L1-L11>), [codex flag·Claude settings 단언과 Windows 예외](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/test/auto-mode-sandbox.test.cjs#L30-L56>), [autoModeFlag 정의](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/shared/agentProvider.ts#L196-L214>), [hive spawn이 `--add-dir`로 writable root를 추가](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hive.ts#L882-L887>).
- 증거 한계: 이 테스트는 `node:test` 단위 테스트이며 이번 분석에서 실행하지 않았다. 실제 provider가 그 flag를 받아들였는지, OS sandbox가 실제로 켜졌는지는 NOTRUN이다. Windows에서 sandbox 미가용 시의 동작은 코드 주석 수준에서만 확인했다.
- TalkAbout: Windows Job containment는 수명·정리 계층이며 sandbox가 아니고([processes.py](<../../talkabout/processes.py#L1-L7>)), 작업 프로필도 "sandbox 주장 아님"을 명시한다([WORK_PROFILE](<../../docs/WORK_PROFILE.md#L83-L89>)).
- 적용: provider sandbox flag·settings는 **"선언된 설정" 증거로만** 기록하고 플랫폼 격리로 승격하지 않는다. A0/A1 evidence에 "sandbox 선언됨 / 가용성 미확인 / 불가용"을 구분하는 상태가 필요하다. Windows에서 sandbox 불가용인데 실행을 계속하는 기본값은 채택하지 않는다.

### F2 — hook 경계는 좋은 제어점이지만, socket이 죽으면 fail-open이다

hook server는 각 agent의 `--settings`로 주입된 shim이 Unix socket으로 hook payload를 보내는 구조다. 여기서 운영자 제어(`pause`, `gateTool`, `steer`, `halt`)가 Claude Code의 hook 반환 프로토콜을 타고 내려가며, 도구 거부는 `permissionDecision:'deny'`로 즉시 집행된다. 사람의 느린 승인은 provider 네이티브 프롬프트에 맡긴다.

- 문제는 실패 모드다. socket bind가 실패하면 코드 주석·로그·알림이 모두 "복구될 때까지 **모든 agent hook이 ALLOWED**되고 비용도 기록되지 않는다"고 말한다. 즉 관찰 계층이 죽으면 **허용으로 저하**한다.
- 그래도 프레임은 bounded다: newline-delimited JSON 1프레임, 최대 256KiB, 초과 시 연결 파기, 소유권 ping으로 다른 인스턴스의 socket을 오인하지 않는다.
- 구현: [hook server의 역할과 shim 구조](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hooks.ts#L1-L11>), [프레임 상한 256KiB](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hooks.ts#L24-L25>), [bind 실패 시 "every agent hook is ALLOWED"](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hooks.ts#L260-L272>), [bounded frame 처리와 ping](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hooks.ts#L278-L312>), [운영자 deny/steer/halt가 hook 반환으로 집행](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/control.ts#L1-L18>).
- 증거 한계: 코드 주석과 로그 문장을 근거로 한 정적 판정이다. 실제 socket 사망 후 provider가 어떻게 진행하는지는 실행 검증하지 않았다. hook 이벤트가 avatar 애니메이션에도 쓰이므로, 관찰 실패가 UI 상태에도 영향을 준다.
- TalkAbout: 도구 이벤트는 관찰이며, parser 거절·interrupt는 부작용 없음의 증거가 아니고 prompt 후 실패는 unknown으로 남긴다([REPOSITORY_ADOPTION_PLAN A0](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L19-L32>), [07-TALKABOUT-GAPS](<../../analysis/repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L16-L23>)).
- 적용: **관찰 실패는 "미관찰"이지 "허용"이 아니다.** A0/A4에 이 규칙을 문장으로 고정하고, 합성 회귀에서 "hook/stream 실패 시 success·allowed로 승격되지 않음"을 검사한다. 운영자 deny/steer/halt 어휘는 향후 control-plane 계약 후보로만 둔다.

### F3 — HITL은 provider 프롬프트 + god agent로 대체되고, 기본값은 bypassPermissions다

이 저장소는 사람 승인 큐를 따로 두지 않는다. 사람에게 향한 메시지는 god(=orchestrator)로 라우팅되고, 도구 권한 프롬프트가 HITL gate이며 원격 승인도 가능하다고 설계 문서가 적는다. 기본 자율 모드는 `bypassPermissions`이고, 자체 블로그도 이를 "unattended control room의 올바른 기본값이지만 production repo에서는 loaded foot-gun"이라고 인정한다.

- 반면 untrusted 입력 경계는 좋은 예다. hire manifest는 실행 파일명·command 필드가 없고, `commandFlags`는 allowlist(`--model`, `--max-turns`, `--output-format`, `--verbose`)만 받으며 모든 token이 보수적 문자 집합을 만족해야 하고, argv는 shell을 거치지 않는다. skills/MCP도 번들 allowlist id만 허용하고 자동 spawn하지 않는다.
- 구현: [승인 큐 없음·native HITL·god 라우팅](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hive.ts#L13-L17>), [god-mode autonomy·native HITL 설계](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/HIVE.md#L45-L52>), [bypassPermissions 기본과 foot-gun 인정](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/blog/src/posts/agent-security-and-sandboxing.md#L37-L51>), [Claude preset의 bypassPermissions](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/shared/agentProvider.ts#L170-L195>), [hire manifest의 default-deny security model](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/docs/hires/spec/HIRE_SPEC.md#L44-L68>).
- 증거 한계: 제품 문서·주석·스펙 기준이다. 실제 승인 프롬프트가 원격에서 어떻게 승인되는지, god agent가 어떤 기준으로 "critical"을 판정하는지는 코드/실행으로 확인하지 않았다. hire manifest 검증기도 실행하지 않았다.
- TalkAbout: 기본은 text-only이고 tools는 꺼져 있으며([WORK_PROFILE](<../../docs/WORK_PROFILE.md#L8-L19>)), Approval은 human actor·fence·인자 digest를 요구한다([TOOL_BROKER_PLAN](<../../docs/TOOL_BROKER_PLAN.md#L28-L42>)).
- 적용: **hire manifest의 "untrusted descriptor는 실행 파일·flag·MCP를 지정할 수 없다" 스키마**는 A2 계획 초안/노드 지시 capability 설계에 유용한 참고다. 반대로 "god agent가 routine item을 스스로 해결하고 critical만 사람에게"는 채택하지 않는다. TalkAbout의 승인 주체는 사람 principal이어야 한다.

### F4 — 파일 공유는 git worktree + hive 단일 writer/단일 committer이고, 파일 IPC는 symlink까지 막는다

멀티 에이전트 조율은 git과 파일로 한다.

- 모든 hive 지식은 로컬 git repo의 파일이고 **오직 Electron main만 commit**한다(agent는 git을 호출하지 않는다). 파일당 단일 writer: 각 agent는 자기 `agents/<id>/` 안에만 쓰고, 교차 전달은 router가 outbox→inbox로 옮긴다. 메시지는 파일 1개 + temp→rename 원자 쓰기다.
- workspace 격리는 git worktree로 하고, worktree에는 base checkout의 `node_modules`를 symlink/junction으로 연결한다.
- 파일 IPC는 workspace root + 상대 경로만 받고, lexical check로는 부족하므로 `realpath` + 구성요소별 symlink 검사 + `O_NOFOLLOW`로 최종·중간·dangling symlink 탈출을 모두 거절한다. 정상 in-workspace symlink는 허용한다.
- 프로세스 종료는 별도 경화가 있다: POSIX는 group SIGKILL, Windows는 `taskkill /T /F`, 지연 sweep 전에 pid+start-time 스냅샷을 대조해 **재활용된 pgid를 죽이지 않는다**.
- 구현: [hive 단일 committer·단일 writer·router](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/HIVE.md#L34-L62>), [on-disk layout·원자 rename·append-only log](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/HIVE.md#L65-L91>), [파일 IPC containment 목적과 symlink 위협](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/test/fs-path-containment.test.cjs#L3-L21>), [dangling symlink로 외부 파일 생성 차단 단언](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/test/fs-path-containment.test.cjs#L107-L118>), [PID 재사용 가드와 지연 sweep 판단](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/procKill.ts#L30-L108>), [worktree에 base node_modules를 junction](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/worktreeDeps.ts#L13-L32>).
- 증거 한계: 테스트는 실행하지 않았다. worktree는 git 수준 격리이며 OS sandbox가 아니다. `node_modules` 연결은 worktree가 base 의존성을 **공유 writable**로 쓰는 구조다. hive repo는 변경마다 commit되므로, gitignore되지 않은 비밀은 영구 기록이 될 수 있다고 코드가 스스로 경고한다([hive.ts](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/hive.ts#L649-L655>)).
- TalkAbout: write scope는 선언일 뿐이고([REPOSITORY_ADOPTION_PLAN A2](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L34-L48>)), workspace 검사는 시점별 검사다([WORK_PROFILE](<../../docs/WORK_PROFILE.md#L100-L102>)).
- 적용: (i) symlink 탈출을 포함한 **경로 검증 테스트 항목**을 A2 gate 체크리스트에 추가, (ii) PID start-time 대조는 기존 POSIX C1의 `start` 대조와 같은 사고방식이므로 보강 근거로만 사용, (iii) 단일 writer 원칙은 TalkAbout의 room event/task 저장에 "한 writer" 규칙으로 참고하되 파일 저장을 도입하지 않는다.

### F5 — 오피스 UX는 hire/roster/god 중심이고, TalkAbout의 room·초대·시작 조건과 대응물이 없다

에이전트 추가는 "hire"다. manifest를 fetch/검증해 **폼을 미리 채우고**, 사람이 필드를 검토한 뒤 spawn을 누른다(자동 spawn 없음). floor에는 roster 기반 아바타가 서고, god agent가 라우팅·할당을 맡는다. 반면 TalkAbout은 방 준비 → 초대(pending) → room-scoped 토큰 → 인증 worker + 연결 시험 → 사람이 시작이라는 조건부 흐름이다.

- 이 저장소에는 TalkAbout식 room membership·초대 수락·준비 worker·시작 gate가 없다. 시작은 사람 클릭/spawn이고, 이후 turn은 hook 이벤트로 관찰된다.
- 외부 입력은 웹훅으로 받는다. 이 웹훅 서버는 **공개 터널(tunnelmole)** 뒤에 있고, 코드 주석도 "PUBLIC surface (tunnel-forwarded)"라고 명시한다. 대신 상수시간 secret 비교, unknown id와 wrong secret의 동일 응답, 토큰별 상태 조회만, body cap·전역/엔드포인트 rate limit을 둔다. 승인 대기 모드(`awaiting-approval`)도 있다.
- 구현: [hire manifest가 폼만 채우고 human이 spawn](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/docs/hires/spec/HIRE_SPEC.md#L44-L68>), [office/roster/god 구조](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/README.md#L116-L163>), [공개 터널 보안 경계와 gate](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/webhook.ts#L1-L40>), [터널 오픈과 public URL](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/src/main/webhook.ts#L192-L221>).
- 증거 한계: UX는 문서·스펙만, 웹훅은 main 프로세스 코드 일부만 읽었다. 실제 터널 개방·외부 POST·승인 대기 동작은 실행하지 않았다. god agent의 판정 기준과 UI 표시는 확인하지 않았다.
- TalkAbout: 시작 조건은 [USER_FLOW](<../../docs/USER_FLOW.md#L10-L16>)에 있고, 관찰은 시작·취소가 아니다([REPOSITORY_ADOPTION_PLAN A4](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L77-L97>)).
- 적용: 웹훅의 **상수시간 비교·열거 방지·동일 오류 응답·rate limit** 패턴은 A5 ingress 설계 참고로 유용하다. 그러나 공개 터널 자체는 TalkAbout의 기본 no-network 원칙과 충돌하므로 도입 금지다. "아바타가 움직인다"는 관찰은 승인·성공 증거가 아니다.

### F6 — 문서 주도 설계(SPEC/HIVE/MEMORY_GRAPH)는 코드보다 앞선 계약이고 드리프트가 있다

문서들은 스스로 위치를 밝힌다. HIVE.md는 "Code is the source of truth for what's built; this is the source of truth for what we're building toward"라고 적고, MEMORY_GRAPH_SPEC는 "awaiting god sign-off. No component code is written yet"라고 적는다.

- 그러나 pinned SHA의 트리에는 메모리 그래프 컴포넌트(`src/renderer/src/components/MemoryGraphPanel.tsx`, `components/memoryGraph/buildGraph.ts` 등)가 이미 있다. 스펙 문서의 상태 문장이 코드 상태와 어긋난다.
- SPEC.md는 "Electron, macOS-first", "tmux pane마다 avatar", "agent-to-agent 버스 아님(v2)"을 전제하지만, 현재 README는 `node-pty`로 12개 엔진을 띄우고 hive로 조율한다고 적는다. 초기 설계 문서를 그대로 현재 구현 설명으로 읽으면 틀린다.
- 이 드리프트는 문서 품질 문제가 아니라 **문서를 구현 증거로 쓰면 안 된다**는 사례다. TalkAbout도 분석 산출물을 제품 구현·테스트 영수증으로 쓰지 않는다고 이미 못박고 있다.
- 구현: [SPEC의 tmux·macOS-first·비버스 전제](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/SPEC.md#L9-L20>), [SPEC 두 데이터 평면과 tmux pipe/send-keys](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/SPEC.md#L24-L68>), [HIVE.md의 "무엇을 지향하는가" 위치](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/HIVE.md#L7-L10>), [MEMORY_GRAPH_SPEC의 awaiting sign-off·코드 없음](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/MEMORY_GRAPH_SPEC.md#L1-L14>), [현재 구현 설명(node-pty·hive)](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/README.md#L116-L163>).
- 증거 한계: 메모리 그래프 컴포넌트는 트리 목록으로 존재만 확인했고 내용은 읽지 않았다. "스펙보다 먼저 구현됐다"는 정황이지 릴리스 이력 검증은 아니다.
- TalkAbout: [REPOSITORY_ADOPTION_PLAN 단계 종료 방식](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L99-L102>)은 분석 산출물을 승인된 설계 근거로만 보존하고 구현·테스트 영수증으로 쓰지 않는다고 적는다.
- 적용: SPEC/HIVE/MEMORY_GRAPH를 TalkAbout 아키텍처로 이식하지 않는다. 대신 **"설계 문서 / 구현된 코드 / 실행 검증" 상태를 분리해 표기하는 문장 습관**만 참고한다.

## 2. 주장·문서 대 실제 구현

| 주장/문서 | 소스 기반 판정 |
|---|---|
| "run an office of claude code/codex agents on your laptop, sandboxes or anywhere" | 실제 CLI를 `node-pty`로 띄우는 구조는 확인했다. 그러나 "sandbox"는 앱이 만든 격리가 아니라 provider flag/settings 수준이고, Windows에서는 sandbox 미가용도 spawn한다. |
| "auto mode keeps the OS sandbox ON" | 단위 테스트가 spawn 인자·settings 내용을 단언한다. 실제 sandbox 가용성·동작은 실행 검증되지 않았고, 테스트 자체가 Windows 예외를 둔다. |
| "agents collaborate through the hive" | 단일 writer mailbox + router + 단일 committer git 설계가 문서·코드에 있다. 모델이 메시지를 읽고 이해했다는 증거는 아니다. |
| "memory … recall in milliseconds" | 주장이며, 이번 분석은 메모리 구현·벤치를 읽거나 실행하지 않았다(NOTRUN). |
| "anonymous telemetry" | `TELEMETRY.md`가 event·property allowlist와 opt-out 3가지를 명시하고 PostHog를 쓴다. 구현 코드는 열람했으나 실행 검증은 하지 않았다. |
| "source code is MIT" | 맞다. 단 아트 에셋은 별도 라이선스이고 크레딧이 필수다(§6). |

## 3. TalkAbout 적용 후보

아래는 새 설계·검증 항목의 **후보**이며, 구현되었다는 주장이 아니다. A단계 표기는 [REPOSITORY_ADOPTION_PLAN](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17>)을 따른다.

| 우선순위 | 채택 패턴 | TalkAbout 적용 지점 | 단계/gate | 비용 | 위험 |
|---|---|---|---|---|---|
| P0 | "관찰 실패 ≠ 허용" 규칙(반면교사) | A0/A4 관찰 계약, [WORK_PROFILE P0](<../../docs/WORK_PROFILE.md#L45-L68>) | 합성 회귀 gate: stream/hook 실패 시 success·allowed로 승격 금지, unknown 유지 | 1일 | 기존 unknown 의미 중복 정의 |
| P0 | 경로 containment 테스트 항목(final/intermediate/dangling symlink, O_NOFOLLOW) | A2 write scope 검증 체크리스트 | A2 gate: symlink 탈출 거절·정상 in-workspace 링크 허용 단언 | 1~2일 | A2는 초안 단계라 실행 연결과 분리 필요 |
| P1 | PID start-time 대조 + 지연 sweep 가드 | [processes.py](<../../talkabout/processes.py#L167-L233>) 보강 근거(기존 POSIX C1과 동일 사고) | 기존 회귀 gate 유지: 회수 증명 없는 "종료됨" 금지 | 1일 | 중복 구현 위험 |
| P1 | untrusted descriptor default-deny 스키마 | A2 계획 초안/노드 지시 capability 설계 참고 | schema gate: 실행 파일·임의 flag·MCP 지정 필드 부재 | 2일 | 초안 단계에서 과설계 |
| P2 | telemetry "무엇을 보내지 않는가" 문서 스타일 | TalkAbout 감사·관찰 문서 서술 형식 | 문서 gate: 자유 텍스트·경로·프롬프트 미전송 목록 명시 | 0.5일 | 문서만으로 구현 증명 아님 |
| P2 | 공개 ingress의 상수시간·열거 방지·rate limit | A5 ingress 설계 참고(터널 제외) | 보안 gate: 동일 오류 응답·cap·rate limit 테스트 | 2~3일 | 기본 no-network와 혼동 |

도입하지 않을 것/보류:

1. **즉시 금지:** bypassPermissions/auto 기본값, hook fail-open, god agent 자동 adjudication.
2. **보류:** 공개 터널·웹훅 ingress. A5 gate와 별도 승인 전에는 열지 않는다.
3. **즉시 채택(문서·테스트 항목만):** containment 테스트 항목, 관찰 실패 규칙, default-deny 스키마.

## 4. 그대로 가져오면 안 되는 것

1. **`--permission-mode bypassPermissions` / auto mode 기본값.** TalkAbout 기본은 text-only이고 MCP/hooks/plugins/web가 꺼져 있다. 이 기본값은 불변식과 정면 충돌한다.
2. **hook socket 실패 시 fail-open.** "복구될 때까지 모든 hook 허용"은 관찰 실패를 허용으로 바꾸는 정반대 설계다.
3. **god agent가 routine item을 스스로 해결하고 critical만 사람에게 보내는 HITL.** 별도 승인 큐가 없다는 설계는 TalkAbout의 human principal Approval과 양립하지 않는다.
4. **hive git repo를 감사 원장으로 쓰는 것.** 변경마다 commit되고, gitignore되지 않은 비밀은 영구 기록이 된다. TalkAbout은 durable store·event ACL·redaction 규칙을 유지한다.
5. **공개 터널 웹훅.** 기본 no-network·MCP/web 차단과 충돌한다. ingress가 필요하면 A5 gate 안에서 별도 설계한다.
6. **worktree에 `node_modules`를 junction으로 공유하는 것.** 쓰기 가능한 의존성 공유는 공급망·오염 경로가 된다.
7. **SPEC/HIVE/MEMORY_GRAPH를 TalkAbout 아키텍처로 이식하는 것.** 문서 전제(tmux/pty, 서버가 모든 CLI를 spawn, god 중심)가 TalkAbout의 room/ACL·"서버가 실행하지 않음"과 다르다.
8. **에셋 포함.** 타일셋·맵(LimeZu)·폰트·캐릭터 아트는 MIT가 아니며 크레딧 조건이 붙는다. 코드 패턴만 검토 대상이다.
9. **"문서가 그렇게 말한다"를 구현 증거로 쓰는 것.** SPEC/HIVE/MEMORY_GRAPH는 지향 또는 제안 단계 문서이고, pinned SHA의 코드와 어긋나는 부분이 확인된다.

## 5. 검증 경계 (NOTRUN)

- `npm install`/빌드, `node --test test/*.test.cjs`, `electron` 기동, 실제 hook 발화, 실제 CLI spawn, 메모리 그래프 렌더, 웹훅 터널 개방을 **수행하지 않았다**.
- 인용한 테스트(`auto-mode-sandbox`, `fs-path-containment`)는 **소스에 존재하는 단언**의 증거이지 통과 결과가 아니다.
- Windows에서 provider sandbox 미가용 시 동작, macOS/Windows별 터널·hook 소켓 동작, 실제 provider가 bypass/sandbox flag를 수용하는지는 NOTRUN이다.
- 메모리 계층·의미 검색·텔레메트리 전송·업데이터는 코드/실행 검증하지 않았다.
- `auto-mode-sandbox` 테스트가 단언하는 것은 주입된 인자·settings 파일이지, 실제 커널 sandbox 적용 결과가 아니다. 이 구분을 유지한다.
- `README` Roadmap(v0.5.6)과 `package.json`(0.4.6)의 기준선 차이는 릴리스 태그·배포물 확인 없이 해소하지 않았다.
- 이 문서는 법률 자문이 아니다. 라이선스 판정은 읽은 파일 기준이며, 에셋·폰트·번들 의존성 전체의 실사를 완료했다는 의미가 아니다.

## 6. 라이선스·의존성

- 루트 [LICENSE](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/LICENSE#L1-L21>)는 MIT(저작권자 Chaitanya Giri)이며, 고지·허가문 유지 조건이 있다.
- **[LICENSE-ASSETS](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/LICENSE-ASSETS#L1-L14>)가 MIT 적용 범위를 명시적으로 제한한다.** `src/renderer/src/assets/`의 타일셋·맵은 LimeZu의 "Modern Interiors - RPG Tileset [16X16]"이며 **LimeZu Complete Version 라이선스**로 별도 취급되고, **LimeZu 크레딧이 필수**다. Office 캐스트 스프라이트는 절차 생성이라 MIT라고 적는다.
- [README 라이선스 절](<https://github.com/HarnessMD/munder-difflin/blob/f797048cc88ca2ff94bfc6c8c93b16f47a201f15/README.md#L550-L563>)도 "MIT는 코드만, 아트는 별도이며 크레딧 필수"를 재확인하고, "The Office" 패러디이며 해당 방송사와 무관하다고 적는다.
- 폰트: `src/renderer/src/assets/fonts/LICENSE.txt`가 트리에 있으나 읽지 않았다. 폰트는 이번 검토·채택 대상에서 제외한다.
- 기준선 메타데이터 불일치: `package.json`의 repository URL은 `chaitanyagiri/munder-difflin`이고 이번 대상은 `HarnessMD/munder-difflin`이다. 리네임/이전으로 보이나 확인하지 않았다.
- 의존성: Electron 32, `node-pty`, `pixi.js`, `better-sqlite3`, `posthog-node`, `electron-updater`, `localtunnel`/`tunnelmole`(웹훅 터널), Monaco/CodeMirror 등. `postinstall`이 `electron-rebuild`·pty 권한 패치를 수행한다. 이번 분석은 설치·실행하지 않았다.
- 코드 패턴만 참고했고 코드·에셋 복사는 없다.

## 7. 교차 확인한 TalkAbout 근거

- [REPOSITORY_ADOPTION_PLAN](<../../docs/REPOSITORY_ADOPTION_PLAN.md#L9-L17>): A0~A6 단계·gate, A2는 비실행 초안과 실행 DAG 분리.
- [07-TALKABOUT-GAPS](<../../analysis/repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L31-L38>): 이벤트 schema gate와 "runtime event는 서버 증명이 아니다".
- [WORK_PROFILE](<../../docs/WORK_PROFILE.md#L45-L68>): P0 관찰 계약, 실패·nonzero 종료는 turn 성공/테스트 통과 증거가 아님.
- [TOOL_BROKER_PLAN](<../../docs/TOOL_BROKER_PLAN.md#L73-L79>): unknown 외부 쓰기는 자동 재시도 금지, 감사에 actor·fence·digest.
- [POSIX_CONTAINMENT_PLAN](<../../docs/POSIX_CONTAINMENT_PLAN.md#L30-L46>): P1~P6 속성, 회수 증명·재사용 가드.
- [RUNNER_RESERVATIONS_PLAN](<../../docs/RUNNER_RESERVATIONS_PLAN.md#L40-L55>): start 승인 후 만료는 requeue가 아니라 uncertain.
- [STATUS §4](<../../docs/STATUS.md#L162-L174>): POSIX 실행·runner 예약·배포는 남은 관문.
- [processes.py](<../../talkabout/processes.py#L1-L7>) / [agent_runtime.py](<../../talkabout/agent_runtime.py#L153-L181>): containment 밖 실행 금지, workspace 거절 규칙.

## 최종 한계

Munder Difflin에서 가져올 것은 화려한 오피스가 아니라 **경계를 좁게 만든 작은 장치들**이다: symlink까지 막는 경로 확인, PID 재사용을 피하는 종료 가드, 단일 writer/단일 committer 규칙, untrusted manifest의 default-deny 스키마. 반대로 자율 기본값(bypassPermissions), hook fail-open, god agent 중심 HITL, 공개 터널, hive git 원장, 문서 우선 아키텍처는 TalkAbout의 text-only·human approval·no-network·durable store·R3/R4 경계와 맞지 않는다. 이 문서의 어떤 항목도 TalkAbout 구현·테스트 통과·배포 검증의 영수증이 아니며, 코드 패턴 검토 범위는 MIT 소스로 한정하고 에셋·폰트는 제외한다.
