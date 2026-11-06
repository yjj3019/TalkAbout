# OpenClaw 드리밍 · Hermes 메모리 — 외부 조사 내용 정밀 검증 (R6)

- **검증 대상 클론 1**: `C:\AI-Codding\claude\TalkAbout\.analysis-repos\openclaw` (HEAD `40b34f2f44b08a8e1583e95f897d1d14ce784fa6`, 2026-11-05, 읽기 전용)
- **검증 대상 클론 2**: `C:\AI-Codding\claude\TalkAbout\.analysis-repos\hermes-agent` (HEAD `8e7cf55eb673ab4ee5a35cd41faf35592eb70bd6`, 2026-11-05, 읽기 전용)
- **검증 방법**: 두 클론을 read/grep/glob으로 직접 열어 경로·코드·문서를 대조했고, 외부 2건(hermes-dreaming GitHub README, GitHub API issue 25309)만 web_fetch로 확인. 모든 판정은 「문서에 적혀 있음」/「코드에서 확인」/「찾지 못함」으로 구분 표기. 저장소 수정 없음(읽기 전용 유지).
- **검증 방법 상세**: (a) 존재성은 glob으로 전수 확인, (b) 동작 서술은 코드 심볼·주석·문서 문장을 1-2줄 짧은 인용으로 고정, (c) 미발견은 grep 결과 0건을 근거로 명시, (d) 외부 주장(카탈로그·issue 상태)만 HTTP로 확인하고 그 내용은 "외부 데이터"로 취급.

---

## §0 검증 판정 요약

> 이 보고서는 다음 질문에 답한다: ①파일/명령/기본값이 실제 존재하는가 ②위상 흐름과 검증 단계가 실제 그런가 ③카탈로그·issue 상태가 맞나 ④TalkAbout의 어느 자리에 무엇을 넣을 수 있는가.

| 주장 번호 | 판정 | 핵심 근거 (파일:줄) |
|---|---|---|
| 1. `extensions/memory-core/src/dreaming.ts` 존재·서비스·스케줄러·훅·owner 미지정 시 skip | ✅ 정확 | `dreaming.ts:550-551`(`memory-core-dreaming` 서비스), `:613-656`(`before_agent_reply`, heartbeat/cron), `:442-447`(owner 없으면 cron 미생성·1회 경고) |
| 2. `src/memory-host-sdk/dreaming.ts` 존재 | ✅ 정확 | 파일 존재(559줄); 설정 해석·기본값 22-42, 339-440 |
| 3. `docs/concepts/dreaming.md`·`docs/reference/memory-config.md` 존재 | ✅ 정확 | 개념 문서 419줄, 참조 문서 `## Dreaming` 섹션 936-985 |
| 4. Light→REM→Deep 3단계·Deep 검증 체크·preimage·fallback | ⚠️ 골격 정확, 세부 명칭 일부 부정확 | 순서·비MEMORY.md 원칙·검증·fallback은 코드/문서로 확인. 단 action은 `added/merged/superseded`(「replace」 아님), 스코어링 6개 신호+phase boost, minUniqueQueries는 게이트 겸용 |
| 5. 저장소 4종 + "새 내용 없으면 diary 없음" | ✅ 정확 | MEMORY.md·DREAMS.md·SQLite plugin state(`dreaming-state.ts:12-20`)·`memory/.dreams/` corpus; memory-config.md:981-982 |
| 6. 기본 활성화·`0 3 * * *`·명령어 목록 | ✅ 정확(의미 보정 1건) | `DEFAULT_MEMORY_DREAMING_FREQUENCY = "0 3 * * *"`(memory-host-sdk/dreaming.ts:22), enabled 기본 true(:375); 명령은 §1-6 목록 |
| 7. 비밀 미마스킹 수집·Dream Diary 기본/설정 모델 사용 | ✅ 정확 | dreaming.md:109 "Transcript text is not secret-masked before ingestion." / dreaming.md:147 "uses the workspace agent's default model unless `dreaming.model` is configured" |
| 8. `agent/memory_manager.py`·`agent/memory_provider.py` 존재 | ✅ 정확 | 두 파일 모두 존재(935줄 / ABC) |
| 9. hermes 문서 2건 + 훅 4개 | ✅ 정확 | 훅 `sync_turn`(memory_provider.py:151)·`on_session_end`(:183)·`on_pre_compress`(:194)·`on_memory_write`(:212) |
| 10. 세션 종료 추출+새 세션 전환 단일 직렬화 task | ✅ 정확 | memory_manager.py:638 "Queue old-session extraction + provider rebinding as ONE serialized task." |
| 11. `memory.write_approval: true` → pending 목록·`/memory pending|approve|reject` | ✅ 정확 | memory.md:333-350, tools/memory_tool.py:212·354, config_defaults.py:1303·1467 |
| 12. hermes-dreaming 플러그인 존재·카탈로그·규칙기반 후보·표준 memory tool·별도 저널·선택 cloud judge | ✅ 정확 | `plugin-catalog/hermes-dreaming.yaml` + README 원문(웹). `website/plugins.json`은 클론에 없음 → 카탈로그는 `plugin-catalog/` |
| 13. issue 25309 open·내장 3-stage 없음 | ✅ 정확 | GitHub API `"state":"open"`, `closed_at:null`; hermes 클론 grep에서 내장 dreaming 구현 없음 확인 |

판정 범례: ✅ = 클론/웹 증거로 실제 확인 / ⚠️ = 골격은 맞으나 세부 명칭·구성 요소가 다름 / ❌ = 틀림 / 미확인 = 근거 부족.

**종합 결론(한 줄)**: **pasted report의 13개 주장은 모두 클론/웹에서 확인되거나 미세 보정만 필요한 수준** — ❌ 판정은 0건, ⚠️는 주장 4뿐(명칭·구성요소 수준)이며, TalkAbout에 반영할 신규 아이디어는 §5의 22개 항목으로 정리됨.

### 클론이 실제 담고 있는 주장 관련 파일 목록(누락 없음)

OpenClaw(주장 1-3의 "cited files" 전부 존재):

- `extensions/memory-core/src/dreaming.ts` — 서비스·훅·승격 오케스트레이션.
- `src/memory-host-sdk/dreaming.ts` — 설정 해석·기본값.
- `docs/concepts/dreaming.md` — 개념 문서.
- `docs/reference/memory-config.md` — 설정 참조(드리밍 섹션 포함).
- 보조 파일(검증에 사용): `dreaming-cron.ts`, `dreaming-cron-contract.ts`, `dreaming-phases.ts`, `dreaming-phase-types.ts`, `dreaming-narrative.ts`, `dreaming-dreams-file.ts`, `dreaming-state.ts`, `dreaming-consolidation*.ts`, `dreaming-markdown.ts`, `dreaming-command.ts`, `short-term-promotion*.ts`, `memory-budget*.ts`, `cli.ts`, `cli-status.runtime.ts`, `cli-rem.runtime.ts`.

Hermes(주장 8-11의 "cited files" 전부 존재): `agent/memory_manager.py`(935줄), `agent/memory_provider.py`, `website/docs/user-guide/features/memory.md`(555줄), `website/docs/developer-guide/memory-provider-plugin.md`(562줄), `plugin-catalog/hermes-dreaming.yaml`, `hermes_cli/write_approval_commands.py`, `tools/memory_tool.py`, `hermes_cli/config_defaults.py`.

---

## §1 OpenClaw 점별 검증 (주장 1-7)

### 주장 1 — `extensions/memory-core/src/dreaming.ts` — ✅ (코드에서 확인)

