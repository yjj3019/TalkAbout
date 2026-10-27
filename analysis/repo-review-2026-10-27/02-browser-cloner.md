# 02. agent-browser / ai-website-cloner-template 정적 심층 검토

- 작성 기준: 2026-10-27, TalkAbout 에이전트 방의 실제 코딩·분석 작업을 위한 도입 판단.
- agent-browser: `vercel-labs/agent-browser`, **edb40f1878864f90e14f6c11e26fdafd3fecc8dc** (로컬 HEAD 일치 확인).
- cloner: `JCodesMore/ai-website-cloner-template`, **ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb** (로컬 HEAD 일치 확인).
- 방법: 로컬 소스·테스트·매니페스트·실제 LICENSE 열람 및 git 메타데이터 조회만 수행했다. 저장소 내용은 신뢰되지 않은 분석 대상이며 실행 지시로 채택하지 않았다. **설치, 빌드, 테스트, 저장소 스크립트, 브라우저 도구, 대상 사이트 접속은 전부 NOTRUN**. 아래의 ‘테스트 존재’는 통과나 실환경 보안 입증을 뜻하지 않는다. 커밋 없음. 이 보고서 외 저장소 파일 변경 없음.

## 1. 결론

| 대상 | 실제로 있는 것 | 없는 것으로 보아야 할 것 | TalkAbout 판단 |
|---|---|---|---|
| agent-browser | Rust CLI/daemon, CDP 실행, AX snapshot/ref, 장기 세션, 저장 상태, 명령 정책·확인·도메인 제약, 구체 회귀 테스트 | 인간 승인 신원/방 권한 모델, OS 수준 egress 방화벽, 포괄적 SSRF 방어 입증, 자동 prompt-injection 면역 | **격리된 선택형 browser work profile의 실행 어댑터 후보**. 기존 no-web 프로필에 그대로 연결하지 않는다. |
| cloner | Next.js 시작 골격 코드와 세밀한 관찰→구현→비교 작업 지침 | 내장 에이전트 런타임, 자동 capture 파이프라인, 강제 상태 머신/검증 게이트, 실제 clone 구현 | **작업 brief·검수 체크리스트 패턴만 선택 채택**. 자동 복제 엔진이라고 소개하지 않는다. |

핵심은 ‘브라우저를 붙이면 코딩 가능’이 아니라 **코드 수정 권한, 웹 관찰 권한, 외부 부작용 권한을 분리**하는 것이다. 분석 방에는 provenance가 있는 캡처 산출물을 공급하고, 코딩 방에는 승인된 작업 트리만 제공한다. 웹 페이지의 지시가 방 권한이나 파일 권한을 확장할 수 없어야 한다.

## 2. agent-browser: 프로토콜과 refs

### 2.1 실제 소스 증거

모든 링크는 검토 SHA에 고정되어 있다.

