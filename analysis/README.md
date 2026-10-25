# 분석 기록 / analysis notes

이 폴더는 **세션 산출물**이며 제품 문서가 아니다. 정본 문서 체계는 [`docs/`](../docs/)에 있다.

## 무엇이 들어 있나

외부 저장소 3개를 정밀 분석해 TalkAbout에 쓸 만한 것을 판정한 기록이다.

| 파일 | 내용 |
| --- | --- |
| `00-SYNTHESIS.md` | 1라운드(Orca 단독) 종합 판정 — 채택/차용/대조/제외, 결함 목록, 정정 |
| `01-providers.md` … `05-product-ux.md` | 1라운드 상세 보고서 5편 |
| `BRIEF.md`, `BRIEF-R2.md` | 각 라운드의 분석 브리프(범위·규칙·기준 상태) |
| `r2-00-CROSS-REPO.md` | 2라운드 3-저장소 비교 종합 — 영역별 최적 출처 |
| `r2-adjudication.md` | 1라운드 판정 재검증(18건) |
| `r2-hermes-*.md`, `r2-opencodex-*.md` | 2라운드 상세 보고서 7편 |

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