- 파일 존재: `extensions/memory-core/src/dreaming.ts`(총 657줄). 서비스 등록은 `dreaming.ts:550-551` — `api.registerService({ id: "memory-core-dreaming", apiVersion: 2, ... })`.
- 스케줄러 후킹: `dreaming.ts:613-656` — `api.on("before_agent_reply", ...)` 등록이며 `{ eligibleTriggers: ["heartbeat", "cron"] }`. 훅 본문은 시스템 이벤트 토큰을 검사한다: `includesSystemEventToken(event.cleanedBody, DREAMING_SYSTEM_EVENT_TEXT)`(617-622). 토큰 실제 값: `MEMORY_DREAMING_SYSTEM_EVENT_TEXT = "__openclaw_memory_core_short_term_promotion_dream__"`(memory-host-sdk/dreaming.ts:26-27).
- manifest 크론 프로비저닝: `reconcileShortTermDreamingCronJob`(dreaming-cron.ts:88-272) — 상태 `unavailable/disabled/doctor-required/owner-required/added/updated/noop`(89-98). 크론 이름 `MANAGED_MEMORY_DREAMING_CRON_NAME = "Memory Dreaming Promotion"`(memory-host-sdk/dreaming.ts:24), 태그 `[managed-by=memory-core.short-term-promotion]`(25).
- owner 미지정 시 skip: `dreaming.ts:442-447` — "managed dreaming cron job was not created because no ambient agent owner is configured. Set `agents.defaults.systemAgent.agentId` to an existing agent to enable automatic dreaming."(문서 250-254에도 "Without one, reconciliation skips creation and warns once while ownership remains unresolved."). 즉 "소유 시스템 에이전트가 결정되지 않았으면 작업 생성이 생략될 수 있다"는 서술 정확.
- 실행 큐: `KeyedAsyncQueue`(dreaming.ts:30-36) — "Installed generations coordinate the same durable declaration without sharing managers."
- 동작 모델: 관리 cron은 독립 세션에서 기동(`sessionTarget: "isolated"`, dreaming-cron.ts:47, 122)하고, `runInBackground: ctx.trigger === "cron"`(dreaming.ts:634). 메시지 본문에 시스템 이벤트 토큰을 실어 보내므로(122) `before_agent_reply` 훅이 대신 실행을 소비한다(레거시 cron이 남긴 토큰·이름 호환: `LEGACY_MEMORY_LIGHT_DREAMING_CRON_NAME = "Memory Light Dreaming"`·`"Memory REM Dreaming"`, memory-host-sdk/dreaming.ts:28-33 — 예전 라이트/REM 분리 cron에서 현재 통합 "Memory Dreaming Promotion"으로 이행되었음을 보여주는 이력, 세부는 `migration/dreaming-cron-classifier.ts`).
- 주변 경로: Doctor 통합(`src/gateway/server-methods/doctor.ts:270-272` — 크론 이름+payload 토큰 일치 검사), 시작 정리(dreaming-startup-cleanup.ts), E2E(`test/dreaming-startup-cleanup.e2e.test.ts`), UI 대시보드(`ui/src/pages/agents/memory/dreaming.ts`), 시스템 이벤트 소유권 테스트(dreaming.system-event-ownership.test.ts).

### 주장 2 — `src/memory-host-sdk/dreaming.ts` — ✅ (코드에서 확인)

- 파일 존재(559줄). 책임: 기본값 정의(memory-host-sdk/dreaming.ts:22-42)와 설정 해석(`resolveMemoryDreamingConfig` 339-440, `resolveMemoryDeepDreamingConfig` 455-459, `resolveMemoryLightDreamingConfig`·`resolveMemoryRemDreamingConfig`). `src/plugin-sdk/memory-core-host-status.ts:13-33`에서 plugin SDK로 재수출 — plugin 경계(extensions/AGENTS.md의 "imports: `openclaw/plugin-sdk/*`" 규칙)를 지키는 구조.
- 보고서가 말한 "run-event handling"이 이 파일에 담긴 것과 달리, 실제 run 핸들링은 `extensions/memory-core/src/dreaming.ts`가 소유 — 위치 서술만 조정(파일 2번 항목 자체는 정확).

### 주장 3 — 문서 2건 — ✅ (문서에 적혀 있음)

- `docs/concepts/dreaming.md` — 요약: "Background memory consolidation with light, deep, and REM phases plus a Dream Diary"(1-2). 기본값·비활성화 경로(13-20), "When the cron scheduler is disabled ... dreaming defers automatic job creation"(22-31), "What dreaming writes"(33-41), 위상 표(72-78), 랭킹·게이트(96-102), 병합 안전(127-141), 시크릿(109-112), 일정·권한(211-261), 슬래시 명령(304-313), CLI(315-347).
- `docs/reference/memory-config.md:936` "## Dreaming" — 설정 키 `enabled|frequency|model|storage|\*_phase` 등(946-985), 저장 규칙(981), diary 규칙(982), `dreaming.model` 사용 시 `subagent.allowModelOverride: true` 요구(985).
- **활용 가치(보고서 3번 보강)**: memory-config.md의 dreaming 표는 설정 키·기본값 1줄 목록이므로 TalkAbout 설정 스키마 초안의 참고 컬럼으로 그대로 쓸 수 있음 — "참조 문서가 이미 존재"함이 핵심(코드 재발명 불필요). 보고서가 3번을 단순 "존재"로만 한 것은 부족하나 오류는 아님.

### 주장 4 — 3단계 흐름과 Deep 검증 — ⚠️ (골격 정확, 세부 명칭 일부 부정확)

**실제로 정의된 흐름(코드 기준, 순서 그대로)**:

1. **라이트(Light)** — `prepareLightDreaming`(dreaming-phases.ts:1063-1102). 소스는 daily memory 파일·세션 코퍼스·리콜 스토어(983-1040)에서 메시지 단위 스니펫을 수집(`SESSION_INGESTION_MAX_MESSAGES_PER_SWEEP` 등 상한 960-978). `dedupeEntries(..., dedupeSimilarity 0.9)`로 중복군 제거(1068-1077), diary 최근 4개 항목 대비 우선순위화(`prioritizeLightEntriesByDiaryCoverage`, 1078-1083). `---` 구분자 관리 일일 파일의 `## Light Sleep` 블록에만 기록(`MANAGED_DAILY_DREAMING_BLOCKS`, 83-94; 1176-1183), **MEMORY.md 미기록**. 개별 "Candidate: ... confidence ... status: staged" 항목 생성(860-865).
2. **REM** — `prepareRemDreaming`(1104-1149). **라이트가 stage한 키를 우선 소비**("Prefer entries staged by light sleep so REM synthesises from the sequential light→REM pipeline", 1109-1117). concept tags에서 `Reflections`(주제)를, 멀티데이 반복 신호(recallDays)에서 `Possible Lasting Truths` 후보 진실(confidence 포함)을 구성(`previewRemDreaming`, 946-981). `## REM Sleep` 블록 기록, **MEMORY.md 미기록**.
3. **Deep** — `rankShortTermPromotionCandidates`(short-term-promotion.ts:83-200) 후 `applyShortTermPromotions`(short-term-promotion-apply.ts). `runDreamingSweepPhases`가 light→REM을 먼저 돌리고 REM 실패 시 deep까지 스킵(dreaming-phases.ts:1209-1213). Deep은 `phase: "deep"` 데이터로 저널 요약(dreaming.ts:262), 승격만 MEMORY.md 기록(229).

**Deep의 실제 점수와 게이트**(보고서와의 차이는 §4 참조):