1. **ref 자료구조와 문서 수명**: [element.rs L8–31](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/element.rs#L8-L31)에 backend node, role/name, selector, frame 및 문서별 session/loader가 있다. [L145–181](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/element.rs#L145-L181)에서 새 snapshot의 현재 map은 비우되 문서 동일성 bucket은 유지하고, loader가 없거나 session/loader가 바뀌면 연속성을 끊는다.
2. **잘못된 대상 대체 방지**: [element.rs L76–91](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/element.rs#L76-L91)의 exact backend node ref는 role/name fallback을 허용하지 않는다. 반대로 일반 ref에는 fallback 가능성이 있으므로 외부 효과가 있는 클릭에서 ‘ref가 있으니 동일 버튼’이라고 가정하면 안 된다.
3. **실제 회귀 테스트**: [snapshot.rs L71–104](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/snapshot.rs#L71-L104)는 동일 iframe 문서 ref 유지, 문서 교체·CDP session 교체·불명 문서에서 ref 갱신을 검사한다. 모의 WebSocket 기반 테스트이며 이번 검토에서는 NOTRUN.
4. **명령 wire path**: [daemon.rs L550–594](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/daemon.rs#L550-L594)는 줄 단위 입력을 JSON으로 파싱해 `execute_command`로 전달한다. [e2e_tests.rs L36–53](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/e2e_tests.rs#L36-L53)에는 `id`, `action`, `selector`, `values`/`script`의 구체 명령 형태가 있다. 텍스트 출력 파싱보다는 좁힌 JSON 스키마 어댑터가 적합하다.

**TalkAbout 적용**: snapshot 응답에 browser-session ID, tab/frame, 문서 generation, 관찰 시각, 최종 URL, viewport/state, ref map을 함께 묶는다. 후속 action은 동일 room/task/fence와 문서 generation에 귀속하고, navigation/DOM 교체 후 재관찰한다. ref는 관찰 편의 핸들이지 승인 capability가 아니다. AX tree만으로 배치·색·스크롤·실제 화면 일치가 증명되지는 않으므로 필요한 경우 별도 screenshot을 기록한다.

### 2.2 지속 세션과 상태는 서로 다르다

- daemon은 세션별 PID/version/socket·port/stream 파일을 유지한다. Unix socket과 Windows TCP가 구분된다: [daemon.rs L67–99](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/daemon.rs#L67-L99). Windows listener는 [L355–365](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/daemon.rs#L355-L365)에서 loopback에 bind한다. loopback은 네트워크 노출 축소이지 동일 호스트 사용자·프로세스 격리의 증명은 아니다.
- 기본 idle timeout은 1시간이나 headed/사용자 attach 브라우저는 기본 정책에서 예외가 있고 명시적 0은 무기한이다: [daemon.rs L128–180](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/daemon.rs#L128-L180). room 취소/권한 회수 시 독립적인 kill/cleanup과 임대 TTL이 필요하다.
- 저장 상태에는 cookies와 origin별 localStorage/sessionStorage가 들어간다: [state.rs L18–38](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/state.rs#L18-L38). 이는 모델 대화 기억이 아니라 인증·사이트 상태이며 방 간 공유 금지 대상이다.
- [state.rs L312–335](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/state.rs#L312-L335)는 환경변수 키가 있을 때만 암호화하고 없으면 평문 JSON을 쓴다. ‘상태 저장 지원’을 ‘기본 암호화 비밀 저장소’로 오인하지 말아야 한다.
- [chrome.rs L532–553](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/cdp/chrome.rs#L532-L553)는 지정 profile 재사용 또는 임시 UUID profile 생성, 인증서 오류 무시 및 파일 접근 옵션을 구현한다. 사용자 Chrome 프로필을 방에 연결하면 기존 쿠키·SSO·서비스워커·열린 페이지가 합쳐지는 위험이 있다.

권고 초기형은 **익명 임시 profile + task 단위 수명**, 상태 저장/복원/사용자 CDP attach 금지다. 로그인 작업은 별도 후속 설계로 분리한다. 세션 이름만 room별로 바꾸는 것은 credential isolation이나 인가 경계가 아니다.

## 3. agent-browser: 정책의 실효성과 한계

### 3.1 action policy와 승인

- [policy.rs L76–145](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/policy.rs#L76-L145)는 정책 로드 실패 시 close 외 deny, deny→confirm→allow 우선순위 및 reload를 구현한다. 다만 정책이 아예 없으면 이 보호가 없고 빈 allow/누락 allow는 허용 기본값이다. 이를 의도한 테스트도 [L191–225](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/policy.rs#L191-L225)에 있다. 반드시 명시적 `default: deny`를 사용해야 한다.
- [actions.rs L3000–3067](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/actions.rs#L3000-L3067)는 매 명령 정책 재로딩, compound action 평가, pending command 저장 및 `confirmation_required` 응답을 실제 구현한다. 문서상의 구호만은 아니다.
- 그러나 [actions.rs L13351–13377](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/actions.rs#L13351-L13377)의 `handle_confirm(_cmd, ...)`는 전달된 confirmation ID나 인간 신원을 확인하지 않고 현재 pending 하나를 꺼내 재실행한다. **이 인터페이스는 인간 승인 시스템이 아니라 daemon 명령 확인 프로토콜**이다. 명령 호출자가 confirm에도 접근하면 스스로 확인할 수 있다. 여러 에이전트가 한 daemon을 공유하면 pending 교체/잘못된 승인 연계도 외부에서 방지해야 한다.

TalkAbout의 신뢰된 dispatcher만 confirm을 호출하도록 숨겨도 충분하지 않다. 승인 대상을 canonical arguments hash, URL/origin, ref/document, 방·작업·정책 revision, 만료 시각에 고정하고 승인 직전/실행 직전 권한을 다시 검사해야 한다. 클릭은 읽기처럼 보여도 구매·전송·삭제가 될 수 있어 command 이름만으로 부작용 분류가 되지 않는다. 무응답은 승인이 아니다.

### 3.2 domain/network policy: 상당한 방어가 있으나 보안 경계 전체는 아님

- [network.rs L79–125](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/network.rs#L79-L125)는 비어 있으면 모두 허용, 정확 hostname 또는 `*.suffix`(apex도 포함) 매칭을 한다. scheme/port/path/IP 대역 정책이나 DNS 해석 후 주소 검사는 이 함수에 없다. 허용 hostname이 사설 주소로 해석되거나 허용 호스트의 다른 port를 향하는 문제를 별도 경계가 해결해야 한다.
- 단순 navigation 검사만 있는 것은 아니다. [network.rs L161–189](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/network.rs#L161-L189)는 new-document 및 현재 runtime에 filter script를 설치한다. [actions.rs L12197–12234](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/actions.rs#L12197-L12234)는 Fetch interception에서 domain 검사를 route/header보다 먼저 수행한다. 비 HTTP/WS 계열은 document와 subresource를 다르게 취급하므로 모든 스킴이 일괄 차단된다고 말할 수 없다.
- **중요한 fail-closed 비호환**: [actions.rs L3762–3855](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/actions.rs#L3762-L3855)는 allowed-domains와 restore/state, CDP/auto-connect, persistent profile, iOS/Safari, direct-page provider 등을 거부한다. 이유는 초기 스크립트 실행·저장 origin replay·WebRTC·worker/popup containment 설치 시점이다. 호환이 안 된다고 allowlist를 꺼서 해결하면 안 된다.
- `read` HTTP 경로도 존재하므로 브라우저만 통제해서는 부족하다. [read.rs L198–215](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/read.rs#L198-L215)는 요청 전과 redirect별 허용 도메인을 재검사한다. 이것은 긍정적이나 hostname 검사와 IP/egress 제약은 다른 문제다.
- [e2e_tests.rs L4452–4485](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/e2e_tests.rs#L4452-L4485)에는 service-worker 외부 요청 누출 시도가 있다. [L4588–4610](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/e2e_tests.rs#L4588-L4610)의 worker WebSocket, [L4371–4390](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/e2e_tests.rs#L4371-L4390)의 popup 초기 요청도 별도 시나리오다. 하지만 E2E는 기본 ignore이고 실제 Chrome을 요구한다([L1–8](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/e2e_tests.rs#L1-L8)). **이번 검토 NOTRUN; 우회 성공도 완전 차단도 실증하지 않았다.**

### 3.3 파일·코드·prompt injection 위험

`allow_file_access=false`는 Chrome flag 기본값일 뿐 OS 파일 sandbox가 아니다. screenshot/download/state 출력, upload 입력, raw JS evaluation, plugin/provider, shell에서의 직접 CLI 접근은 각각 별도 capability다. 초기 프로필에서는 모델에 raw `evaluate`, CDP, 확장, 플러그인, arbitrary Chrome args, 인증서 오류 무시, 파일 URL, 업로드, 쿠키 조회·설정, state export, 임의 출력 경로를 노출하지 않는다. 필요한 DOM 측정은 호스트 소유의 고정 함수와 제한 필드만 제공한다.

페이지 본문·접근성 이름·이미지 OCR·다운로드 문서에는 ‘기존 지시 무시/쿠키 출력/로컬 파일 업로드’ 같은 공격이 들어올 수 있다. snapshot refs는 이 명령을 무해화하지 않는다. 원문은 `untrusted_observation`으로 provenance와 함께 저장하고, 정책·도구 설명·system prompt와 분리한다. 외부 페이지를 방의 instructions/skills로 자동 승격하지 않는다. 동일 origin XSS나 악성 허용 사이트는 domain allowlist 안에서도 공격자다.

## 4. cloner: 실제 workflow인가, 실행 엔진인가

### 4.1 정확한 구현 범위와 고정 소스 근거

1. [SKILL.md L8–23](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.agents/skills/clone-website/SKILL.md#L8-L23)는 실제 프로젝트를 읽고 source→local route/file ownership을 매핑하라고 지시한다. 기존 authored route 보존, origin별 분리, scoped styles 등 **작업 지침의 품질은 높다**. 그러나 URL을 받아 이를 자동 수행하는 서버/runner 코드는 아니다.
2. [SKILL.md L25–37](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.agents/skills/clone-website/SKILL.md#L25-L37)는 desktop/mobile 전체 관찰, 실제 입력별 상태, viewport/scroll/state가 표시된 screenshot, page brief, 실제 asset URL 매핑을 요구한다. context 설계·capture 체크리스트이지 저장 스키마나 자동 collector는 아니다.
3. [SKILL.md L39–61](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.agents/skills/clone-website/SKILL.md#L39-L61)는 shared component 선행, 독립 페이지/섹션 위임, source/local 동조건 비교, 실제 상호작용 및 production build와 rendered comparison을 완료 조건으로 삼는다. 이 조건을 강제하는 실행 가능한 gate나 similarity metric은 검토 트리에서 확인되지 않았다.
4. [inspection-guide.md L13–41](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.agents/skills/clone-website/references/inspection-guide.md#L13-L41)는 currentSrc/srcset/fonts/SVG/video, content type·dimensions, trigger→result, computed style 등 구체 관찰 항목과 handoff 문맥을 제공한다. DOM dump만 읽거나 한 장 screenshot으로 ‘pixel-perfect’ 선언하는 방식을 피하는 데 유용하다.
5. [Claude bridge L1–6](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.claude/commands/clone-website.md#L1-L6)는 canonical skill을 읽으라는 얇은 호출문뿐이다. 자체 에이전트 실행·세션 영속화·브라우저 연결·권한 제어는 제공하지 않는다.
6. [page.tsx L1–9](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/src/app/page.tsx#L1-L9)는 ‘Clone target not yet built’인 시작 화면이다. [package.json L29–58](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/package.json#L29-L58)에는 dev/build/lint/typecheck/check만 있고 browser-agent/test runner 의존성이 없다. [CI L26–36](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/.github/workflows/ci.yml#L26-L36)도 install/lint/typecheck/build만 실행하도록 정의되어 있다. **실제 웹 복제 품질·시각회귀 테스트 성공 근거가 아니다.**

즉 ‘prompt-only’라 하여 아무것도 없다는 뜻은 아니다. Next.js 앱·공통 UI 기반은 실제 코드다. 다만 **에이전트 workflow의 orchestration/capture/verification 부분은 다른 코딩 에이전트와 그 도구가 수행하도록 적힌 명세**다. 임의 사이트에서의 자동 완료, 재현 가능한 품질 점수, 토큰 절약, 테스트 통과를 이 저장소만으로 주장할 수 없다.

### 4.2 TalkAbout에 가져올 context 패턴

- 방 작업을 관찰자→brief 작성자→구현자→독립 검증자로 분리하되 고정 에이전트 수를 강요하지 않는다. 공통 파일 소유자를 먼저 정하고 독립 route/section에만 병렬 쓰기 권한을 준다.
- brief에는 source URL/최종 URL, route map, viewport와 scroll·interaction state, screenshot hash, fonts/assets 매핑, 측정 geometry, 미확인 항목을 넣는다. 원문 페이지 텍스트는 신뢰도 태그를 유지한다.
- 완료는 ‘build 성공’과 ‘동일 조건의 렌더·상호작용 비교’를 각각 기록한다. NOTRUN/관찰 불가/차이 잔존을 허용하는 정직한 상태 모델이 필요하다. agent의 자기 보고만으로 passed를 쓰지 않는다.
- source 사이트와 로컬 preview는 다른 네트워크 경계다. 외부 사이트용 브라우저에 TalkAbout 관리자 세션/내부망을 열지 않는다. preview는 승인된 컨테이너의 정확한 host:port만 노출하고 외부 캡처 브라우저에서 임의 localhost 탐색을 허용하지 않는다.
- 실제 텍스트·폰트·SVG·영상 다운로드 지침을 그대로 자동화하면 저작권·상표·사이트 약관·개인정보 수집 문제가 생긴다. 사용자 권리/범위 확인과 asset provenance/attribution 및 민감정보 제외가 먼저다. MIT 템플릿 라이선스는 대상 웹 자산의 복제 허락이 아니다.

## 5. 실제 라이선스 및 의존성 주의

| 대상 | 직접 확인 | 도입 시 추가 확인 |
|---|---|---|
| agent-browser | [LICENSE L1–10](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/LICENSE#L1-L10)는 Apache-2.0 실제 전문. [Cargo.toml L1–7](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/Cargo.toml#L1-L7)와 일치. 포함된 [axe-core LICENSE L1–12](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/src/native/a11y/LICENSE-axe-core.txt#L1-L12)는 **MPL-2.0**이므로 전부 Apache라고 통칭하면 안 된다. | 재배포 시 Apache 고지/변경 표시·NOTICE 해당 여부, MPL 적용 파일의 고지·소스 제공 의무 및 third-party notices 검토. Chrome/엔진/클라우드 provider 약관은 별도. |
| cloner | [LICENSE L1–21](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/LICENSE#L1-L21)는 2025 JCodesMore MIT 전문, 저작권·허락 고지 보존 필요. | Next/React/Base UI/shadcn 등 전이 의존성, 추출 자산별 라이선스·font embedding·상표·이미지 초상권 별도 검토. |

agent-browser는 [package.json L5–9, L22–34](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/package.json#L5-L34)의 Node 24+/pnpm 11, native build 및 **postinstall**을 포함한다. [Cargo.toml L13–47](https://github.com/vercel-labs/agent-browser/blob/edb40f1878864f90e14f6c11e26fdafd3fecc8dc/cli/Cargo.toml#L13-L47)의 Tokio/CDP WebSocket/reqwest/rustls/AES/image/ZIP 등은 브라우저 외 네트워크·암호·파일 공격면도 형성한다. 공급망 승인 후 artifact digest와 lockfile을 고정하고 SBOM/취약점·서명·업데이트 절차를 별도 수행해야 한다. 이번 검토에서 다운로드·취약점 스캔·무결성 검증은 NOTRUN.

cloner는 [package.json L26–57](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/package.json#L26-L57)의 Node 24+, Next 16.3.5/React 19.2.4 및 caret 의존성이 있다. lockfile이 있어도 재해석 설치나 수동 upgrade는 별도 결과다. [layout.tsx L1–13](https://github.com/JCodesMore/ai-website-cloner-template/blob/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb/src/app/layout.tsx#L1-L13)의 `next/font/google`은 빌드 환경의 폰트 획득/네트워크 요구도 검토해야 한다. 의존성 버전이 적혀 있다는 이유로 현재 취약점 부재나 오프라인 빌드 가능성을 주장하지 않는다.

## 6. TalkAbout 구체 통합 경로: 기본 no-web은 그대로

현재 [agent_runtime.py:665–700](../../talkabout/agent_runtime.py#L665-L700)는 work profile에서도 web search/플러그인/skills discovery 등을 끄고, 기본 경로는 더 엄격히 도구를 끈다. [tool_broker.py:93–127](../../talkabout/tool_broker.py#L93-L127)는 실제 어댑터를 금지하고 FakeToolAdapter 및 `fake_tools` profile만 허용한다. 따라서 ‘이미 안전한 브라우저 broker가 있다’고 볼 수 없으며, 이 제약을 삭제하여 실제 브라우저를 꽂는 것은 권고하지 않는다.

### P0 — 설계·승인 선행 (현재 기본값 변화 없음)

1. **별도 `browser_observe` capability/profile**을 설계한다. 기본 `text_only`/기존 coding work profile에는 추가하지 않는다. 방 관리자 승인, task scope, 시작/만료, exact origin+port, 데이터 등급, 횟수·바이트·시간 예산을 trusted host 설정으로 고정한다.
2. [tool_broker.py:138–165](../../talkabout/tool_broker.py#L138-L165)의 canonical arguments/intent digest/무응답 deny와 [204–255](../../talkabout/tool_broker.py#L204-L255)의 인간 인가·승인 재검사 패턴은 재사용 후보다. 단 현재 fake-only 안전 가드를 유지한 채 별도 검토된 실제 dispatcher를 만들고, 모델에는 approve/confirm/policy edit를 노출하지 않는다. 네트워크 접근 승인도 초기 프로필 입장 시 명시적으로 받아야 한다.
3. 브라우저는 별도 사용자·컨테이너/VM, disposable profile, 최소 mount와 비밀 없는 env로 실행한다. 네트워크는 proxy/방화벽에서 public destination만 허용하고 DNS 결과/IPv4·IPv6/redirect마다 loopback, RFC1918, link-local, metadata, 내부 DNS·사설망을 막는다. domain allowlist를 이 경계의 대체재로 삼지 않는다.
4. 모델의 shell에서 browser 바이너리·소켓·proxy 우회 경로에 접근하지 못하게 한다. daemon은 task 전용이며 모델에게 endpoint를 알려주지 않는다. 네트워크 가능한 셸을 그대로 열어 놓고 browser tool만 제한하는 설계는 무의미하다.

### P1 — 좁은 읽기 관찰 시범

- 명령은 승인 URL open, snapshot, screenshot, 고정 viewport, 제한 scroll, 안전한 DOM 측정만 시작한다. click/form/type/upload/download/cookie/state/evaluate/raw CDP/plugin/profile/attach는 초기 금지. 읽기 페이지 방문도 추적·GET 부작용을 낼 수 있으므로 완전 무부작용이라고 이름 붙이지 않는다.
- 새 구현 후보는 `talkabout/browser_policy.py`(scheme/origin/주소 검증), `talkabout/browser_adapter.py`(고정 JSON 명령), `talkabout/browser_artifacts.py`(경로·크기·민감정보·provenance), 별도 통합 테스트다. 이들은 **제안 경로이며 현재 파일 존재나 구현 완료 주장이 아니다**.
- 승인 철회/room 이탈/task 종료에 browser lease를 폐기한다. 반환 시 fence 재검사, 상태 UNKNOWN인 부작용 재시도 금지, 타 방 artifact 접근 차단을 유지한다. screenshot·DOM·URL query에도 토큰/개인정보가 있으므로 로그 마스킹·보존 기한·삭제를 적용한다.

### P2 — 실제 코딩/검증 방 연결

- cloner의 brief를 데이터 스키마로 정규화하고 캡처 artifact를 코딩 방에 명시적으로 전달한다. 구현자는 별도 workspace에서 diff를 만들며 capture 브라우저에 소스 트리 전체나 인증정보를 mount하지 않는다.
- 로컬 렌더 검증은 별도 preview profile/container에서 수행한다. 정확한 preview origin만 허용하고 TalkAbout API/DB/관리 UI·호스트 localhost를 차단한다. build는 브라우저 승인과 별개인 코드 실행 승인·sandbox 정책을 따른다.
- 이후에만 상호작용 profile을 추가한다. 제출/구매/삭제·로그인·업로드는 request digest와 화면상 대상·문서 generation을 인간에게 제시하고 1회 승인한다. 브라우저 자체 confirm은 추가 체크일 뿐 최종 인가가 아니다.

### P3 — 실행 검증 계획 (현재 전부 NOTRUN)

허용/차단 URL, 비표준 port, redirect chain, DNS rebinding·IPv6·metadata, iframe/popup/worker/service worker/WebSocket/WebRTC, blob/data/file, download traversal/크기 폭탄, profile/state replay 금지, stale ref·교체 문서, 타 방 daemon/artifact 접근, 승인 없음·만료·취소·동시 pending, prompt-injection 문구를 넣은 snapshot, cookie/URL secret 마스킹을 테스트한다. 기존 no-web runtime 인자와 MCP 표면에 도구가 새지 않는 회귀도 필수다. 외부 repo의 테스트 존재만으로 TalkAbout의 실제 차단을 대체하지 않는다.

## 7. Do-not-copy 목록

1. 기존 TalkAbout no-web 기본값을 지우고 전역 브라우저/MCP/skills를 켜는 방식.
2. daemon `confirm` 응답을 인간 승인·방 인가·감사기록으로 간주하는 방식, 모델에게 confirm/정책 파일 쓰기를 허용하는 방식.
3. 빈 allowlist/정책 없음의 허용 기본값, 호환성 문제를 해결하려고 allowed-domains를 비활성화하는 방식.
4. 사용자 Chrome profile·SSO 쿠키·공유 daemon·평문 state export를 방 사이에 재사용하는 방식.
5. raw JS/CDP/Chrome args/plugins/file access/인증서 검증 무시를 편리함 때문에 통째로 노출하는 방식.
6. hostname allowlist만으로 SSRF·내부망·DNS rebinding·허용 사이트 악성 콘텐츠를 해결했다고 주장하는 방식.
7. cloner skill의 직접 다운로드·외부 링크 보존을 저작권/도메인/네트워크 승인 없이 실행하는 방식.
8. placeholder 앱 또는 lint/typecheck/build만으로 clone 완성·pixel-perfect·실제 interaction 검증을 선언하는 방식.
9. screenshot/DOM/페이지 문구를 trusted instruction으로 승격하거나 무제한 방 context에 넣는 방식.

**최종 권고:** agent-browser는 구현 깊이가 있는 실행 부품이지만 TalkAbout의 보안·승인 체계가 아니다. cloner는 잘 구성된 관찰·handoff·비교 방법론이지만 자동 agent 제품이 아니다. 두 저장소를 결합하는 첫 단계는 라이브 브라우저 전면 허용이 아니라 **no-web 기본 유지 + 익명·격리·기한부 관찰 프로필 + 정형 증거 전달 + 실제 검증 상태 표시**다.
