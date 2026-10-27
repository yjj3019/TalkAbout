# 7개 저장소 소스 고정 기록

조사일: 2026-10-27 KST. GitHub REST metadata와 git commit을 확인하고 아래 revision으로 detached checkout했다.
외부 코드의 설치·실행·provider 호출은 하지 않았다. README와 소스·테스트의 정적 분석이며 성능·안전성 실측이 아니다.
checkout은 `.tmp-verify/repo-review/` 아래 ignored 조사 자료다. 소스 복사/제품 의존성 추가 없음.

| 번호 | 확인한 저장소 | 고정 revision | GitHub 최상위 license metadata |
|---|---|---|---|
|1|[herdrdev/herdr](https://github.com/herdrdev/herdr/tree/2713feb56e9c9db33992db0a262c68f7426d46dd)|`2713feb56e9c9db33992db0a262c68f7426d46dd`|Apache-2.0|
|2|[vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser/tree/edb40f1878864f90e14f6c11e26fdafd3fecc8dc)|`edb40f1878864f90e14f6c11e26fdafd3fecc8dc`|Apache-2.0|
|3|[codewhale-hq/Codewhale](https://github.com/codewhale-hq/Codewhale/tree/c5cd7c87f004af4918dd710adb9e3ed784b18c9d)|`c5cd7c87f004af4918dd710adb9e3ed784b18c9d`|MIT|
|4|[Yeachan-Heo/oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode/tree/e10206d8789b0ac4e5214eddbfbd203c1b757dea)|`e10206d8789b0ac4e5214eddbfbd203c1b757dea`|MIT|
|5|[PDFMathTranslate/PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate/tree/3f2ab95ae9d74d0ed9a5ff018fe71280223906a8)|`3f2ab95ae9d74d0ed9a5ff018fe71280223906a8`|AGPL-3.0|
|6|[JCodesMore/ai-website-cloner-template](https://github.com/JCodesMore/ai-website-cloner-template/tree/ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb)|`ee3f5a2f31fd549b9593fa4f7cf6d2955ee593bb`|MIT|
|7|[can1357/oh-my-pi](https://github.com/can1357/oh-my-pi/tree/9c4933a978439acfec958488f14b36ac6a7ec187)|`9c4933a978439acfec958488f14b36ac6a7ec187`|MIT|

## URL 정정

3번 설명의 잘린 Codewhale URL은 4번에 적힌 `codewhale-hq/Codewhale`로 확인했다.
4번 이름에 해당하는 저장소는 GitHub API에서 `Yeachan-Heo/oh-my-claudecode`로 확인했다.
이 정정은 사용자가 제공한 Codewhale 링크를 OMC로 오인하는 것을 방지하기 위한 것이다.

## 증거 규칙

- 각 상세 보고서는 README 기능 주장과 실제 구현 위치를 분리한다.
- 테스트 파일 존재/내용을 읽은 것은 테스트 통과 증거가 아니다. 외부 저장소 테스트는 NOTRUN.
- GitHub top-level license만으로 모든 dependency/model/font/asset 권리를 판단하지 않는다.
- 직접 코드 이식과 독립 설계 아이디어 채택은 구분한다. AGPL 연계·배포 범위는 법률 검토 사항이다.
- 현재 TalkAbout 비교 기준: R3 one-shot server start/uncertain retirement, R4 DPAPI opt-in,
  W1 Codex exec-json owner opt-in workspace, 공유 UTF-8 manifest, immutable artifact version.
  브라우저/PDF/범용 plugin/MCP 자동 허용은 현재 기본 범위가 아니다.