- 가중치 6종: `frequency 0.24, relevance 0.30, diversity 0.15, recency 0.15, consolidation 0.10, conceptual 0.06`(short-term-promotion.ts:30-37) + phase boost(라이트 ≤0.06, REM ≤0.09, 27-28, 58-82). `resolvePromotionThresholds`: `minScore`·`minRecallCount`·`minUniqueQueries`·`maxAgeDays`(93-94).
- 기본값: `minScore 0.75, minRecallCount 3, minUniqueQueries 3, recencyHalfLifeDays 14, maxPromotedSnippetTokens 160, maxPriorEntryLossFraction 0.25, limit 10`(memory-host-sdk/dreaming.ts:34-42, 399-417). 라이트는 `limit 100·lookbackDays 2·dedupeSimilarity 0.9`, REM은 `minPatternStrength 0.75·limit 10·lookbackDays 7` 수준(383-395, 428-436); REM 소스는 `["memory", "daily", "deep"]`(246).
- 상수 인용: 일일 인제스트 되먹임 상수 `DAILY_INGESTION_SCORE 0.62·MAX_SNIPPET_CHARS 280·MIN_SNIPPET_CHARS 8·MAX_CHUNK_LINES 4`(dreaming-phases.ts:78-82), diary 최근 4개 참조(82), phase signal 반감기 14일(29).
- **pre-write 재검증 실재 여부 → 실재**: 후보를 live 일일 파일에서 rehydrate(rehydration 실패/출처 지문 변경 → "source rehydration"/"source changed" 거절, short-term-promotion-apply.ts:328-347), `isContaminatedDreamingSnippet`·`isPromotionOriginBlocked` 검사(275-319), 도달 검증 후 적용(320-368).
- **모델이 add/merge/replace 결정 → 대체로 참, 이름만 다름**: `CONSOLIDATION_SYSTEM_PROMPT`(dreaming-consolidation.ts:23-32) — "Emit exactly one operation per candidate: candidateKey, action (added, merged, or superseded), and priorEntries." + "The host writes each candidate's supplied resultEntry." 괄호 안에 후보당 결과 항목은 모델이 아닌 **호스트**가 조립(55-61, `Source: path#Lx-Ly` 형식 51-53).
- **검증 체크 4종 실재**: ①승격 후 기존 항목 삭제 상한 `maxPriorEntryLossFraction`(0.25) 초과 → 계획 무효(291-293, 462-474) ②후보에 출처 캐리(`Source:` + recall annotation, 60, buildPromotionRecallAnnotations) ③메모리 예산 `memoryFileMaxChars`(옵션, 71-79) + 스니펫 토큰 상한(160) ④모델 응답 구조 파싱·검증(`parseConsolidationPlan` 83-125, `validateConsolidationPlan` 199-274 — 연산 수=후보 수, priorEntries 정확 1회 일치, 병합 심층 관련도, superseded는 lineage 요구, 프로젝트 그룹 미교차).
- **pre-change MEMORY.md 보존 → 실재**: `storeMemoryPreimage`(dreaming-consolidation-artifacts.ts:35-69)가 `dreaming-memory-backups` 네임스페이스(SQLite plugin state)에 최근 8개 보존(`CONSOLIDATION_BACKUP_LIMIT = 8`, 12), sha256 contentHash 포함(44). 문서 136-139 — "Before the file changes, the previous `MEMORY.md` is stored in SQLite-backed plugin state."
- **fallback → 실재**: 모델 부재·파싱 실패·검증 실패 시 append-only 승격("using append-only fallback", dreaming-consolidation.ts:450-451; "Model absence or rejected output preserves the shipped append-only promotion contract", short-term-promotion-apply.ts:549-551), 실패 사유는 DREAMS.md 요약으로 기록(105-113 appendConsolidationSkippedSummary → "Fallback: append-only promotion.").
- **문서가 정의한 위상 계약(정밀 인용)**(dreaming.md:72-102): 순서(dreaming.md:72 "light -> REM -> deep. These are internal implementation phases, not separate user-configured modes"): 라이트(82-86 "Reads recent short-term recall state, daily memory files, and session transcripts when available."·"Never writes to `MEMORY.md`"), REM(90-93 "Builds theme and reflection summaries from recent short-term traces"·"Never writes to `MEMORY.md`"), Deep(97-102 "Ranks candidates with weighted scoring and threshold gates (`minScore`, `minRecallCount`, `minUniqueQueries` must all pass)"·"Rehydrates snippets from live daily files before writing, so stale/deleted snippets are skipped"·"chooses additions, merges, and supersessions"·"Falls back to the previous append-only promotion path when the model is unavailable or the rewrite fails validation").
- **승격 후보의 taint 게이트**: consolidation 프롬프트 전에 `untrusted`/`system` 출처 후보를 구조적으로 제거("structural taint gate, not a score penalty", dreaming.md:121-125).

**「Deep이 정말 승격을 gate하는가」→ 그렇다**: `dreaming-command.ts:35` "deep is the only stage that writes durable entries to MEMORY.md."(문서 dreaming.md:72-78, memory-config.md:942 "Dreaming runs as one scheduled sweep and uses internal light/deep/REM phases as an implementation detail"도 동일). 자동 sweep에서 MEMORY.md를 쓰는 경로는 오직 승격뿐(사용자 개입 명령 없이).

### 주장 5 — 저장소 종류 — ✅ (코드+문서에서 확인)

1. **MEMORY.md(장기)**: `path.join(workspaceDir, "MEMORY.md")`(short-term-promotion-apply.ts:229). Deep만 기록.
2. **DREAMS.md(사람 검토용 다이어리)**: `DREAMS_FILENAMES = ["DREAMS.md", "dreams.md"]`(dreaming-dreams-file.ts:14), "Dream Diary"(같은 파일 35). consolidation 요약·실패 사유도 이 파일의 `## Memory Consolidation History`에 기록(71-90).
3. **SQLite plugin state**: `dreaming-state.ts:53-56` — createPluginRuntimeStore, 오류 문구 "memory-core dreaming SQLite state store is not configured". 네임스페이스 9종(12-20): `dreaming-daily-ingestion`, `dreaming-session-ingestion-files`, `dreaming-session-ingestion-seen`, `session-backfill-rewind`, `dreaming-memory-backups`, `short-term-recall`, `short-term-phase-signals`, `short-term-meta`, `short-term-locks`. workspace당 최대 엔트리 50,000(22), lock 최대 4,096(24). memory-config.md:981 — "Dreaming keeps machine state (recall store, phase signals, ingestion checkpoints, locks) in SQLite-backed plugin state; under `memory/.dreams/` it writes only the derived session corpus."
4. **`memory/.dreams/` corpus**: `memory/.dreams/session-corpus/<날짜>.txt`(테스트 고정 경로 cli.test.ts:897 등; 생성자 session-ingestion.ts:37·494), `memory/.dreams/session-ingestion.json`(체크포인트), `memory/.dreams/events.jsonl`(실패 이벤트 — dreaming-events.ts). daily memory의 오염 보호: `memory/.dreams` 경로는 `isShortTermMemoryPath`/`isShortTermSessionCorpusPath`로 다루어짐(short-term-promotion-utils.ts:119-128).
5. **diary "새 내용 없으면 없음"**: `deepHasContent` 게이트(dreaming.ts:276-285: `repair.changed || applied.applied > 0 || hasReportableRejections`), `writeDreamingReport({ hasContent })`(dreaming-markdown.ts:96-127 — hasContent 없으면 "nothing new" 반환 경로). 문서 memory-config.md:982 — "Dreaming combines Light, REM, and promoted Deep memories into at most one diary entry per workspace per sweep in `DREAMS.md` ... sweeps without new material produce no diary entry.", 테스트 dreaming.test.ts:930 — "does not create memory/ or DREAMS.md on an empty workspace sweep". dreaming.md:61-62 — "An empty sweep records completion in plugin state without creating memory or dreaming files".
- 파일 권한·출처 분류: DREAMS.md는 workspace 파일로 취급되어 0o640 모드 검증(workspace-file-mode.test.ts:145), 경로 분류에서 `DREAMS.md`/`memory/dreaming/`/`memory/.dreams/`는 system 산출물로 인정(memory/memory-path-provenance.ts:62-63).
- 저장 관련 보조 결정: 관리 일일 파일(`memory/<ISO>.md`)도 위상 블록 대상(dreaming-markdown.ts:27, 83-94), `memory/dreaming/<phase>/<YYYY-MM-DD>.md` 분리 보고서는 storage mode에 따라(`inline|separate|both`, memory-host-sdk/dreaming.ts:43-66) 선택 — 보고서의 "4종 저장소" 분류와 충돌 없음.

### §1 종합 관찰

- 가능한 검증 결론의 요지: **"3-stage + Deep 단독 기록 + 검증/폴백 계단"이라는 pasted report의 골격은 클론 코드·문서와 일치**하고 실질 오류는 명칭·구성요소 수준(§4). 이는 "문서 레벨 조사"로 보기엔 우수한 정확도.
- 주의점 1: 보고서가 "REM 정리는 주제/반복 신호"로만 서술 — 실제로 REM은 라이트 stage 키 우선(자체 수집 없음)이며 Deep은 REM 다음에만 실행(의존 순서) — 위상 병렬성 오해 가능.
- 주의점 2: "기본 활성화"는 사용자 동의 없이 자정 배치 모델 호출이 돌 수 있음을 의미 — 다중 업체 작업장 정책 입안 시 반드시 고려(§5 항목 11·15).

### 주장 6 — 기본값과 명령어 — ✅ (문서+코드에서 확인, 표현 보정 1건)

- **기본 활성화**: `parseBoolean(dreaming?.enabled) ?? true`(memory-host-sdk/dreaming.ts:375), dreaming.md:14 "Dreaming is enabled by default", memory-config.md:950 `enabled | boolean | true`.
- **기본 주기**: `DEFAULT_MEMORY_DREAMING_FREQUENCY = "0 3 * * *"`(memory-host-sdk/dreaming.ts:22), 사용 예 dreaming.md:260-261, memory-config.md:951.
- **명령어 전수 확인**:
  - `/dreaming status`(dreaming-command.ts:59-61; usage 28-37). 부가로 `/dreaming on|off`는 owner/`operator.admin` 필요(63-68), `help|options|phases`(55-57). 문서 dreaming.md:306-311.
  - `openclaw memory status --deep`(dreaming.md:323; 동일 옵션이 status에서 "Check embedding provider availability"로도 정의 — cli.ts:158-159 — §4 항목 3 참조; 보조 힌트 tools.shared.ts:89).
  - `openclaw memory promote`(cli.ts:237-257), `promote --apply`(254 "Append selected candidates to MEMORY.md"), `--min-score/--min-recall-count/--min-unique-queries/--limit/--include-promoted/--json`(238-256). 문서 워크플로 dreaming.md:319-324.
  - `openclaw memory promote-explain "<선택자>"`(cli.ts:260-275, `--agent/--include-promoted/--json`), 문서 333-334.
  - `openclaw memory rem-harness`(cli.ts:277-285, "Preview REM reflections, candidate truths, and deep promotions **without writing**", `--path/--grounded/--include-promoted/--json`) — "write-free preview" 확인.
  - 같은 패밀리: `rem-backfill`(287-304), `session-backfill --rem --apply --rollback`(306-329), `memory forget`(215-235).
