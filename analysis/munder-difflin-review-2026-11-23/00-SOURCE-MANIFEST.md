# 00-SOURCE-MANIFEST — munder-difflin

- 분석 대상: <https://github.com/HarnessMD/munder-difflin.git> (공개, MIT)
- 클론 시각: 2026-11-23 (분석 기준일)
- 분석 리비전: `d86ab7acd2c5b817a4ec80d897a44a1db7e8062e` (브랜치 `main`, HEAD == `origin/main`)
- 클론 위치: `.analysis-repos/munder-difflin` (gitignore 대상, TalkAbout 트리 밖 산출물 아님)
- 클린룸 원칙: 아이디어만 참고. 코드·문자열·테스트는 복사하지 않음. 리비전·라이선스 기록 의무.

## 1. 프로젝트 정체

- 이름: **Munder Difflin Classic** (`munder-difflin`), 버전 **0.6.0** (`package.json`)
- 설명: "Clones for you and your team, working 24/7" — 로컬 우선 멀티 에이전트 데스크톱 하네스.
  사용자가 가진 CLI 코딩 에이전트 구독(Claude Code, Codex, Gemini CLI, Antigravity, Grok, Kimi,
  Qwen, OpenCode, Crush, Pi, GitHub Copilot, Cursor + 커스텀 명령, 총 12+ 엔진)을 하나의 "오피스"로
  오케스트레이션하는 Electron 앱.
- 홈페이지: <https://munderdiffl.in/>, 저자: Chaitanya Giri
- 라이선스: **MIT** (`LICENSE`, Copyright (c) 2026 Chaitanya Giri)
  - `LICENSE-ASSETS`: 픽셀아트 타일셋/맵(`src/renderer/src/assets/tilesets`, `maps`)은 LimeZu
    "Modern Interiors - RPG Tileset [16X16]"의 별도 라이선스(상업/비상업 허용, 출처 표기 의무).
    캐스트 스프라이트는 LimeZu가 아닌 절차적(procedural) 생성물로 명시.
- 저장소 주소 이원화: 현재 원격은 `HarnessMD/munder-difflin`, `package.json`/`SECURITY.md`/
  `CONTRIBUTING.md` 등 다수 문서는 구 `chaitanyagiri/munder-difflin`을 가리킴 (→ 01-FINDINGS).

## 2. 규모 측정 (분석 시점, 워크트리 기준)

| 항목 | 값 |
|---|---|
| 커밋 수 (`main`) | 1,485 |
| 최초 커밋 / 최신 커밋 | 2026-05-31 / 2026-11-23 |
| 태그 | 43 (`v0.2.x`~`v0.6.x` 계열) |
| 원격 브랜치 | 371 (main + release/v* + evidence/* + seo-engine/* + blog/* + fix/* + feat/* 등) |
| 추적 파일 수 | 5,561 |
| 워크트리 디스크 | 384.6 MB (node_modules 제외, 미설치 상태) |
| 텍스트 파일/라인 | 2,512 개 / 561,344 줄 (node_modules·.git·dist·out·build 제외) |

확장자 상위 (개수): `.png` 2,670 · `.html` 572 · `.md` 426 · `.css` 413 · `.js` 410 ·
`.ts` 405 · `.tsx` 143 · `.cjs` 139 · `.json` 115 · `.webp` 58 · `.mp4` 47 · `.jpg` 32 ·
`.gif` 27 · `.svg` 26 · `.njk` 14

주요 기여자: Chaitanya Giri 891 · chaitanyagiri 253 · Gulum 56 · Vyapak Goyal 52 ·
github-actions[bot] 31 · Gastón Péchieu 27 · wall-sync 16 외 (자동 커밋 봇 다수 혼재)

## 3. 디렉터리 구조 (1계층)

```
.claude/           에이전트 설정·룰 (트래킹됨 — 위생 검토 대상)
.github/           워크플로 7개(ci, release, blog, pr-evidence, contributor-role,
                   contributors, issue-os-labeler), 템플릿, CODEOWNERS, FUNDING
blog/              마케팅 블로그 (HTML·njk 상당량)
build/             빌드/배포 스크립트 디렉터리 (electron-builder, notarize 등)
docs/              문서 (media, screenshots 포함)
evidence/          영속 증거 수집물 (SEO/릴리스 운영 기록; 이미지·HTML 다수)
hive/              에이전트 간 협업 저장소 샘플/스펙 (로컬 git 배송 시뮬레이션)
landing-remotion/   랜딩 페이지 (Remotion 영상 포함)
prototypes/        프로토타입
resources/         에셋/기타
scripts/           검증·운영 스크립트 (verify-*.mjs 미러 검증기 등)
seo/               SEO 엔진 산출물 (검색 엔진 대상 콘텐츠 운영)
src/               앱 본체: core(순수 로직) · main(Electron 메인) · preload ·
                   renderer(React SPA) · shared
test/              테스트 115개 (node:test) + repro/ 재현 스크립트
tools/             개발·빌드 도구 (copy-main-assets, ensure-pty-perms, 맵 생성기 등)
```

## 4. 기술 스택 (package.json 기준)

- 실행: Electron + **electron-vite**(메인 `out/main/index.js`), React 18 + TS(tsconfig node/web 분리)
- 핵심 의존: `better-sqlite3`(DB) · `node-pty`(실제 터미널 프로세스) · `@xterm/xterm`
  (터미널 UI) · `monaco-editor`/`@uiw/react-codemirror`(편집기) · `pixi.js`(오피스 플로어
  렌더링) · `localtunnel`(터널) · `posthog-node`(텔레메트리) · `libsodium-wrappers`(암호화)
  · `commit-graph` · `blobatar` · `i18next`/`react-i18next`(i18n) · `@openai/agents-realtime`
  (보이스)
- 개발: `electron-rebuild`, node-pty 권한/ConPTY 패치 도구(`postinstall` 3단계), 타입체크
  (node/web), `node --test` 테스트, `check:links`(릴리스 링크 검증)
- Node 요구: `.nvmrc` 22 / `engines` ">=20 <25" (이원화)

## 5. 테스트·CI·릴리스 요약

- 테스트 115개(`test/*.test.cjs`, `node:test`) — `test/load-ts.cjs`로 TS 런타임 로드,
  Electron/네이티브 의존 로직은 미러 스크립트(`scripts/verify-*.mjs`) 검증 (→ 01-FINDINGS F1)
- CI: macOS(Node 20) 단일 러너, typecheck + check:links + (실패 허용) build
- 릴리스(0.6.0~): 소스 전용. v* 태그 → GitHub Release 발행(빌드/서명/업로드 없음)
- 시크릿 워크플로 사용: `GITHUB_TOKEN`, `DISCORD_BOT_TOKEN` 2종, 모두 env 경유.
  저장소 내 시크릿 누출 흔적 없음(grep 검증: 문서·픽스처 분할 문자열뿐)

## 6. 관련 문서

- README.md, CHANGELOG.md(Keep a Changelog), DESIGN.md, SPEC.md, HIVE.md,
  MEMORY_GRAPH_SPEC.md, RELEASE.md, RELEASE-CHECKLIST.md, SECURITY.md, TELEMETRY.md,
  CONTRIBUTING.md, CONTRIBUTORS.md, OPERATING 계열은 없음(설치형 앱)
