# 7개 외부 저장소 정밀 분석: TalkAbout 적용 판단

**기준일 2026-10-31 · TalkAbout 기준 commit `0da71a8e519f4dc7591b9600f316d4f087f503b4` · 소스 정적 분석 · 제품 코드 변경 없음 · 외부 코드/테스트/설치 실행 NOTRUN**

사용자가 지정한 7개 저장소를 고정 revision에서 읽고, **TalkAbout 현재 결손에 실제로 도입 가능한 개선점만** 추렸습니다.
1차 분석([2026-10-27](../repo-review-2026-10-27/README.md))이 "공동 코딩을 위한 도구·작업 계약"이었다면, 이번 7개는 **"에이전트를 자기 PC/샌드박스에서 상주시키는 제품"** 계열이라 결론이 다릅니다.

## 결론

**직접 이식할 코드는 없습니다.** 7개 모두 "에이전트가 자기 환경에서 일한다"는 전제이고, TalkAbout은 "서버가 소유한 room 계약 안에서 만나며 서버는 모델·도구를 실행하지 않는다"입니다. 게다가 serena는 **GPL-3.0-or-later**, openbot은 **PolyForm Noncommercial**이라 코드 도입 자체가 부적합합니다.

도입 가치는 **설계·관찰·승인 데이터 모델**에 있습니다. 지금 바로(제품 코드 실행 없이) 고정할 수 있는 P0 4건:

1. **관측 실패를 허용·성공으로 승격하지 않는 규칙** — hook/stream/parser 실패는 `unknown` 유지 (munder·OpenMausBot·openbot·codex 공통 신호)
2. **A0 exec JSONL 고정 fixture + 버전 태그** — provider tool 표면 드리프트 감지 (codex·serena)
3. **승인 데이터 요건 확정** — `source(human/saved_rule/timeout)`, 결정·전달·소비 시각, digest, fence, 자동승인 0건 (openbot·rakazo·open-duet·OpenMausBot·codex·munder)
4. **A5/A6 격리·placement 계약표 선행** — 표 밖 backend 실행 거절, "sandbox" 용어 금지 (OpenMausBot·rakazo·munder·openbot)

이 4건은 전부 **문서 gate + 순수 검증기 + 합성 fixture**로 검증 가능하고, 실제 provider/VM/브라우저 호출이 필요 없습니다.
그 다음 P1(경로 검증, read-only 진단, 선언 scope 직렬화) → P2(승인 스키마 확장, capability token 설계, config flag 감지) → A5/A6 실제 adapter 순서입니다.

## 저장소별 판정 요약

| 저장소 | 가장 유용한 내용 | 그대로 가져오면 안 되는 것 | 판단 |
|---|---|---|---|
| [serena](01-serena.md) | 도구 descriptor 3단 투영(등록→모드/context→클라이언트 schema), 경로 봉쇄(lexical+realpath), 심볼 유일성→LSP edit→atomic write, LSP 수명 fail-fast | GPL 코드, LSP/REPL 상주, 서버 측 편집 실행, activation command | **설계 참고만** — P1 경로 검증 |
| [codex](02-codex.md) | exec item 표면·flag 드리프트 fixture, 승인 거절 응답 스키마, `features.*` no-op 위험 | exec-server/remote·auto_review, app-server 기본 전환, sandbox 집행 주장, 코드 복사 | **A0 보강** — P0-2·P2-1 |
| [openbot](03-openbot.md) | lane별 보류 사유의 닫힌 enum·사용자 축약 투영, 선언 자원(`browser`,`workspace:<path>`)만 직렬화, 잔여 작업 boot 정리 | `danger-full-access`·`approvalPolicy: never`, 자동 승인 grant, Computer Use 데몬 | **관찰 계약 참고** — P1-2 |
| [rakazo](04-rakazo.md) | run 상태와 "compute를 잡는 대기" 구분, 장기 대기 알림·만료 임계값, 외부 내용 taint 후 승인 격상, durable 멱등키 | Never ask·모델 승인·실패 run 자동 재큐잉, "container=sandbox" 전제 | **A5/A6 승인 gate** — P0-3·P2-2 |
| [open-duet](05-open-duet.md) | read-only 운영 진단, 승인 결정/전달/소비 3분리, fail-closed 격리 gate 문구 | CLI permission 래핑, NEEDS INPUT 문자열 파싱, 제거 전 정착 필수 | **A4 진단·승인 분리** — P1-2·P2-2 |
| [OpenMausBot](06-openmausbot.md) | placement×isolation×lease 3축 계약표, "관찰 불가≠허용" 규칙, per-turn capability token(설계 입력) | VM/원격 실행 하네스, Full access 기본, 하네스가 CLI 소유 | **A5/A6 계약표** — P0-4 |
| [munder-difflin](07-munder-difflin.md) | 관찰/훅 실패 규칙, 경로 containment 테스트 항목(final/intermediate/dangling symlink), untrusted descriptor default-deny | `bypassPermissions`·hook fail-open·공개 tunnel, 아트 에셋(LimeZu 별도 라이선스) | **A2/A0 테스트 항목** — P0-1·P1-1·P1-3 |