- **관찰(일반론)**: 보고서의 명령어 목록은 전부 실재하며 과장 없음. 다만 `/dreaming ...`(슬래시)와 `openclaw memory ...`(CLI)가 서로 다른 표면(dreaming.md:409 — "Memory CLI status / promote / REM helpers still follow the built-in `memory-core` config path")이라는 구분은 보고서에 없음.
- **명령어↔구현 위치 대조(요약)**: `/dreaming status`(dreaming-command.ts:59) · `memory promote`(cli.ts:237) · `memory promote-explain`(cli.ts:260) · `memory rem-harness`(cli.ts:277) · `memory status --deep`(cli.ts:158-159) · `memory rem-backfill`/`session-backfill`(cli.ts:287/306) — 모두 클론에서 확인.

### 주장 7 — 시크릿 미마스킹과 모델 사용 — ✅ (문서에 적혀 있음)

- **미마스킹**: `dreaming.md:109` — "Transcript text is not secret-masked before ingestion."(같은 문단에 "Only interactive sessions are eligible. Cron, heartbeat, subagent, and unknown sessions stay out of durable candidate ingestion."·"Runtime-marked recalled context is removed so recalled snippets cannot be learned again as new memory."). 즉 **수집 단계에서는 마스킹 없음** — 보고서 서술 정확. 오염 스니펫 검사(`isContaminatedDreamingSnippet`)는 승격·diary 단계에서 걸러지는 별개 장치(short-term-promotion-utils.ts:226).
- **모델 사용**: `dreaming.md:147` — "The tool-free background completion uses the workspace agent's default model unless `dreaming.model` is configured. If the configured model is unavailable, the diary run retries once with that agent's default model. Trust or allowlist failures are not retried." → "Dream Diary가 기본/설정 모델을 쓸 수 있고(데이터가 외부 모델로 나갈 수 있고)" 서술 정확.
- **실패의 가시성**: "Failed or empty diary generation writes a local fallback entry and reports a degraded outcome, so missing model output leaves a visible trace."(dreaming.md:153-154) — 모델 부재가 조용히 넘어가지 않음.
- **경계를 실제로 넘는 데이터**: ①저널 완료 프롬프트 — light/REM 스니펫·테마·deep 승격 요약·sourceEntryKeys(dreaming.ts:261-272) ②consolidation 완료 프롬프트 — 현재 MEMORY.md 원문 + 후보 스니펫·sourceRef·provenance·projectKey·supersedesKey(dreaming-consolidation.ts:63-81). 이 두 경로가 기본/or 설정 모델(=원격 공급자일 수 있음)로 나간다.
- **대비 포인트(신규 관찰)**: Hermes core와 hermes-dreaming은 정반대로 "전송 직전 scrub"를 넣음(§2-11, §3) — 작업장 관점의 시사점은 §5-4·§5-10.

---

## §2 Hermes 점별 검증 (주장 8-11, 13)

### 주장 8 — `agent/memory_manager.py`·`agent/memory_provider.py` — ✅ (코드에서 확인)

- `agent/memory_manager.py`(935줄) — "MemoryManager — fans the agent's memory hooks out to registered providers."(1-5). 내장 provider는 항상 허용, 외부 플러그인 provider는 **동시 1개만** 등록(3-4: "only ONE external plugin provider may be registered at a time").
- `agent/memory_provider.py` — `class MemoryProvider(ABC)`(84), "Abstract base class for memory providers"(85), v1(비동기 best-effort)/v2(결함-폐쇄) pre-compress checkpoint 구분(35, 105-106). 매니저 쪽 `_checkpoint_api_version`(683-689)이 버전 협상.
- 보고서의 "session-boundary 추출/전환"과 "provider 인터페이스" 역할 구분은 정확: 추출·전환 오케스트레이션은 manager(memory_manager.py:629-681), 계약은 provider ABC. 주변 모듈 `agent/memory_caller_scope.py`(호출자 스코프·도구 거부 메시지)·`agent/memory_context.py`(컨텍스트 블록·sanitize)·`agent/memory_manager_tools.py`(훅 기반 도구 표면)도 존재 — 주장 8의 두 파일 외 메모리 패밀리가 클론에 온전히 있음.

### 주장 9 — 문서 2건 + 훅 4개 — ✅ (문서+코드에서 확인)

- `website/docs/user-guide/features/memory.md`(555줄) — 파일(13-20), frozen snapshot(38-57), "Memory Needs Session Boundaries"(59-61), 외부 provider 소개(544-555).
- `website/docs/developer-guide/memory-provider-plugin.md`(562줄) — 발견 경로 4종(17-24), ABC 소개(87-114), 초기화 컨텍스트(116-132), 계약 규칙.
- **훅 4개 실재**(memory_provider.py): `sync_turn`(151, "Per-turn synchronization"; hermes 측 호출부 memory_manager.py:444-449 "Never inline: a provider's `sync_turn` may block for minutes"), `on_session_end`(183-184 — "End-of-session extraction; fires only at real session boundaries, never per-turn"), `on_pre_compress`(194, "Callback for the context compression phase"; v2는 checkpoint API, manager 쪽 `_checkpoint_api_version` 683-689), `on_memory_write`(212 — "from ... memory tool actions"). 부가 훅: `on_turn_start`(172), `on_session_switch`(186 — `/resume /branch /reset /new`, `rewound`는 `/undo`), `on_delegation`(198).
- **provider egress scrub(repeated 사용)**: 모든 fan-out(sync_turn·session-end·pre-compress 전사·memory-tool 미러·delegation 결과·provider 도구 인자)이 `_redact_for_provider`를 통과(memory_manager.py:99-108 "Providers archive whatever they are handed (#115104)") — 주장 7의 OpenClaw 미마스킹과 정면 대비(§4 항목 7).

### 주장 10 — 세션 종료 추출+새 세션 전환 단일 직렬화 task — ✅ (코드에서 확인)

- `memory_manager.py:636-667` — `commit_session_boundary_async`: 주석(638-651) — "Queue old-session extraction + provider rebinding as ONE serialized task." / "`on_session_end` (LLM-bound, seconds) must run strictly BEFORE `on_session_switch` rebinds provider state; an ad-hoc thread raced the inline switch and misattributed transcripts." / "Submitting BOTH hooks as one task on the manager's single background worker gives both properties at a single chokepoint ... the worker's FIFO order serializes end→switch against every other provider write (per-turn `sync_all`, prefetches)"(647-650). 실행부는 `_run()`에서 end→switch 순차 호출(657-665), 스레드풀 부재 시 inline 폴백(650-651).
- 즉 "세션 경계 추출+전환을 하나의 직렬화된 task로 처리해(old 추출이 새 세션에 잘못 귀속되는 레이스 방지)" — 서술 정확. `agent/AGENTS.md:130` — "End-of-session memory extraction and provider `on_session_end` run wherever the session ends — turn, eviction, shutdown, `tui_gateway` teardown".
- 사용 위치: `/new`·`/resume`·`/branch`·`/reset`·일반 회전 등 경계 전환 지점에서 `commit_session_boundary_async`/`on_session_switch` 호출(669-681 주석 "AIAgent.session_id rotated without teardown"; `rewound`는 `/undo`). 컴프레션은 세션 경계가 아니므로 별도 `on_pre_compress` 훅으로 분리 — 경계 추출과 컴프레션이 섞이지 않는 구조(194).

### 주장 11 — `memory.write_approval` 게이트 — ✅ (문서+코드+테스트에서 확인)

