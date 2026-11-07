# 분석 기록 / analysis notes

이 폴더는 **세션 산출물**이며 제품 문서가 아니다. 정본 문서 체계는 [`docs/`](../docs/)에 있다.

## 무엇이 들어 있나

여러 라운드에 걸쳐 외부 저장소를 정밀 분석해 TalkAbout에 쓸 만한 것을 판정한 기록이다.

| 파일 | 내용 |
| --- | --- |
| `00-SYNTHESIS.md` | 1라운드(Orca 단독) 종합 판정 — 채택/차용/대조/제외, 결함 목록, 정정 |
| `01-providers.md` … `05-product-ux.md` | 1라운드 상세 보고서 5편 |
| `BRIEF.md`, `BRIEF-R2.md` | 각 라운드의 분석 브리프(범위·규칙·기준 상태) |
| `r2-00-CROSS-REPO.md` | 2라운드 3-저장소 비교 종합 — 영역별 최적 출처 |
| `r2-adjudication.md` | 1라운드 판정 재검증(18건) |
| `r2-hermes-*.md`, `r2-opencodex-*.md` | 2라운드 상세 보고서 7편 |
| `repo-review-2026-10-27/` | 7개 저장소 묶음(1차) — herdr, agent-browser, Codewhale, oh-my-claudecode, PDFMathTranslate, website cloner, oh-my-pi. TalkAbout 결손 대조와 단계별 반영 계획(A0~A6)의 근거 |
| `repo-review-2026-10-31/` | 7개 저장소 묶음(2차) — serena, codex, openbot, rakazo, open-duet, OpenMausBot, munder-difflin. 승인·관찰·격리 데이터 계약과 A5/A6 선행 gate 후보 |
| `product-direction-review-2026-10-31.md` | 첨부 "가상 오피스+IDE+에이전트 오케스트레이션" 조사 정밀 검증 — 30+ 저장소 라이선스·활동 실측, TalkAbout 기존 자산 대비 격차, 도입 순서 |
| `self-review-2026-11-01.md` | 자체 점검 — 보안·UI 접근성·테스트/CI·운영. 로그인 스로틀 우회, 1클릭 토큰 클립보드 잔존, 자격증명 응답 무음 폐기, Linux/이미지 CI 공백, SSOT 드리프트, 재시작·백업 부재 |
| `BRIEF-R3.md` | 3라운드(성능) 브리프 — handy·t3code·claude-mem, 정적 분석 규칙 |
| `r3-00-SYNTHESIS.md` | 3라운드 종합 — 세 저장소 성능 프로필, 횡단 패턴 6개, TalkAbout P0/P1/P2 착수 순서(실측 근거) |
| `r3-handy.md` · `r3-t3code.md` · `r3-claude-mem.md` | 3라운드 상세 3편(각 440~554줄). 이벤트 폭·블로킹·훅 예산·전달 규율 |
| `r3-talkabout-baseline.md` | TalkAbout 성능 기준선 — pytest/Node 실행값, 인프로세스 HTTP·도메인 마이크로벤치, 병목 14건. 벤치 스크립트는 `.tmp-perf/` |
| `r4-harness.md` | P0-0 성능 스냅샷 하네스(`tools/perf_snapshot.py`) 사용법·측정 정의·한계·CI 연결 제안 |
| `r4-web.md` | P1-4 웹 SSE 갱신 코얼레스(100ms 디바운스+400ms 상한, hidden 지연) before/after·검증·위험 |
| `r4-verification.md` | R3 P0/P1 반영 내역·검증 결과·라이선스 준수·남은 한계. 이 샌드박스 제약과 우회 검증 방법 기록 |
| `r5-p2-verification.md` | P2 경량 fence·자격증명 배치 조회, 보안·호환성 검증 및 남은 한계 |
| `BRIEF-R6.md` | 6라운드(협업 작업장) 브리프 — artiboard · session-peer · moai · openclaw · hermes-agent · openmuse · OpenGrokBot |
| `r6-artiboard.md` · `r6-session-peer.md` · `r6-moai.md` | 6라운드 상세 3편. 보드 동시성(claim+if_version)·전달 증거(제출≠소비≠ACK)·저장소-내재 트래커(필드 3-way 머지·감독→일꾼) |
| `r6-openclaw-hermes-dreaming.md` | OpenClaw 드리밍(Light→REM→Deep)·Hermes 메모리 외부 조사 **점별 검증** — ✅14건+정정 7건+모방 금지 목록 |
| `r6-openmuse.md` · `r6-opengrokbot.md` | AG-UI 실행 인프라(제안→해시→클레임→영수증·멱등 키)·teammate(=컴퓨터)·BYOK·외부 데이터 펜스 |
| `r6-00-SYNTHESIS.md` | 6라운드 종합 — 영역별 최적 출처, **반영 내역**(`talkabout/content_framing.py`+테스트 16건), 다음 단계, 모방 금지 목록 |
| `BRIEF-R7.md` | 7라운드 비교 분석 — autoresearch, harness2, OpenAlex, bloks, Tutti, session-peer, Artiboard; TalkAbout 적용 우선순위·라이선스·안전 경계 |
| `R7_IMPLEMENTATION.md` | R7의 즉시 적용 가능한 worker stop/result-recovery 경합 보완, fail-closed 경계와 미실행 검증 상태 |

## 인용 규칙

보고서의 경로는 **각 외부 저장소 루트 기준**이다. 예: `orca-analysis/src/main/...`,
`hermes-agent/acp_adapter/server.py:120`, `opencodex/src/quota/...`.
이 저장소에는 외부 clone을 포함하지 않는다. 필요하면 다음으로 다시 받는다.

```sh
git clone --depth 1 https://github.com/stablyai/orca.git orca-analysis
git clone --depth 1 https://github.com/lidge-jun/opencodex.git opencodex
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
```

분석 시점 HEAD: orca `51dcef8` · opencodex `9f0d40a`(v2.92.0) · hermes-agent `930e5ed`.
세 저장소 모두 MIT이며, 이 폴더에는 **코드를 복사하지 않았다**(구조·프로토콜 참조만).

## 주의

- 여기 적힌 수치·판정은 **작성 시점 스냅샷**이다. 현재 상태의 정본이 아니다.
- 이 폴더의 내용은 빌드·테스트 경로에서 읽지 않는다.
- 판정과 달리 실제로 반영된 변경의 현재 상태는 [`docs/STATUS.md`](../docs/STATUS.md)와
  [`docs/SESSION_LOG.md`](../docs/SESSION_LOG.md)를 따른다.