전체 교차 수렴·결손 대조·통합 표는 [08-TALKABOUT-GAPS.md](08-TALKABOUT-GAPS.md).

## 우선순위와 시작점

| 순서 | 항목 | 성격 | 근거 |
|---|---|---|---|
| 1 | P0-1 관측 실패 ≠ 허용 | 문서·합성 회귀 | munder·OpenMausBot·openbot·codex |
| 2 | P0-2 exec schema fixture | 테스트 전용 | codex·serena |
| 3 | P0-3 승인 데이터 요건 | 설계 문서(A5/A6 선행) | openbot·rakazo·open-duet·OpenMausBot·codex |
| 4 | P0-4 격리·placement 계약표 | 설계 문서(A5/A6 선행) | OpenMausBot·rakazo·munder |
| 5 | P1-1 W1 경로 3회 재검증 | 단위 테스트 | serena·munder |
| 6 | P1-2 read-only 진단/대기 사유 | 관찰 API·UI(합성) | openbot·rakazo·open-duet |
| 7 | P1-3 A2 선언 scope 직렬화 | A2 실행 전 설계 | openbot·munder |
| 8 | P2·P3 (승인 스키마 확장, config flag 감지, capability token, POSIX P4) | 후속 | codex·OpenMausBot·기존 계획 |

**첫 수직 단위**는 "순수 함수·fixture·문서 gate"입니다. A5/A6 실제 adapter는 위 P0 gate가 전부 통과한 뒤 **별도 예산 승인**으로 엽니다.

## 라이선스 (실측)

| 저장소 | 실측 | 코드 도입 |
|---|---|---|
| serena | GPL-3.0-or-later(앱) + MIT(SolidLSP) | **불가**(전염) |
| openbot | PolyForm Noncommercial 1.0.0 | **불가**(비상업) |
| codex | Apache-2.0 + NOTICE | 조건부(고지·특허) — 이번엔 패턴·fixture만 |
| rakazo / open-duet / OpenMausBot | Apache-2.0 | 조건부 — 이번엔 설계 참고만 |
| munder-difflin | MIT(코드) + LimeZu 아트 에셋 별도 | 코드 패턴만, 에셋 제외 |

## 검증 경계

- **한 것**: GitHub API로 고정 SHA의 tree·파일 내용 읽기, TalkAbout 현재 코드·문서와 교차 대조, 저장소별 분석 7편·통합 판정 1편 작성.
- **NOTRUN**: 외부 저장소 빌드·테스트·설치, 실제 provider/app-server/LSP/MCP, VM·Podman·컨테이너, 브라우저·원격 실행, 성능·보안 측정.
- 이 분석은 **설계 제안**입니다. 채택은 `docs/REPOSITORY_ADOPTION_PLAN.md` 편입 PR + 합성 테스트 + 필요 시 실제 예산 승인으로만 진행합니다.
- 인용 링크는 고정 SHA의 정적 위치이며, 내용의 완전성·정확성을 기계적으로 전수 검사한 것은 아닙니다.

## 문서 지도

| 파일 | 내용 |
|---|---|
| [00-SOURCE-MANIFEST.md](00-SOURCE-MANIFEST.md) | 고정 SHA·라이선스 실측·방법·계보 |
| [01-serena.md](01-serena.md) | 시맨틱 코드 도구·LSP·경로 봉쇄 (GPL) |
| [02-codex.md](02-codex.md) | exec-json·승인/샌드박스·schema 드리프트 |
| [03-openbot.md](03-openbot.md) | 로컬 데스크톱 런타임·lane 사유·자원 직렬화 |
| [04-rakazo.md](04-rakazo.md) | run 상태·대기·taint 승인·멱등키 |
| [05-open-duet.md](05-open-duet.md) | 승인 3분리·read-only 진단 |
| [06-openmausbot.md](06-openmausbot.md) | placement×isolation×lease·VM 경계 |
| [07-munder-difflin.md](07-munder-difflin.md) | 관찰 실패 규칙·경로 containment·descriptor |
| [08-TALKABOUT-GAPS.md](08-TALKABOUT-GAPS.md) | 교차 수렴·결손 G1~G8·통합 우선순위·gate |

## 이 문서가 주장하지 않는 것

- 7개 제품의 품질·보안·성능 순위가 아닙니다.
- TalkAbout의 기능 추가·구현 완료가 아닙니다. 코드 변경은 0줄입니다.
- "sandbox", "격리", "승인 자동화"가 TalkAbout에 도입되었다는 뜻이 아닙니다.
- 실패 run 자동 재시도, 모델 승인, 데스크톱 자동화 개방은 이 문서에서 **명시적으로 금지**한 방향입니다.