- 설정 자리: `memory.write_approval`(config_defaults.py:1303 `False` 기본, 1467), 슬래시 `/memory approval on|off`로 전환(둘 다 `_save_write_approval("memory", ...)` 1294-1296; gateway slash_commands.py:250-263). 같은 메커니즘이 `skills.write_approval`과 공유(테스트 e2e/security/test_store_traversal.py:75).
- 의미: memory.md:333-339 — "Require approval before anything is saved to MEMORY.md or USER.md" / "all write attempts are staged for review with `/memory pending`" / `write_approval: false` 기본 = "write freely"(338). 자동(백그라운드 리뷰) 제안은 pending 목록에서 `[auto]` 태그, 수동 요청은 즉시(346-347: "Items the agent wants ... shows `[auto]`").
- 명령 실재: `/memory pending`(목록), `/memory approve <id>`·`/memory approve all`, `/memory reject <id>`·`/memory reject all`(348-349). 구현: `hermes_cli/write_approval_commands.py::handle_pending_subcommand`(gateway slash_commands.py:914-921; TUI도 동일 핸들러 위임 — tui_gateway/methods_tools.py:924-992). `tools/memory_tool.py:212` — "review it with /memory pending (approve to apply, discard to drop)". 게이트 없이 재생(승인)하는 경로의 주석(354) — "Replay a staged write against the store, bypassing the gate (/memory approve)".
- 표면 전체: `/memory`는 pending/approve/reject 외 `approval on|off`·`mode` 서브커맨드도 가짐(gateway slash_commands.py:914-921, 926-936 — 게이트 꺼져 있어도 저장된 staged write가 있으면 응답), `/skills approval`과 같은 게이트 코드 공유(memory.md:541 "Gating agent skill writes ... `skills.write_approval`").
- 제한: builtin MEMORY.md 2,200자·USER.md 1,375자(성 기준, 17-18) — 작성 제한은 곧 승격/패치의 암묵적 예산이 되고, 초과 시 `memory` tool이 오류 반환·agent가 같은 턴에 정리 후 재시도(27-33).
- 경쟁 처리: 승인 시점에 원문이 달라졌으면 거절되어 pending 유지(memory.md:356-362); 테스트 `tests/gateway/test_session_memory_review.py:137-139` — 'Approved 1 memory write', "Rejected pending memory write 'dropme02'".

### 주장 13 — issue 25309 — ✅ (GitHub API로 확인) — 상세

- `https://api.github.com/repos/NousResearch/hermes-agent/issues/25309`(web_fetch HTTP 200): `"state":"open"`, `"closed_at":null`, `created_at 2026-05-14`, `updated_at 2026-10-16`, label `type/feature`·`comp/cron`·`comp/plugins`·`tool/memory`·`P3`, 댓글 10. 제목: "🌙 feat: Dreaming — Automatic Background Memory Consolidation".
- 제안 내용(본문): `plugins/dreaming/` 3-phase(라이트: 스캔·dedupe·stage / REM: 테마·DREAMS.md / Deep: 스코어링·MEMORY.md), "Opt-in, disabled by default", cron 기본 `0 3 * * *`, `hermes dream run|status|diary` CLI — OpenClaw 드리밍을 명시적 모티브로 인용. 상태 판정 근거: `"state":"open"` + `closed_at:null`(API 원문), label `P3`(Low), `author_association: NONE` — "open"은 미병합·미채택의 의미일 뿐.
- **내장 3-stage 없음**: hermes 클론 grep("dreaming") 결과는 `plugin-catalog/hermes-dreaming.yaml`(플러그인 카탈로그)과 `optional-skills/health/neuroskill-bci/references/metrics.md:190`(수면 생리 표의 "dreaming")뿐 — main 트리에 내장 드리밍 파이프라인 구현 없음. 보고서의 "the report did NOT find a built-in 3-stage dreaming pipeline in hermes main" — 정확.

---

## §3 hermes-dreaming 플러그인 (주장 12) — ✅ (클론 카탈로그 + GitHub README로 확인)

- **리포지토리 존재**(web_fetch `https://github.com/itpartypattaya/hermes-dreaming` HTTP 200; README는 핀 sha `b89b733433641ba68cc5d90fe0ca977deeb61bfe`의 raw 파일로 확정): "Nightly, deterministic **memory consolidation ("dreaming")** for Hermes Agent, in the spirit of OpenClaw dreaming."
- **카탈로그**: hermes 클론의 카탈로그 파일은 `website/plugins.json`이 **아니라** `plugin-catalog/hermes-dreaming.yaml`(`website/plugins.json`은 클론 전체에 없음). YAML: `name: hermes-dreaming`, `repo: https://github.com/itpartypattaya/hermes-dreaming`, `sha: b89b73...`, `version: "3.8.3"`, `tier: community`, `maintainer: itpartypattaya`, `category: memory`, `requires_hermes: ">=0.21.0"`, `capabilities: provides_tools: [] / provides_hooks: [] / provides_middleware: [] / requires_env: []`(18-22).
- **규칙 기반 후보 검사**: "Exact, declarative alias rules and stemmed containment in three directions; a candidate that repeats an entry with *different numbers* is a possible update (`conflicts`)"(README "Matching against memory"). "The rules call no LLM and the script never writes memory." 상관 확인은 "humans mention it on distinct days" 기준(크론 프롬프트·컴팩션 배너·URL·불신 채팅·자기 스레드는 제외).
- **에이전트 검토 + 표준 memory tool**: "wakes the agent only when there is something to decide." / "the agent applies its proposals through the regular `memory` tool and reports in a few lines." 일감 게이트: "On a quiet night the cron job prints `{"wakeAgent": false}` and the model never starts." 변경 상한: "A night is capped at 6 memory changes"·"`replace` is used only with a verbatim anchor".
- **별도 저널**: README — "A diary (`memories/DREAMS.md`) with provenance for every candidate."(설정 `diary.heading`·`diary.path`).
- **선택 cloud judge**: "An optional cloud judge, off by default, can second-guess the rules' mention count"(README 도입부) — "off by default; turned off, its code is not even loaded". 켜면 "only the fact and the person's own words" 쌍을 외부(README 설정 항목 기준)로 전송, "no ids, senders or times"·"screened for secrets and instructions first", `send_from`/`send_sources`로 제한, `shadow`(무영향)·`veto` 모드.
- **시크릿(보고서에 없는 보강)**: 이 플러그인은 OpenClaw와 반대로 **수집 전 스크리닝** — "Secrets and prompt-injection are quarantined"(README "Screening"; 자체 규칙 + holographic `holo_screen.py` 복제, "applied to stored facts and human messages before anything reaches memory, the report or the model"), 단 정규식 필터로 "단, 완벽은 아님" 인정.

### §2 종합 관찰

- hermes core와 hermes-dreaming이 "전송 직전 scrub"/"수집 전 스크리닝"을 갖춘 반면 openclaw dreaming 수집은 미마스킹 — 드리밍이라 불리는 기능의 **시크릿 기본값이 저장소별로 갈리는** 것이 R6가 작업장에 주는 핵심 발견(§5 항목 10).
- hermes의 세션 경계(추출·전환 직렬화)와 write_approval 게이트는 "사람이 통제하는 자동 메모리"라는 점에서 TalkAbout "사람 승인" 원칙과 같은 철학 — 코드로 구현된 참조 구현이 이미 존재.
- 심화 포인트: `/memory approve all`이 가능하나 "batch operations are not recommended"(memory.md:349의 문맥 없음 — 미인용 범위) — 개별 승인을 기본 UX로 설계 권장(문서의 권장 태조를 따르면).
- **판정**: 주장 12의 5개 구성 요소(규칙기반 후보·에이전트 검토·표준 memory tool·별도 저널·선택 cloud judge) 모두 진본. "in the hermes plugin catalog"도 `plugin-catalog/`(설치 명령 `hermes plugins install <name>`이 소비하는 것)로 확인.
- **설치 형태·경계**: "Portable Agent Plugins v1 package (plugin.json + skills/); no tools, hooks, middleware or env vars."(YAML 4행). 기본(judge off) 실행은 네트워크/LLM 호출 없음, SQLite는 `mode=ro`, cron이 agent를 깨우는 패턴 — 순수 스크립트(에이전트 훅/도구 아님).
- **README의 추가 사실(디자인 노트)**: "Corroboration must come from **humans**; machine text never counts." / "Text, not ids. SQLite reuses ids; state is keyed by a text fingerprint" / "**Zero writes is a success.** A night is capped at 6 memory changes" / 차용 출처 명시: "Borrowed ideas: OpenClaw (phases, weights, gates, loss fraction, explain), mem0 (nearest entry → update instead of append), Graphiti (supersession as a flag)".
- **실용 상한(README)**: nightly 상한 — 신규 사실 다이어리 3개, 메모리 변경 6개, `replace` 6개 미만(설정 분화 포함) — "replace is used only with a verbatim anchor the script hands over"로 잘못된 부분 교체 방지.

