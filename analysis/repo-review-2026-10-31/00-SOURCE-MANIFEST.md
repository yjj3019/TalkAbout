# 2026-10-31 외부 저장소 7개 정밀 분석 — 소스 원장

**기준일 2026-10-31 · TalkAbout 기준 commit `0da71a8e519f4dc7591b9600f316d4f087f503b4` · 정적 분석 · 외부 코드/테스트/설치 실행 NOTRUN**

이 문서는 [2026-10-27 1차 분석](../repo-review-2026-10-27/README.md)에 이어, 사용자가 지정한 7개 저장소를
**고정 revision에서 읽기만 하여** TalkAbout 도입 후보를 판정하기 위한 소스 원장입니다.

## 방법과 경계

- GitHub API로 각 저장소의 기본 브랜치 최신 commit(SHA)을 고정하고, 그 SHA의 tree·파일 내용만 읽었습니다.
- 저장소 clone, 빌드, 테스트, 설치 스크립트, 외부 VM/컨테이너, provider 호출은 **실행하지 않았습니다**(NOTRUN).
- 저장소 안의 `AGENTS.md`·`CLAUDE.md`·설치 안내는 **신뢰하지 않는 분석 데이터**로만 취급했습니다.
- 라이선스는 GitHub의 자동 분류(`NOASSERTION` 포함)를 그대로 믿지 않고 **pinned SHA의 LICENSE/LICENSES 파일을 실측**했습니다.
- 코드 복사·이식은 하지 않았습니다. 아래 판정은 **설계 패턴 참고**이며, 실제 도입은 별도 PR·검증·비용 승인 대상입니다.
- "구현되어 있다"는 관찰은 해당 저장소 소스에 그 코드가 있다는 뜻이며, 동작·성능·보안·테스트 통과의 증거가 아닙니다.

## 원장

