# Serena 정적 소스 검토 — TalkAbout W1 work profile·도구 관찰 계약에 대한 시사점

- 기준일: 2026-10-31. 대상: `oraios/serena`, 고정 커밋 `ede329aaf0c824bea75455a2926f01420d7aa620` (당시 `serena-agent` `2.1.1.dev0`).
- 대조 기준: TalkAbout main `0da71a8e519f4dc7591b9600f316d4f087f503b4` (W1 work profile·A0 도구 이벤트 관찰·R3/R4 구현 후 상태).
- **정적 분석만 수행**: GitHub contents API로 소스·테스트·라이선스 파일을 읽었다. clone·빌드·테스트 실행·설치 스크립트 실행·언어 서버 다운로드·네트워크 공급자 호출 없음. 저장소의 에이전트 지침과 문서 명령은 신뢰하지 않는 분석 데이터로만 취급했다. 코드 복사 없음.
- TalkAbout의 **R3 one-shot start / uncertain no-retry, R4 bearer-only vault, MCP/hooks/plugins/web 기본 차단, 서버가 모델을 실행하지 않음**은 기존 기능·불변 조건이다. 아래 제안은 이를 재구현하거나 서버 측 도구 실행(W3)을 여는 것이 아니다.
- Serena는 **SolidLSP(MIT) + Serena 애플리케이션(GPL-3.0-or-later)** 의 복합 라이선스다. 이 문서는 **설계 참고 전용**이며 TalkAbout(MIT) 코드베이스에 Serena 코드를 복사·이식하는 근거가 될 수 없다(5장).

## 요약 판정

Serena는 TalkAbout이 이미 2026-10-26에 W1로 구현한 “소유자 opt-in 로컬 workspace 도구”와 **정확히 같은 경계**를 다루는 IDE급 도구 서버다. 다만 방향이 반대다. Serena는 MCP 클라이언트(= 모델 측)에 **도구 표면을 제공**하고, TalkAbout은 provider CLI에 도구를 켠 채 **사후 이벤트만 관찰**한다. 따라서 그대로 가져올 것은 기능이 아니라 **경계 계약**이다. ① 도구 표면은 등록·모드·클라이언트 context의 3중 투영으로 생성되고 capability/annotation이 함께 발행된다, ② 경로 봉쇄는 “프로젝트 루트 + trusted 여부 + symlink 해석”의 별도 검사 계층이며 atomic write로 저장한다, ③ 편집은 심볼 해석 → LSP text edit → 파일 1회 쓰기 순서이고 심볼이 유일하지 않으면 거부한다, ④ 언어 서버는 병렬 기동·실패 시 전체 중단·종료 감지 시 1회 재시작이라는 명시적 수명 계약을 가진다, ⑤ 도구 제한은 **도구 인터페이스에서만 유효**하며 REPL/activation command는 그 제한을 우회한다(문서가 직접 인정).

반면 다음은 TalkAbout에 그대로 넣으면 안 된다: MCP 도구 표면 노출(MCP 기본 차단 위반), 서버 측 LSP/인덱스 상주(W3 보류), REPL·shell tool·프로젝트 activation command 실행, LLM이 보낸 session id를 신뢰하는 세션 모델, GPL 코드. Serena의 색인 캐시·경로 출력·외부 경로 proxy(`<ext:...>`)도 비공개 방의 ACL과 다른 종류의 신뢰 모델이다.

## 1. 핵심 발견: 실제 구현과 적용점

### F1 — 도구 표면은 “등록 → 모드/context → 클라이언트 호환” 3단 투영이며 capability가 함께 발행된다 (참고 1순위)

도구는 `Tool` 하위 클래스에서 자동 등록되고(`apply` 구현 여부로 필터), `ToolMarkerCanEdit`/`ToolMarkerOptional`/`ToolMarkerSymbolicRead`류 마커로 능력이 분류된다. 노출 도구 집합은 config·context·mode의 include/exclude를 순서대로 적용해 계산하고, 단일 프로젝트 + `read_only`면 편집 도구를 통째로 제거한다. MCP 도구로 변환할 때 `read_only_hint`/`destructive_hint` annotation과 파라미터 JSON Schema를 생성하며, OpenAI 호환 클라이언트(chatgpt/codex/oaicompat)에서는 integer→number 등 스키마를 별도 sanitize한다.