---

## §4 정정·차이 목록 (pasted report가 틀렸거나 부정확했거나 과장한 부분)

1. **「add/merge/replace」→ 실제 action 이름은 `added | merged | superseded`**(dreaming-consolidation.ts:26, 39-45). "replace"는 action이 아니라 나머지 부가 개념(`superseded`가 lineage로 기존 항목 대체). 또한 **모델은 memory 텍스트를 작성하지 않으며** 호스트가 `resultEntry`를 조립(23-32) — 보고서의 "model decides add/merge/replace"는 압축이라 틀리진 않되 표현이 어긋남.
2. **Deep 점수 서술이 불완전**(4개만 열거) — 실제 6개 가중 신호 + phase boost(§1-4 목록)이며, "number of distinct queries"는 스코어의 diversity 항목(0.15)이자 **게이트** `minUniqueQueries=3`. `maxAgeDays=30`, `limit=10`, 스니펫 토큰 상한 160, 손실 상한 0.25도 생략.
3. **`openclaw memory status --deep`의 이중 의미**: dreaming 문서의 승격 워크플로(cli.ts:102, dreaming.md:323)에 쓰이지만, status 명령 자체에서 `--deep`은 "Check embedding provider availability"(cli.ts:158-159) — 드리밍 전용 진단이 아니라는 점을 명시하지 않음.
4. **REM의 입력 의존성 미기재**: REM은 라이트가 stage한 키를 우선 사용(dreaming-phases.ts:1109-1117) — "REM 단독 수집"처럼 읽힐 수 있는 서술은 부정확.
5. **"no new diary entry"·저장 4종은 추가 확인 없이도 정확**(§1-5).
6. **명령어 목록에 `/dreaming on|off`(owner/`operator.admin` 필요, dreaming-command.ts:63-68)·`rem-backfill`·`session-backfill --apply` 누락** — 잘못이라기보다 미언급.
7. **경계 정책 대비 누락**: OpenClaw는 수집 미마스킹(dreaming.md:109), Hermes core는 provider 송출 scrub(memory_manager.py:99-108), hermes-dreaming은 수집 전 스크리닝 — 세 저장소의 시크릿 기본 자세가 다르다는 사실이 보고서에 없음(작업장 설계에 가장 중요한 차이).
8. **카탈로그 경로**: 보고서가 "website/plugins.json or similar"이라 한 것 자체는 예비적 표현으로 무해하나, 실제 카탈로그 파일은 `plugin-catalog/hermes-dreaming.yaml`(26줄, `tier: community`)임을 확정.
9. **issue 25309의 "open" 판정은 정확**, 또한 본문 제안(opt-in etc.)은 제안이지 구현이 아님 — "내장 부재" 판정과 모순 없음.
10. **보고서에 없는 우회 사실**: openclaw dreaming은 `dreaming.recencyHalfLifeDays`(기본 14일) 등 "반감기" 튜닝이 있고, REM의 최소 패턴 강도(0.75)가 REM 게이트로 따로 있음 — 보고서의 "3-stage" 설명에 위상별 기본 임계가 전혀 빠져 있어 운영 판단에는 부족한 요약.
11. **보고서의 라이트 소스 서술("recent short-term search results, daily memory, session logs")은 정확**(dreaming.md:82 문장과 일치) — 정정 불필요(§1-4에 그대로 확인).

### 용어 대조(주장 표기 ↔ 실제 구현 표기)

| pasted report | 실제 (파일:줄) |
|---|---|
| add / merge / replace | `added / merged / superseded`(dreaming-consolidation.ts:26) |
| 3-stage dreaming pipeline | phases: `light → REM → deep`(dreaming.md:72) · CLI 표기 "light/REM/deep"(dreaming-command.ts:34) |
| SQLite plugin state | `memory-core:dreaming-state` plugin runtime store(dreaming-state.ts:53-56) |
| DREAMS.md | `DREAMS_FILENAMES = ["DREAMS.md", "dreams.md"]`(dreaming-dreams-file.ts:14) · hermes-dreaming은 `memories/DREAMS.md`(README) |
| write approval | `memory.write_approval`(config_defaults.py:1303) — memory/skills 공용 게이트 |
| pending 목록 | hermes pending 저장소 · openclaw는 "staged 키"(short-term-promotion `readLightStagedKeys`) |

---

## §5 개선점 도출 (대상: 다중 업체 AI 에이전트 토론+코딩+산출물 작업장)

> 형식: [근거 파일:줄 → 좋은 점 → 적용안] — 태그 [즉시]/[단기]/[중기]/[제외].