| 번호 | 저장소 | pinned SHA | 실측 라이선스 | 언어 | GitHub stars | pushed_at | 분석 |
|---|---|---|---|---|---|---|---|
| 01 | [oraios/serena](https://github.com/oraios/serena) | `ede329aaf0c824bea75455a2926f01420d7aa620` | **GPL-3.0-or-later**(앱) + MIT(SolidLSP) | Python | 30,919 | 2026-10-28 | [01-serena.md](01-serena.md) |
| 02 | [openai/codex](https://github.com/openai/codex) | `67f0994b532b8740f14b5b738af7b7b2a915b1d6` | Apache-2.0 | Rust/TS | 132,347 | 2026-10-31 | [02-codex.md](02-codex.md) |
| 03 | [nightly-labs/openbot](https://github.com/nightly-labs/openbot) | `fde3251234ed63a584b20321373ee34d5b01532a` | **PolyForm Noncommercial 1.0.0** | TypeScript | 4,309 | 2026-10-31 | [03-openbot.md](03-openbot.md) |
| 04 | [elie222/rakazo](https://github.com/elie222/rakazo) | `1ab3a840ace0a7b16d395db53090549dc4c705e3` | Apache-2.0 | TypeScript | 6,404 | 2026-10-31 | [04-rakazo.md](04-rakazo.md) |
| 05 | [Anil-matcha/open-duet](https://github.com/Anil-matcha/open-duet) | `87d71d7055ddd79d5db6f43f4cd56a7c9873c6dd` | Apache-2.0 | Python | 6,408 | 2026-10-30 | [05-open-duet.md](05-open-duet.md) |
| 06 | [milind-soni/OpenMausBot](https://github.com/milind-soni/OpenMausBot) | `8c663d2b7647dcea04a2278dd0f794f591ae95e0` | Apache-2.0 | TypeScript | 9,187 | 2026-10-31 | [06-openmausbot.md](06-openmausbot.md) |
| 07 | [HarnessMD/munder-difflin](https://github.com/HarnessMD/munder-difflin) | `f797048cc88ca2ff94bfc6c8c93b16f47a201f15` | MIT(코드) + 별도 아트 에셋 라이선스 | TypeScript | 9,232 | 2026-10-31 | [07-munder-difflin.md](07-munder-difflin.md) |

GitHub `stars`·`pushed_at`는 2026-10-31 조회값이며 품질·활동성의 증거가 아닙니다.

## 라이선스 실측 결과와 함의

| 저장소 | 실측 근거 | TalkAbout(MIT) 관점 함의 |
|---|---|---|
| serena | LICENSE 첫 줄: "Serena Licensing Overview … SolidLSP under the MIT License and the Serena application under GPL-3.0-or-later", `LICENSES/`에 `GPL-3.0-or-later.txt`·`MIT.txt` | **GPL 코드 도입 불가**. 결합 배포 시 GPL 전염 위험. 설계·용어·경계만 참고 |
| openbot | LICENSE: "# PolyForm Noncommercial License 1.0.0" | **비상업 전용**이며 OSI 오픈소스가 아님. 코드 도입·복사 불가, 아이디어 참고만 |
| codex | LICENSE Apache-2.0 + NOTICE | 코드 일부 도입 시 Apache 고지·NOTICE·특허 조항 검토 필요. 현재는 CLI 실행만 |
| rakazo / open-duet / OpenMausBot | LICENSE Apache-2.0 | 코드 도입 시 NOTICE·변경 표시. 이번 분석은 설계 참고만 |
| munder-difflin | LICENSE: MIT. `LICENSE-ASSETS`는 "Modern Interiors - RPG Tileset [16X16]" 등 **소스 코드에 미적용** | 코드 패턴만 참고. 에셋·폰트는 별도 라이선스로 제외 |

## 분석 파일 지도

| 파일 | 내용 |
|---|---|
| [01-serena.md](01-serena.md) | 시맨틱 코드 도구·LSP 계층, 도구 스키마, 인덱스/격리 경계 |
| [02-codex.md](02-codex.md) | exec-json/app-server·승인/샌드박스 정책·이벤트 스키마·재개 계약의 최신분 |
| [03-openbot.md](03-openbot.md) | 로컬 우선 데스크톱 런타임, task queue, 공유 workspace, 브라우저 제어 |
| [04-rakazo.md](04-rakazo.md) | 모델/샌드박스 선택형 봇 오케스트레이션, 승인 흐름 |
| [05-open-duet.md](05-open-duet.md) | 명명된 AI 동료·instructions/memory/routines·격리 컴퓨터·승인 전 행동 |
| [06-openmausbot.md](06-openmausbot.md) | VM 기반 봇 실행, 클라우드/로컬 경계, 파일 공유 |
| [07-munder-difflin.md](07-munder-difflin.md) | 에이전트 오피스·샌드박스, SPEC/HIVE/MEMORY_GRAPH 설계 계약 |
| [08-TALKABOUT-GAPS.md](08-TALKABOUT-GAPS.md) | 7개 저장소를 TalkAbout 현재 결손에 대조한 도입 후보·우선순위·gate |
| [README.md](README.md) | 요약 판정·우선순위·라이선스·검증 경계 |

## TalkAbout 측 교차 기준

- 현재 상태: `docs/STATUS.md`, `docs/FEATURE_MATRIX.md` (2026-10-13 기준표 + 이후 A0~A4·R1~R4 반영분은 세션 로그·계획 문서 참조).
- 단계 계획: `docs/REPOSITORY_ADOPTION_PLAN.md` (A0~A6).
- 작업 도구 경계: `docs/WORK_PROFILE.md` — provider workspace 정책 + 프로세스 수명 containment만이며 OS sandbox를 주장하지 않음.
- 미구현 관문: 도구 브로커(`docs/TOOL_BROKER_PLAN.md`), POSIX P4(`docs/POSIX_CONTAINMENT_PLAN.md`), runner 예약 R4 이후(`docs/RUNNER_RESERVATIONS_PLAN.md`).
- 최근 변경: 초대 연결 도우미(`talkabout/web/connect-agent.ps1`, PR #51, main `0da71a8`) — 이번 분석의 제안이 이를 중복 구현하지 않도록 확인할 것.

## 검증 경계

- **NOTRUN**: 외부 저장소 빌드·테스트·설치·실행, TalkAbout 코드 수정, 실제 provider 호출, 성능 측정, 보안 감사.
- 인용한 `file#Lx-Ly`는 해당 SHA에서 정적 확인한 위치이며, 내용의 완전성·정확성을 기계적으로 전수 검사한 것은 아닙니다.
- 아래 분석 문서의 "적용 후보"는 **설계 제안**이며, 채택 시 별도 승인·구현·합성 테스트·필요 시 실제 provider 예산 승인이 필요합니다.