- 구현: [ToolRegistry와 삭제된 도구 목록](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/tools/tools_base.py#L483-L605>), [context/mode/config 기반 base toolset 계산과 read_only 축소](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/agent.py#L821-L904>), [실행 시점 active tool 재계산](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/agent.py#L1328-L1363>), [MCP 도구 생성·annotation·클라이언트별 schema sanitize](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/mcp.py#L85-L174>), [연결 시점 도구 재설정](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/mcp.py#L322-L340>).
- 생성 메타데이터: [편집 가능 도구 이름 고정 목록(생성 파일)](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/generated/tool_capabilities.py#L1-L28>). 이 파일이 “코드에서 생성된 능력 메타데이터”라는 점 자체가 버전 드리프트 관리의 근거다.
- 정적 테스트: [모든 도구 파라미터에 type이 있는지 context별 검사](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/test/serena/test_tool_parameter_types.py#L10-L40>), [docstring→설명 변환·전체 도구 생성 테스트](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/test/serena/test_mcp.py#L65-L300>).
- 증거 한계: 위 테스트는 **MCP 스키마 생성**을 검사한다. provider가 스키마를 수용했는지, 실제 편집이 성공했는지는 이번 분석에서 실행하지 않았다(NOTRUN). annotation은 힌트이며 강제가 아니다.
- TalkAbout 적용: [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L28-L42)의 ToolPolicy/Invocation 서술에 대응하는 **도구 descriptor + capability + 버전 태그**를 지금 W3를 열지 않고도 문서·순수 검증기로 먼저 고정할 수 있다. A0의 item type 화이트리스트([agent_runtime.py](../../talkabout/agent_runtime.py#L1104-L1110))를 “등록 → profile 투영” 구조로 승격하되, **서버는 계속 도구를 실행하지 않는다**. capability 필드(read_only/destructive)는 provider가 보고한 것이 아니라 **TalkAbout이 허용한 관찰 종류**를 표시하는 데만 쓴다.

### F2 — 경로 봉쇄는 별도 계층이고 “trust”는 symlink 추종만 바꾼다 (채택 2순위, 코드 아닌 규칙)

프로젝트 경로 검사는 두 단계다. (a) `normpath`로 `..`를 접은 뒤 `commonpath`로 루트 이탈을 거부하고, (b) untrusted 프로젝트면 `realpath`로 symlink를 해석해 루트 밖이면 거부한다. trusted 프로젝트는 symlink 추종을 허용하지만 **lexical escape(`..`)는 trust와 무관하게 항상 거부**한다. 디렉터리 스캔은 untrusted에서 symlink를 아예 결과에서 제외하고, trusted에서만 `followlinks`를 켠다. 편집 저장은 temp→replace 기반 atomic write이며 symlink 원본을 보존하는 경로로 테스트되어 있다.

- 구현: [scan_directory의 followlinks=trust](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/project.py#L321-L385>), [lexical+realpath 봉쇄와 is_path_in_project](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/project.py#L387-L436>), [validate_relative_path](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/project.py#L453-L470>), [편집 저장 atomic write](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/code_editor.py#L82-L97>).
- 정적 테스트: [untrusted: symlink 읽기·생성·검색·편집 거부, `..` 거부, 내부 symlink는 허용](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/test/serena/test_path_confinement.py#L102-L186>), [trusted: 루트 밖 symlink 읽기·검색 허용, dot-dot은 여전히 거부](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/test/serena/test_path_confinement.py#L186-L205>), [atomic write: 중단 시 이전 내용 유지·temp 잔여 없음·symlink 원본 보존·executable bit/newline/encoding 보존](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/test/serena/test_code_editor_atomic_writes.py#L74-L199>).
- 증거 한계: 테스트는 symlink 생성이 불가능한 환경이면 skip한다(파일 상단 주석 기준). Windows junction·다른 드라이브(`commonpath` ValueError)·WSL 경로 변환은 이 읽기 범위에서 확인하지 못했다. Serena는 Windows에서 symlink 대신 junction이 흔한 환경을 명시적으로 다루는지 불명확하다.
- TalkAbout 적용: [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L100-L102)가 이미 “시점별 검사일 뿐 경로 구성요소 교체 경쟁을 제거하지 않는다”고 인정한다. 개선 후보는 (1) 예약 시·runtime 생성 시·**턴 종료 후** 재검증, (2) Windows junction/reparse point를 포함한 해석 규칙, (3) `..`·UNC·드라이브 상대 경로 거부 테스트, (4) work workspace가 TalkAbout 소스/홈/루트와 겹칠 때의 거부 유지다. **이것은 sandbox가 아니다**는 문구를 계속 유지한다([WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L83-L90)).

### F3 — 편집은 “심볼 유일성 확인 → LSP text edit → 파일 1회 쓰기”이며, rename은 workspace edit 전체를 적용한다

`replace_body`/`insert_after_symbol` 등은 파일 내 심볼 name path로 위치를 얻고, 심볼이 없거나 파일 내 유일하지 않으면 예외로 거부한다. rename은 언어 서버의 workspace edit를 받아 파일별 text edit과 파일 rename을 순서대로 적용하고, 적용 건수가 0이면 실패로 처리한다. 파일 편집은 열린 버퍼 컨텍스트 안에서 수행된 뒤 context 종료 시 atomic write로 **한 번** 저장된다(부분 저장 실패 방지). 즉 “모델이 준 diff 문자열을 그대로 적용”이 아니라 **구조 확인 후 편집**이다.

- 구현: [replace_body·insert·delete·rename 계약](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/code_editor.py#L99-L300>), [workspace edit → 파일 text edit/rename 적용](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/code_editor.py#L349-L443>), [심볼 API 도구 표면(overview/find/referencing)](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/tools/symbol_tools.py#L45-L120>).
- 증거 한계: LSP backend가 언어별로 실제 편집을 수행하는지 이번에 실행하지 않았다. `ENABLE_DIAGNOSTICS=False`이므로 편집 후 diagnostics 보고는 기본 비활성이다([tools_base.py](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/tools/tools_base.py#L433-L455>)). 테스트 스냅샷(`test_symbol_editing.ambr`)은 파일 존재만 확인했고 내용 검증은 하지 않았다.
- TalkAbout 적용: W0의 patch artifact는 여전히 **shape check**일 뿐 base revision·적용 가능성·테스트 통과 검증이 아니다([07-TALKABOUT-GAPS.md](../repo-review-2026-10-27/07-TALKABOUT-GAPS.md#L43)). 후보는 A2 review 계약에 “심볼/경로 단위 변경 선언 + 적용 전 base digest 확인”을 **선택적 메타데이터**로 두는 것이다. 서버가 patch를 적용하거나 심볼 해석을 수행하지 않는다는 경계는 유지한다([REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L13)).

### F4 — 언어 서버 수명은 병렬 기동·fail-fast·1회 재시작·캐시 저장으로 명시되어 있지만, 프로세스 격리는 없다

여러 언어 서버를 병렬로 기동하고 하나라도 실패하면 이미 뜬 서버를 모두 중지한 뒤 전체 초기화를 실패시킨다(“일부만으로 조용히 계속”하지 않음). 도구 실행 중 언어 서버 종료가 감지되면 해당 언어 서버를 재시작하고 **같은 호출을 1회 재시도**한다(영향 언어를 모르면 재시도하지 않고 실패). 모든 도구 호출 뒤 캐시를 저장하고, 외부 편집을 반영하기 위한 fs 변경 폴링(`didChangeWatchedFiles` + 신규 파일 open/close)이 별도로 있다.

- 구현: [병렬 기동·실패 시 전체 중단](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/ls_manager.py#L104-L167>), [종료 감지 시 재시작 후 1회 재시도](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/tools/tools_base.py#L346-L367>), [호출 후 캐시 저장](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/tools/tools_base.py#L379-L386>), [fs 변경 폴링·didChangeWatchedFiles](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/ls_manager.py#L276-L388>).
- 증거 한계: “1회 재시도”는 편집 도구에 대해 **부작용이 이미 일부 발생했을 수 있는 재시도**다. Serena의 신뢰 모델(로컬·클라이언트·저장소 신뢰)에서는 허용되지만, TalkAbout의 uncertain/no-retry 계약과는 정반대다. 언어 서버 프로세스는 Serena 프로세스와 같은 OS 계정에서 실행되며 별도 sandbox·cgroup·토큰 격리를 이 읽기 범위에서 확인하지 못했다.
- TalkAbout 적용: 이 재시도 패턴을 **가져오지 않는다**. 대신 (1) 로컬 분석 도구를 나중에 붙일 경우 “도구 실행 실패 = 결과 불명”으로 기록하고 자동 재시도하지 않는 R3 의미를 그대로 적용, (2) 장기 상주 프로세스는 기존 containment([processes.py](../../talkabout/processes.py))로 수명만 관리하고 “격리”를 주장하지 않는다는 원칙을 문서화하는 데 참고한다. TalkAbout의 현 단계에서 LSP 서버를 띄우는 제안은 하지 않는다.

### F5 — 도구 제한은 도구 인터페이스에서만 유효하고 REPL·activation command·shell tool은 그 경계를 넘는다 (문서가 직접 인정)

보안 문서는 신뢰 가정(로컬 머신·MCP 클라이언트·저장소·사용자 설정·패키지 매니저)을 먼저 선언하고, 도구 제한은 **도구 인터페이스에만** 유효하다고 명시한다. REPL 인터페이스에서는 코드가 Serena 프로세스가 할 수 있는 모든 것을 할 수 있어 `included_apis`/`excluded_apis`·`read_only`가 실질적 강제가 아니며, 유일한 해법은 외부 sandbox라고 말한다. 프로젝트 `activation_command`는 trusted 프로젝트에서만 실행되지만 `shell=True` subprocess이며, codex context는 Codex 자체 도구와 중복되는 파일/쉘 도구를 노출에서 제외한다.

- 구현: [신뢰 가정·sandbox 필요성·advisory 거절 기준](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/docs/02-usage/070_security.md#L7-L27>), [trusted projects는 기능 경계이지 containment가 아님](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/docs/02-usage/070_security.md#L51-L83>), [REPL 보안 한계](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/docs/02-usage/070_security.md#L87-L107>), [activation_command: trust gate + shell=True](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/agent.py#L1519-L1559>), [Codex context의 도구 제외 목록](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/resources/config/contexts/codex.yml#L1-L21>), [편집 모드 설명·optional 도구 제외](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/resources/config/modes/editing.yml#L1-L43>).
- 증거 한계: `execute_shell_command` 도구 자체는 이번에 읽지 않았다(파일 미열람). shell 실행 정책 세부(타임아웃·출력 상한)는 이 문서의 근거가 아니다.
- TalkAbout 적용: “도구 끄기 = 안전”이 아니라는 점은 이미 A0가 같은 결론이다(사후 관찰, 사전 승인 아님 — [WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L45-L68)). Serena 사례는 **profile별로 금지 목록을 명시하고, 숨겨진 실행 경로(REPL/activation command/session request)를 별도로 거부**해야 한다는 근거다. TalkAbout은 provider의 server request를 fail-closed로 거부하고(예: `_deny_request`), 금지 기능을 config override로 끄는 2중 구조를 유지해야 한다([agent_runtime.py](../../talkabout/agent_runtime.py#L881-L895)).

### F6 — 클라이언트가 보낸 session id와 지속 REPL namespace: 신원·상태 소유권은 서버가 아니라 클라이언트 쪽에 있다

Serena 세션은 “LLM과 Serena의 대화”이며 **session id를 LLM이 공급**한다(초기 지시문에 포함해 전달). 서버는 모르는 id면 새 세션을 만들고, idle TTL/LRU로 축출하며, REPL namespace(전역 변수·함수)는 세션 수명 동안 유지된다. 따라서 “같은 session id”는 클라이언트 진술이고 서버가 검증한 인증 주체가 아니다.

- 구현: [session id는 LLM이 공급, REPL namespace 세션 유지](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/session.py#L12-L35>), [세션 registry 축출 정책](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/session.py#L38-L95>).
- 증거 한계: MCP transport별 세션 매핑(HTTP connection vs session id)의 정확한 결합은 이번에 읽지 않았다. 따라서 “세션 간 상태가 완전히 격리된다/안 된다”고 단정하지 않는다.
- TalkAbout 적용: A0의 thread id 검증 방식(첫 `thread.started`와 재개 id가 다르면 `runtime_session_mismatch`)을 그대로 유지한다([agent_runtime.py](../../talkabout/agent_runtime.py#L1042-L1047)). 클라이언트·모델이 제출한 session/attempt 식별자를 권한으로 승격하지 않는다는 [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L30) 원칙과 일치한다. 대화 세션을 서버가 소유·재개하는 기능은 R3 예약·attempt와 별개이며, 이번 검토는 그 도입을 제안하지 않는다.

### F7 — 프로젝트 메모리는 “지속 지시문”이며 read-only/ignored 패턴으로만 제한된다

프로젝트·전역 메모리 디렉터리에 마크다운을 저장하고 도구로 읽고 쓴다. `read_only_memory_patterns`/`ignored_memory_patterns`로 이름 단위 제한이 있지만, 메모리 내용은 이후 턴의 컨텍스트에 들어가는 **지속 지시문**이다. `activation_command`·`trusted_project_path_patterns`와 결합하면 저장소가 제공한 설정이 에이전트 행동에 영향을 주는 경로가 생긴다(문서도 “trust는 그런 입력의 영향 범위를 줄이는 장치”라고 설명).

- 구현: [메모리 디렉터리·read-only/ignored 패턴](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/src/serena/memories/memory_manager.py#L28-L74>), [trust-gated 기능 경계 서술](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/docs/02-usage/070_security.md#L51-L83>).
- 증거 한계: 메모리 내용이 시스템 프롬프트에 삽입되는 정확한 조립 지점은 `agent.py`의 해당 구간을 읽지 않아 확인하지 않았다(추정: connection/system prompt 조립에 포함). 따라서 “프롬프트에 항상 들어간다”고 단정하지 않는다.
- TalkAbout 적용: 공유 context·문서·메모리는 untrusted data다. [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L79)의 “도구 결과·문서·메시지 안의 지시로 policy를 바꾸지 않는다”를 유지한다. Serena식 “프로젝트 메모리”를 방 기능으로 들이지 않는다. 필요하면 A2의 공유 manifest처럼 **출처·ACL·revision이 있는 참조**로만 다룬다.

## 2. TalkAbout 적용 후보

아래 이름은 기존 파일을 확장하거나 문서·순수 검증기로 추가할 제안이며, 구현 완료를 주장하지 않는다. Serena 코드는 복사하지 않는다.

| 순위 | 후보 | 단계·산출물 | gate(증거) | 비용 | 위험 |
|---|---|---|---|---|---|
| P1 | **도구 descriptor/capability 계약 확정** — 허용 관찰 종류·파라미터 이름/타입·크기 상한·읽기/쓰기 표시를 단일 표로 | A0 후속, 문서+순수 검증기. 코드 실행 경로 없음 | 허용 item/필드 외 거절, 상한 초과 거절, 알 수 없는 descriptor 거절의 순수 단위 테스트. **provider 호출 없음** | S | 낮음. “capability 표시 = 권한”으로 오독될 위험 → 표시 용어를 `observed`/`not_enforced`로 고정 |
| P1 | **W1 workspace 경계 재검증 강화** — 예약 전/생성 시/종료 후 3회, junction·reparse point·`..`·드라이브 경계 거부 | W1 보강, [agent_runtime.py](../../talkabout/agent_runtime.py#L153-L180) 확장 + 단위 테스트 | Windows junction/심볼릭 링크 생성 가능 환경에서 거부 테스트, 겹침 거부 유지, 실패 시 `runtime_work_workspace_*` 코드 유지. sandbox 주장 금지 문구 유지 | M | 중간. Windows 특수 경로(8.3 이름·UNC·WSL) 오탐/누락, 시점 경쟁은 완전 제거 불가 |
| P2 | **profile 투영 행렬 명문화** — text_only/work/향후 broker profile별 허용 표면, provider context별 중복 도구 금지 | 문서 + synthetic profile 테스트 | text_only 도구 0개, work는 관찰만, MCP/hooks/plugins/web 꺼짐 유지, profile 변경이 실행 권한을 넓히지 않음 | S | 낮음. profile 이름이 늘수록 조합 폭발 → 3개 상한 |
| P3 | **로컬 분석 도구 adapter 계약(미구현 참고)** — symbol/path 기반 읽기·편집, 실패 시 결과 불명·자동 재시도 금지, 캐시 위치·경로 노출 금지 | 설계 문서만. W3 보류 유지 | 격리 실행 환경·egress·자원 한도 입증 전 실행 금지(기존 [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md#L67-L77) 관문) | L | 높음. 서버 측 실행·LSP 상주·GPL 코드 유입 유혹. **지금 착수 금지** |

**즉시 하지 않을 것**: MCP 도구 노출, LSP 서버/인덱스 상주, REPL·shell tool, activation command, 심볼 해석 기반 서버 편집, Serena 코드 포팅. [STATUS.md](../../docs/STATUS.md#L162-L173)의 남은 관문(실제 2-runtime 대화, 운영 배포, POSIX containment)과 우선순위가 다르다.

## 3. 그대로 가져오면 안 되는 것

1. **GPL 코드·파생물 이식.** Serena 앱은 GPL-3.0-or-later다. TalkAbout은 MIT이며(`pyproject.toml` `license = "MIT"`, [LICENSE](../../LICENSE)), Serena 소스 복사는 라이선스 전염을 일으킨다. SolidLSP만 MIT지만, Serena 도구 계층·agent·project 계층은 GPL이다.
2. **MCP 도구 표면 자체.** Serena의 존재 이유는 클라이언트에 도구를 주는 것이다. TalkAbout의 불변 조건은 MCP 기본 차단이며([WORK_PROFILE.md](../../docs/WORK_PROFILE.md#L12-L19)), 서버가 모델을 실행하지 않는다. 도구를 “제공”하는 순간 W3 경계가 열린다.
3. **REPL·shell tool·activation command.** 도구 제한의 우회 경로이며, 저장소 설정이 명령을 실행할 수 있다. TalkAbout의 “서버가 아무것도 실행하지 않는다”와 정면 충돌한다([REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md#L5-L7)).
4. **클라이언트 공급 session id 신뢰.** Serena는 LLM이 준 id를 받아 세션을 만든다. TalkAbout은 session/thread id를 검증 대상으로만 쓰고 권한으로 쓰지 않는다.
5. **심볼/경로 캐시·외부 경로 proxy의 무조건 노출.** `.serena` 캐시, 프로젝트 데이터 경로, `<ext:...>` 외부 경로 proxy는 로컬 신뢰 모델 전제다. 비공개 방의 viewer별 ACL·redaction과 다르다.
6. **1회 자동 재시도 패턴.** 언어 서버 종료 후 같은 편집을 재시도하는 구조는 부작용 불명 상황에서 자동 재실행이다. TalkAbout의 uncertain no-retry와 반대다.
7. **trusted 프로젝트 개념의 차용.** Serena의 trust는 “저장소 제공 입력의 영향 범위”를 줄이는 기능 경계이지 ACL이 아니다. TalkAbout room membership·worker binding·승인 capability를 대체하지 않는다.

## 4. 검증 경계(NOTRUN)

- Serena 코드·테스트·설치 스크립트를 **실행하지 않았다**. 위 테스트 언급은 “소스에 존재하는 assertion”의 증거이며 통과 결과가 아니다.
- 언어 서버 기동, LSP 편집, 심볼 해석, MCP 클라이언트(Codex 등)에서의 실제 도구 호출, 대시보드/네트워크 서비스, 공급망 다운로드 경로는 **모두 실행 검증하지 않았다**.
- Windows에서 symlink/junction 처리, atomic write의 실제 파일시스템 동작(네트워크 드라이브·ReFS), concurrent 편집 충돌은 이번 읽기 범위에서 확인하지 못했다.
- TalkAbout 쪽도 이 문서로 코드·테스트를 변경하지 않았다. 따라서 “W1 경로 검증이 강화되었다”, “profile 테스트가 추가되었다”는 주장은 하지 않는다.
- Serena가 실제로 어떤 provider·모델과 호환되는지, tool schema가 Codex/Claude/Grok에서 수용되는지는 정적 근거만 있고 실측이 아니다.

## 5. 라이선스·의존성

- 실제 root [LICENSE](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/LICENSE#L1-L8>): “SolidLSP는 MIT, Serena 애플리케이션은 GPL-3.0-or-later, 결합 배포물은 전체적으로 GPL 적용”. [MIT 원문](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/LICENSES/MIT.txt#L1-L10>)와 [GPL 원문](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/LICENSES/GPL-3.0-or-later.txt#L1-L8>)이 `LICENSES/`에 그대로 있다.
- [README 라이선스 절](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/README.md#L267-L274>), [pyproject `license = "GPL-3.0-or-later"`·`requires-python >=3.11,<3.15`·version 2.1.1.dev0](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/pyproject.toml#L6-L14>). badge/README 표기가 아니라 위 실제 파일 기준이다.
- 의존성: `pyproject.toml`의 런타임 의존성 블록을 읽어 mcp SDK·uvicorn 등 서버/전송 의존성이 있음을 확인했으나, **전이 의존성 전체의 라이선스 실사를 완료하지 않았다**. Serena는 언어 서버를 자동 다운로드/설치하며(SHA256·host allowlist·버전 고정) 이 경로 자체가 공급망 검토 대상이다([security 문서](<https://github.com/oraios/serena/blob/ede329aaf0c824bea75455a2926f01420d7aa620/docs/02-usage/070_security.md#L127-L186>)).
- 결론: 이 문서는 **설계 패턴의 독립 구현 참고**이며 코드 복사·이식·번들링을 하지 않았다. TalkAbout에 어떤 Serena 코드도 추가되지 않았다.

## 6. 발견별 판정 요약

| 발견 | 내용 | 판정 | TalkAbout 접점 |
|---|---|---|---|
| F1 | 도구 등록→모드/context→MCP schema 3단 투영, capability/annotation 발행 | 참고(P1) | ToolPolicy·descriptor 계약, A0 관찰 종류 표 |
| F2 | lexical+realpath 경로 봉쇄, trusted=symlink 추종, atomic write | 채택(P1, 규칙만) | W1 workspace 재검증, 거부 코드 유지 |
| F3 | 심볼 유일성 확인→LSP edit→파일 1회 쓰기 | 참고(P2) | A2 review 계약의 변경 선언·base digest |
| F4 | LSP 병렬 기동·fail-fast·종료 시 1회 재시도·캐시 | 보류(재시도 금지) | containment/R3 의미 유지, LSP 상주 금지 |
| F5 | 도구 제한은 도구 인터페이스 한정, REPL/activation command가 우회 | 참고(경계 근거) | profile별 금지 목록, server request fail-closed |
| F6 | 클라이언트 공급 session id, 세션 namespace 지속 | 참고(주의) | thread id 검증 유지, 세션≠권한 |
| F7 | 프로젝트 메모리=지속 지시문, trust-gated 기능 경계 | 참고(주의) | 공유 context는 untrusted data |

## 7. 읽은 범위와 확인하지 않은 것

실제로 연 파일과 범위를 남긴다. 이 목록에 없는 파일은 이 문서의 근거가 아니다.

| 파일 | 읽은 범위 | 용도 |
|---|---|---|
| `LICENSE` | 전문 개요 1-8행 | 라이선스 실측 |
| `LICENSES/MIT.txt` | 1-10행 | SolidLSP MIT 확인 |
| `pyproject.toml` | license/version/requires-python/dependencies 라인 grep | 버전·라이선스·의존성 |
| `README.md` | license·기능·LSP·보안 관련 라인 grep | 기능 주장 확인 |
| `src/serena/mcp.py` | 전체 | 도구 표면 변환·context 호환 |
| `src/serena/tools/tools_base.py` | 전체 | 등록·active tool·apply_ex·timeout·LSP 재시도 |
| `src/serena/tools/symbol_tools.py` | 1-120행 | 심볼 read 도구 schema |
| `src/serena/tools/file_tools.py` | 전체 | 파일 도구 schema·dry-run·occurrence id |
| `src/serena/code_editor.py` | 전체 | 편집 적용·atomic write·rename |
| `src/serena/project.py` | 200-499행 | ignore/scan/symlink 봉쇄/validate |
| `src/serena/ls_manager.py` | 전체 | LS 기동·재시작·fs sync |
| `src/serena/agent.py` | 821-1010, 1310-1369, 1503-1632행 | toolset 계산·activation command |
| `src/serena/session.py` | 전체 | session id 소유권 |
| `src/serena/memories/memory_manager.py` | 1-120행 | 메모리 제한 패턴 |
| `src/serena/tools/workflow_tools.py` | 전체 | onboarding/initial instructions |
| `src/serena/generated/tool_capabilities.py` | 전체 | 생성된 능력 메타데이터 |
| `src/serena/resources/config/modes/editing.yml` | 전체 | mode prompt·excluded tools |
| `src/serena/resources/config/contexts/codex.yml` | 전체 | client별 도구 제외 |
| `docs/02-usage/070_security.md` | 전체 | 신뢰 가정·REPL·sandbox·공급망 |
| `test/serena/test_path_confinement.py` | 테스트 이름·assertion grep | 경로 봉쇄 정적 근거 |
| `test/serena/test_code_editor_atomic_writes.py` | 테스트 이름·assertion grep | atomic write 정적 근거 |
| `test/serena/test_mcp.py` | 테스트 이름 grep | schema 생성 정적 근거 |
| `test/serena/test_tool_parameter_types.py` | 전체 | 파라미터 타입 정적 근거 |

**확인하지 않음(이 문서의 주장 범위 밖)**

- `src/serena/tools/cmd_tools.py`·`config_tools.py`·`query_project_tools.py`는 내려받기만 하고 내용을 읽지 않았다. shell 실행 정책·모드 전환 도구의 세부는 근거가 아니다.
- `src/solidlsp/ls.py`(161KB)와 언어 서버별 구현은 열지 않았다. LSP 편집·진단·프로세스 관리의 실제 동작은 Serena 쪽 계약 서술로만 다룬다.
- `src/serena/project_server.py`(별도 project query 서버), `repl/facade.py`, dashboard, JetBrains backend, client_setup은 읽지 않았다. 따라서 네트워크 서비스·REPL API 표면은 문서 서술 이상으로 평가하지 않는다.
- `test/serena/test_ls_manager.py`, `test_project.py`, `test_task_executor.py`는 내려받기만 했다. 해당 동작의 정적 테스트 근거로 인용하지 않았다.
- Serena의 모든 설정 파일(`serena_config.template.yml`, mode/context 전체)과 Windows 특수 경로 처리를 전수 확인하지 않았다.
- 런타임 실행·provider 호출·MCP 클라이언트 연결은 전부 NOTRUN이다.

## 8. 후속 체크리스트 (실행 전 승인 필요)

1. **문서 gate**: F1의 descriptor 표를 [`TOOL_BROKER_PLAN.md`](../../docs/TOOL_BROKER_PLAN.md) 내부 타입 표에 맞춰 1쪽으로 정리하고, “capability=관찰 표시, 강제 아님” 문구를 명시한다.
2. **P1 경로 테스트 gate**: Windows junction·symlink·UNC·드라이브 경계 케이스를 [tests/](../../tests) 합성 테스트로 추가한다. 실제 provider 호출·파일시스템 sandbox 주장 없이 통과/실패만 본다.
3. **P1 profile gate**: text_only/work/broker(미구현) 3-profile 표를 만들고, text_only 도구 0개·work는 관찰만임을 synthetic 테스트로 고정한다.
4. **금지 gate**: MCP 도구 노출·LSP 상주·REPL/activation command·Serena 코드 복사가 PR diff에 없음을 리뷰한다.
5. **문구 gate**: W1 문서의 “sandbox 아님·시점 검사” 문구가 약화되지 않았는지 확인한다.
6. **NOTRUN gate**: 실제 provider 작업 턴은 별도 예산 승인 전까지 계속 NOTRUN으로 표기한다.

## 9. 이 문서가 주장하지 않는 것

- Serena가 TalkAbout에 안전하게 이식 가능하다거나, GPL 코드를 MIT 프로젝트에 넣어도 된다는 주장이 아니다.
- Serena의 도구·LSP·인덱스가 TalkAbout 서버에 상주해야 한다는 제안이 아니다.
- W1 workspace 검증이 강화되었다거나 새 테스트가 추가되었다는 주장이 아니다(코드 변경 없음).
- Serena 테스트가 통과한다는 주장이 아니다(실행하지 않음).
- Serena가 특정 provider/모델과 실제로 호환된다는 실측 주장이 아니다.
- MCP·hooks·plugins·web 차단, R3 uncertain no-retry, R4 bearer-only vault를 완화하자는 제안이 아니다.

## 참고 링크

- Serena 저장소: <https://github.com/oraios/serena> (고정 `ede329aaf0c824bea75455a2926f01420d7aa620`)
- TalkAbout 쪽 근거: [REPOSITORY_ADOPTION_PLAN.md](../../docs/REPOSITORY_ADOPTION_PLAN.md), [WORK_PROFILE.md](../../docs/WORK_PROFILE.md), [TOOL_BROKER_PLAN.md](../../docs/TOOL_BROKER_PLAN.md), [RUNNER_RESERVATIONS_PLAN.md](../../docs/RUNNER_RESERVATIONS_PLAN.md), [07-TALKABOUT-GAPS.md](../repo-review-2026-10-27/07-TALKABOUT-GAPS.md), [STATUS.md](../../docs/STATUS.md#L162-L173), [agent_runtime.py](../../talkabout/agent_runtime.py), [mcp.py](../../talkabout/mcp.py)