1. [openclaw dreaming.ts:144-227; dreaming-phases.ts:1209-1213 → 방 대화 로그 → **검증된 후보 승격 + 단계화**] "deep is the only stage that writes durable entries"(dreaming-command.ts:35)처럼 "모든 후보는 단계를 통과해야 저장" 구조. **적용안**: 방 대화 로그 → ①후보 수집(반복 횟수·출처 메시지 id/seq) → ②사람 승인 목록 → ③승인분만 ROOM_MEMORY 반영. 각 항목에 출처(`message id/seq`)와 상태(staged/승인/거절)를 붙이고, 미승인 후보 자동 주입 금지 원칙(ROOM_MEMORY.md:45) 유지. **[단기]**
2. [openclaw memory-config.md:982; dreaming.test.ts:930 → **사람 검토 저널 + 쓰기 억제**] "sweeps without new material produce no diary entry". **적용안**: 방 메모리 검토 저널(후보→승인/거절)을 "신규 내용 있을 때만" 한 줄씩 추가하는 규칙을 문서화하고, 방 이벤트에 후보 본문을 싣지 않음(ROOM_MEMORY.md:40) 유지. **[즉시]**
3. [hermes memory.md:333-362; tools/memory_tool.py:212 → **write approval 게이트**] 자동(백그라운드) 저장은 pending `[auto]`로만, 사람 승인 시에만 반영·거절·경쟁 시 "refused and stays pending". **적용안**: worker 자동 메모리 제안 기본 게이트 켬, 수동 요청만 즉시, 승인 시 원문 재검증(변경됐으면 거절)을 ROOM_MEMORY 승인 API 설계(ROOM_MEMORY.md:24-42)에 반영. **[즉시]**
4. [hermes memory_manager.py:99-108(`_redact_for_provider`) → **시크릿 필터 + 경계 위치**] "Providers archive whatever they are handed", "Copies of `values` with every string scrubbed before it reaches a memory provider", 결함-폐쇄(`redact_for_egress` — `_scrub`, 89-93). **적용안**: 방 대화 로그가 외부 provider/모델로 나가는 유일 경로를 정해 전송 직전 scrub+실패시 거부, COLLABORATION_CONTEXT의 `validate_manifest`(입력 매니페스트 서버 재검증, COLLABORATION_CONTEXT.md:17)와 같은 지점에 결합. **[즉시]**
5. [openclaw short-term-promotion.ts:126-129(`minUniqueQueries`) → **복수 출처 규칙**] 고유 쿼리 3개 미만이면 승격 불가. **적용안**: 방 후보의 "서로 다른 사람/다른 회차 반복" 가중(미리보기 반복 그룹 정보 — ROOM_MEMORY.md:17-18 재사용 가능), human 메시지 한정(ROOM_MEMORY.md:15)과 정합. **[단기]**
6. [openclaw dreaming-consolidation.ts:83-274, short-term-promotion-apply.ts:328-347 → **검증 계단(pre-image·손실 상한·구조 파싱)**] 실패 시 append-only 폴백·0.25 손실 상한·8개 pre-image 백업. **적용안**: 방 메모리 통합/재작성 기능 설계에 ①구조 검증(파싱 실패=변경 없음) ②원본 보존(철회 지원: 철회 멱등키+트랜잭션 — ROOM_MEMORY.md:34 방향과 정합) ③변경 개수·문자 상한을 명세. **[중기]**
7. [hermes memory_manager.py:636-667 → **경계 직렬화**] "ONE serialized task"·"FIFO order serializes end→switch". **적용안**: 방 전환(room leave → 새 room 바인딩·handoff 요약)을 단일 워커 큐로 묶어 "이전 방 메모리가 새 방에 귀속" 경쟁 제거 — ROOM_MEMORY.md:49의 "검증된 새 세션 생성·종료" 요구와 결합. **[단기]**
8. [hermes-dreaming README "wake gate `{"wakeAgent": false}`" → **일감 게이트(비용 절감)**] 일감 없으면 모델 미기동, 프로그래매틱 무동작이 아님(prints + cron log). **적용안**: 주기 방 검토·요약 작업은 "할 일 존재 시"에만 기동, 생성 불가 사유는 1회 경고(openclaw dreaming.ts:443-448과 동일 철학). **[단기]**
9. [hermes-dreaming README "the regular `memory` tool" → **표준 경로 재사용**] 플러그인 자체는 쓰기 안 함(스크립트 무기록). **적용안**: 방 메모리 저장도 전용 비밀 API 대신 기존 승인·저장 경로(ROOM_MEMORY.md §2)만 사용 — 공개 API 확장·중복 권한 최소화. **[중기]**
10. [openclaw dreaming.md:109 vs hermes memory_manager.py:99-108 vs hermes-dreaming "Screening" → **정책 대립의 명문화(provider 예산·입력 경계 매니페스트와 충돌 방지)**] 저장소 3곳의 시크릿 기본값이 정반대. **적용안**: 작업장 매니페스트에 "마스킹/스크리닝 소유 계층"을 확정(수집 시점 vs 전송 직전), 전송 경계 단일화, 미스크리닝 데이터는 경계를 못 넘게 fail-closed, `project_manifest`/`redacted_source_count`(COLLABORATION_CONTEXT.md:18) 같은 집계만 외부 노출. **[즉시]**
11. [openclaw 기본 활성화(`?? true`) vs hermes-dreaming "off by default" → **기본값 철학**] **적용안**: TalkAbout은 후보 수집만 자동(기본 켬)·저장/주입은 승인 필요(기본 꺼짐)로 고정. OpenClaw식 자동 3-stage 모델 통합(모델이 merge/supersede 결정) 이식은 승인 원칙과 충돌 → **[제외]**. (hermes-dreaming은 "deterministic rules"만으로 후보를 좁혀 LLM 호출을 최소화 — 이 자세만 차용.)
12. [hermes-dreaming judge 최소 전송("only the fact and the person's own words ... No ids, senders or times") → **외부 전송 최소 필드**] **적용안**: 외부 요약/판정 시 본문 발췌+출처 참조만, sender/time/id 제외, 전송 대상 명시 목록, `shadow` 모드(무영향) 우선 도입. **[중기]**
13. [hermes-dreaming "humans ... on distinct days" 상관 → **신뢰 출처 규칙**] 사람의 서로 다른 날/채팅 발언만 상관 증거. **적용안**: room 로그 기반 후보에서 에이전트·도구 출력·첨부 인용을 상관에서 제외(규칙 명세화) — 우선순위 선택·`input_manifest`(COLLABORATION_CONTEXT.md:14)에 이용. **[단기]**
14. [openclaw dreaming-command.ts:63-68(owner/operator.admin) → **권한 분리**] **적용안**: 방 메모리 승인/거절/주입 전환은 사람·`room:control`(ROOM_MEMORY.md:11) 보유자만, worker는 제안만. **[즉시]**
15. [openclaw memory-config.md:985(`dreaming.model`은 `subagent.allowModelOverride: true` 필요) → **모델 지정 제한**] **적용안**: 모델 지정 가능한 메모리 경로는 별도 동의 게이트와 예산 상한에만 연결(무제한 model override 금지). **[중기]**
16. [openclaw dreaming-state.ts:12-20(플러그인 state 네임스페이스 9종) → **상태 별도 저장**] 기계 상태(리콜·시그널·잠금)를 사용자 파일(원문)과 분리. **적용안**: 방 메모리 원문·승인본은 저장소, 검토 메타(후보·pending·저널)는 별도 네임스페이스 — 철회·삭제·내보내기 경계(ROOM_MEMORY.md:55) 설계 기준으로 활용. **[중기]**
17. [openclaw dreaming.test.ts:930 "does not create memory/ or DREAMS.md on an empty workspace sweep" → **무작업도 "완료"로 기록**] 빈 sweep는 파일을 만들지 않고 완료 마킹만. **적용안**: 방 검토 작업도 "검토 완료(변경 없음)"를 별도 저널로 남기지 않되 상태에는 기록 — "무작업=실패" 오해 방지 (hermes-dreaming "Zero writes is a success"와 동일 철학, §3). **[즉시]**
18. [hermes memory.md:61-66(채팅 1개=세션 1개) → **방=세션 경계 확정**] "where a chat is deliberately one session" — 메모리 기반이 세션 경계. **적용안**: TalkAbout에서 방=메모리 스코프 단위를 명시하고, 방 전환 시에만 세션 경계 추출(주장 10의 직렬화)이 돌도록 설계 — 방 이벤트 기준이 아닌 "방 유지 시간" 기준. **[단기]**
19. [hermes-dreaming known_issues "Cron maintenance requires a single configuration writer" → **설정 단일 작성자 계약**] 자동 작업과 수동 편집의 원자적 동시 거절 불가 인정(plugin-catalog/hermes-dreaming.yaml:26). **적용안**: 방 자동 검토 작업 정의를 단일 작성자(설정 파일 소유자)에 한정하고 경합 시 충돌 설명을 로그 — TalkAbout 방 권한(ROOM_MEMORY.md:11)과 결합. **[중기]**
20. [openclaw dreaming-cron.ts:100-103(관리 cron 설명에 게이트 수치 내장) → **자동 작업에 의도 명시**] "Promote weighted short-term recalls into MEMORY.md (limit=10, minScore=0.750, ...)". **적용안**: 방 자동 제안 작업 이름·설명에 게이트/예산 수치를 내보여 설정과 실행이 어긋나면 Doctor/점검에서 잡히게 함. **[단기]**
21. [openclaw short-term-promotion-apply.ts:71-79(memoryFileMaxChars 부트스트랩 예산) → **주입 예산 유지**] 승격 후 MEMORY.md가 커지면 오래된 자동 섹션을 손실 상한 안에서 압축("so the file stays bounded and bootstrap injection keeps reaching new sessions"). **적용안**: 작업장에서 방 요약·컨텍스트 주입본에도 "최대 크기"를 정하고 초과 시 우선순위 하위 항목을 별도 상태 저장소로 이동(사람 승인 유지). **[중기]**
22. [hermes memory.md:57(frozen snapshot) → **주입 스냅샷 고정**] "captured once at session start and never changes mid-session" · 캐시 보호. **적용안**: ROOM_MEMORY 주입본도 방 세션 시작 시 스냅샷 고정(중간 변경은 도구 응답에만 노출) — 다중 업체 worker가 각자 다른 메모리 스냅샷을 보는 순서 불일치 방지. **[단기]**
23. [openclaw dreaming.md:33-37("Human-readable output in `DREAMS.md`"·"assistant prose") → **사람 읽기 우선 산출물**] 기계 상태와 사람 산출물을 파일류로 분리 — 사람 검토 경로가 1등. **적용안**: 방 메모리 산출물(승인 저널·요약)과 내부 상태(벡터/점수)를 파일과 상태 저장소로 분리해 "사람이 보는 것"과 "기계가 쓰는 것"을 구분(ROOM_MEMORY.md:55와 정합). **[중기]**
24. [hermes-dreaming "Only a memory file close to its char limit counts as work (`memory_pressure`)" → **부담 지표를 일감 신호로**] 용량 임박을 "검토 필요" 신호로 승격. **적용안**: 방 메모리/요약산출물 용량 임계를 자동 검토 트리거 후보에 포함(단, 자동 변경은 금지), 사람 승인 목록에만 노출. **[단기]**

---

## §6 TalkAbout 방 메모리와의 대조 (겹침·신규·신규 내용)

**이미 있는 것(TalkAbout 문서 기준)**:

- `docs/ROOM_MEMORY.md` — 사람이 누른 미리보기만 구현("로컬 개발 중: 1단계 수동 미리보기만 구현 · 2~4단계 설계/미완료", 3). human 메시지 한정(15), 반복 그룹+출처 표시(17-18), **민감정보 탐지·마스킹 없음 명시**(19), 승인 전 트랜잭션 재검증 설계(38-40), 승인 없이 자동 승인/주입 금지(26, 45), 자동화는 허용 범위·중지·비용·감사 선행(53).
- `docs/COLLABORATION_CONTEXT.md` — 공유 입력 컨텍스트: 우선순위 선택(pinned/handoff → 최신 4건 → 조상 추적), `input_manifest` 생성(14), `validate_manifest` 서버 재검증 → `worker_reported_server_validated_refs_not_model_read_receipt`(17), 구형 worker는 거절 대신 `legacy_input_unverified` 기록(73) — "매니페스트 검증"은 이미 존재.
- `docs/SESSION_LOG.md`·`docs/CODEX_SESSION_CONTINUITY.md`·`docs/MODE_HANDOVER.md` — 방/세션 핸드오프·체크포인트 연속성.

**겹치는 아이디어(방향 동일 — 중복 도입 불필요)**:

| 아이디어 | 양쪽 위치 | 결론 |
|---|---|---|
| 사람 승인 전 저장 금지 | hermes write_approval ↔ TalkAbout §3 "미승인 후보 자동 주입 금지" | 겹침 — 공통 원칙 |
| 후보에 출처·반복 횟수 첨부 | OpenClaw `Source: path#Lx-Ly` ↔ TalkAbout 미리보기 출처(원문 id·seq·작성자·반복) | 겹침 — 이미 구현/설계 |
| "일감 있을 때만" 게이트 | hermes-dreaming wake gate ↔ TalkAbout 수동 미리보기 | 겹침(수동 버전) |
| 기본 거부·fail-closed | hermes scrub · OpenClaw 승격-실패 폴백 ↔ COLLABORATION_CONTEXT `validate_manifest` 422 | 겹침 — 경계 설계 공유 |
| 승격은 마지막 단계만 기록 | OpenClaw "deep is the only stage that writes durable entries" ↔ TalkAbout 후보≠메모리(ROOM_MEMORY.md:5 "후보는 메모리가 아니다") | 겹침 — 후보/메모리 구분 공유 |
| 사람 발언만 후보 소스 | hermes-dreaming "humans on distinct days" ↔ TalkAbout human 메시지 한정(15) | 겹침 — 이미 구현 |

**새로운 것(TalkAbout에 없어 도입 후보)**:

1. **변화 있을 때만 저널 추가**(memory-config.md:982) — 저장·기록 노이즈 억제와 철회 슬롯 예약(ROOM_MEMORY.md:28)의 결합.
2. **pre-change 원문 백업 + 손실 상한**(dreaming-consolidation-artifacts.ts:35-69, 291-293) — 승인 재작성/통합의 롤백 근거(원본·승인본·저널을 구분 보존 — ROOM_MEMORY.md:55와 정합).
3. **복수 출처/다양성 게이트**(short-term-promotion.ts:126-129) — "같은 사람 반복" vs "서로 다른 사람/회차 반복" 분리(hermes-dreaming "distinct days"와 동일 방향).
4. **승인 시점 конкурент 재검증 → "변경됨, 계속 pending"**(memory.md:356-362) — TalkAbout 승인 설계(ROOM_MEMORY.md:39)에 "거절→pending 유지" 상태 기계 추가.
5. **모델 지정 제한 게이트**(memory-config.md:985) — 다중 업체 환경에서 메모리 모델 override를 동의+예산에 한정.
6. **세션/방 경계 직렬화**(memory_manager.py:638) — 방 전환 순서 보장(hándoff 요약 → 새 방 바인딩)을 단일 큐에.
7. **판정 실패 사유의 명시 로그**(openclaw dreaming 1회 경고·이벤트 jsonl) — "왜 저널이 안 생겼나"를 방 로그가 아니라 별도 이벤트로(방 이벤트 오염 방지, ROOM_MEMORY.md:40).
8. **스코어링 가중치 자체**(relevance 0.30·frequency 0.24·...) — 후보 우선순위 정렬의 기초(절대 점수로 쓰지 말고 정렬 키로만).
9. **Diary 포맷의 "재사용 금지" 원칙**: "without turning the Dream Diary into a promotion source"(dreaming.md:139) — 저널은 근거로만, 승격 입력으로 재사용 금지. **적용안**: 방 검토 저널과 후보/메모리는 완전 분리(저널 → 후보 재생성 없음).
10. **승격 메타데이터 부착**: "Every newly promoted entry carries trailing recall metadata ... `<!-- trigger: ... -->` and bounded `<!-- importance: N -->`"(dreaming.md:64-66) — 후보가 정착한 뒤에도 추적가능성 유지. **적용안**: 방 메모리 승인본에 출처·중요도 메타(별도 열/주석)를 남겨 추적·철회 근거로.
11. **사람 승인 흐름의 단일 진입점**(claim 12의 파생) — hermes-dreaming은 cron 스크립트→wake→표준 도구, hermes core는 `/memory` 게이트 — **적용안**: 방 메모리 후보→승인 경로를 단일 슬래시 표면(/memory 계열)으로 통일해 다중 업체 worker의 진입 방법 차이를 제거. **[단기]**

**TalkAbout 특수성**: 방 대화 로그는 다중 업체 에이전트(Claude Code/Codex 등) 공유이므로 "외부 모델로 나가는 데이터" 경계가 OpenClaw/Hermes보다 얇고, 입력 매니페스트·scrub·승인 게이트를 **세트**로 묶어야 함. OpenClaw식 "모델이 merge/supersede 결정"은 승인 원칙과 직접 충돌 → **[제외]** 영역 명시 상태 유지.

**격차가 큰 영역(우선 검토)**: ①전송 직전 scrub(hermes만 구현 — OpenClaw·TalkAbout 부재) ②승인 시점 경쟁 재검증→"거절+유지"(hermes만 명시) ③무작업 여부의 가시 로그(openclaw·hermes-dreaming) ④모델 지정 게이트(dreaming.model+allowModelOverride). 이 4개가 §5의 [즉시] 항목(3·4·10·14)과 연결됨.

---

## §7 미확인·한계

- **웹 판단 범위**: hermes-dreaming 내부 구현(`scripts/dream.py` 등)은 실행·정독하지 않았고 GitHub README(핀 sha `b89b7334...`)와 클론 카탈로그 YAML 인용 기반. README의 수치(예: 총 상위 설명의 정확도 수치·모드별 동작)는 외부 데이터로 실측 검증하지 않음.
- **clone depth-1**: 원격 `main` 최신 상태를 보장하지 않음(사용자 제공 HEAD 기준 판정). `website/plugins.json`은 클론에 없음 → 카탈로그 판정은 `plugin-catalog/` 기준.
- **내장 3-stage 부재 범위**: hermes 클론 전체 grep(「dreaming」)에서 내장 구현 부재를 확인했으나 외부 브랜치·다른 저장소는 전수 검사 아님. issue 25309는 제안(opt-in 제안)으로 구현 아님.
- **동작 실측 아님**: "no new diary entry"·스코어 분해(0.75 임계 등)·owner 경고 1회·FIFO 직렬화는 코드/문서가 "그렇게 정의"함을 판정한 것이며 실행·성능·지연 실측은 아님.
- **TalkAbout 상태는 문서 기준**: ROOM_MEMORY.md·COLLABORATION_CONTEXT.md 서술 기반이며 해당 서버·스크립트(예: `talkabout/memory_candidates.py`)의 전체 실측은 별도 검증 필요(부모 에이전트가 이어서 수행 가능).
- **분류 경계**: `website/plugins.json` 부재는 클론 기준이며, 배포 사이트 생성물(패키징 산출물)에 있는지 여부는 미확인(이 검증의 목표 "카탈로그 등재"는 `plugin-catalog/`로 충족 확인).
- **테스트 미실행**: 클론의 테스트든 로컬 실행이든 수행하지 않았음 — 이 보고서는 소스·문서 정적 대조 판정이며, 테스트 명·기대 문자열은 "해당 테스트가 그렇게 검사한다"는 근거 인용.
- **시점·스코프 고정**: 모든 판정 기준 시점은 2026-11-05 클론 HEAD(→ 서두 해시). 백엔드 동작(실제 스코어 수치·크론 실행·DDL)은 실측이 아닌 소스 정의 판정.
- **명명 주의**: "REM/Deep" 표현은 수면 유추 명명(문서 141-143 "sleep-time compute (arXiv:2504.13171)" 인용은 배경 참고일 뿐 구현 메커니즘이 아님) — 의미 해석에 끌려다니지 말 것.
- **번역/인용 주의**: 본 보고서의 인용은 짧은 증거 인용이며 코드 복사가 아님. 두 클론은 읽기 전용으로 유지함.
- **후속 권고(부모 에이전트 전달 사항)**: ①§5의 [즉시] 4종(3·4·10·14)은 추가 조사 없이 TalkAbout 설계 문서(ROOM_MEMORY·COLLABORATION_CONTEXT) 반영 시작 가능 ②[중기] 항목(6·9·15·16·19·21·23)은 승인/철회 저장 설계가 있는 이후 단계에서 채택 ③[제외] 모델 주도 자동 통합은 유지 — 이유는 §5 항목 11과 §6 마지막 문단.
