# 7개 외부 저장소 정밀 분석: TalkAbout 적용 판단

**기준일 2026-10-27 · 소스 정적 분석 · 제품 코드 변경 없음 · 외부 코드/테스트 실행 NOTRUN**

## 결론

도움이 된다. 그러나 7개 제품을 한꺼번에 설치하거나 runtime을 갈아끼우는 방향은 권고하지 않는다.
TalkAbout의 목표인 **에이전트들의 실제 공동 코딩·분석**에는 다음 순서가 더 직접적이다.

1. 기존 W1 작업 프로필의 **도구 이벤트 수용 공백**을 먼저 고친다.
2. 실행 로그를 대화와 분리한 **typed work evidence + 검증 receipt**를 만든다.
3. **작업 DAG·파일 write scope·격리 workspace·리뷰/검증 gate**를 방에 도입한다.
4. 그 위에 안전한 재접속 UI, 선택적 브라우저/PDF capability를 단계적으로 얹는다.

전체 상세 분석은 약 900줄 이상이며, 고정 source line 인용 262개와 로컬 링크 87개를 대상으로 파일 존재·SHA 일치·줄 범위를 기계 검사해 오류 0건을 확인했다(첫 종합 검사 기준). 이는 인용의 내용적 정확성·전체 코드 완전성·테스트 통과 증명이 아니다. 소스 checkout 7개도 지정 SHA·clean 상태를 확인했다.

이는 새 라이브러리 도입 계획이 아니라 **독립적으로 참고할 설계 우선순위**다.
소스의 기능 존재와 운영 안전성·성능·테스트 통과는 다른 증거다.

## 먼저 바로잡은 저장소 식별

사용자 목록의 3번 Codewhale URL은 잘려 있었고, 4번 oh-my-claudecode URL은 Codewhale을 가리켰다.
분석에서는 [Codewhale](https://github.com/codewhale-hq/Codewhale)와
[oh-my-claudecode](https://github.com/Yeachan-Heo/oh-my-claudecode)를 별도 프로젝트로 확인했다.
전체 pinned revision은 [소스 원장](00-SOURCE-MANIFEST.md)에 있다.

## 가장 중요한 현재 제품 발견

외부 기능보다 먼저 처리할 W1 불일치를 발견했다. 작업 명령은 도구를 켜지만,
현재 Codex exec-json parser는 여전히 `command_execution`·`file_change`를 거절한다.
따라서 **도구 이벤트가 나오는 작업 턴은 현재 성공 종료에 도달할 수 없는 정적 경로**다.
텍스트로 코드/diff만 답하는 턴까지 모두 실패한다는 뜻은 아니다. 실제 provider 실행 재현도 아니다.
독립 교차검토로 같은 경로를 확인했다. 이벤트는 native 동작 뒤 올 수 있으므로 거절/interrupt가 **명령 미실행·파일 무변경의 증거는 아니다**. 이벤트 parser 개선도 OS sandbox나 per-tool 사전 승인 구현과는 별개다.

근거와 제안 회귀 gate: [TalkAbout 결손 분석](07-TALKABOUT-GAPS.md).
이번 요청은 분석이므로 이 문제를 포함해 제품 코드는 수정하지 않았다.

## 저장소별 판정 요약

| 저장소 | 가장 유용한 내용 | 그대로 가져오면 안 되는 것 | 판단 |
|---|---|---|---|
|[herdr](01-herdr.md)|관찰 화면 detach와 process/task 수명 분리, prompt ACK/활동/확정 결과 구분, split identity, cursor gap resync|persistent PTY가 실행 중복·종료 증명을 해결한다는 가정, Unix FD handoff의 Windows 즉시 이식, host plugin 무제한 허용|**UX·수명 모델 선택 채택**|
|[agent-browser](02-browser-cloner.md)|문서 generation에 결합한 AX ref, JSON action/daemon, 명시적 default-deny 정책, snapshot 재관찰|daemon confirm을 인간 승인으로 사용, hostname allowlist를 SSRF sandbox로 주장, 사용자 Chrome/CDP/profile 공유|**격리 opt-in adapter 후보**|
|[Codewhale](03-codewhale.md)|provider capability의 unknown/false/true 구분, route/catalog snapshot, 정책 ceiling, 구조화 실행 계약|새 Rust agent runtime 통째 교체, Fleet가 기존 Codex/Claude/Grok CLI를 그대로 조율한다는 오해|**provider 계약·계획 검증 우선 참고**|
|[oh-my-claudecode](04-oh-my-claudecode.md)|task version/claim lease, mailbox identity/delivery, recovery/adoption saga, 완료와 검증 gate 분리|prompt-only permission을 강제 sandbox로 사용, provider bypass/yolo 실행 설정 그대로 채택, task done을 테스트 통과로 표시|**팀 작업 프로토콜 최우선 참고**|
|[PDFMathTranslate](05-pdfmathtranslate.md)|원본/파생 artifact, 단계형 문서 작업·layout/수식 보존 파이프라인|무한 retry/공유 평문 cache/API 노출, binary 업로드 즉시 개방, AGPL 코어 직접 import|**문서 수요 발생 시 격리 pilot**|
|[website cloner](02-browser-cloner.md)|관찰 brief→asset map→구현→비교 체크리스트, canonical skill/얇은 agent bridge|내장 자동 clone/시각 검증 엔진으로 오인, 임의 사이트 asset/브랜드 복제, 지침을 실행 증거로 표시|**작업 템플릿만 빠르게 참고**|
|[oh-my-pi](06-oh-my-pi.md)|typed terminal events, 자식 작업 정산 장벽, compaction provenance, 최신 base 재확인/staging, version-bound LSP|16bit hashline을 무결성/인가로 사용, yolo 기본값, COW/worktree를 OS sandbox로 간주, multi-file write 원자성 가정|**코딩·검증 계약 최우선 참고**|

직접 소스 이식 없이 설계만 참고하는 우선도는 **oh-my-claudecode / oh-my-pi → Codewhale / herdr → agent-browser → cloner → PDFMathTranslate**다.
이는 프로젝트 품질의 종합 순위가 아니라 현재 TalkAbout의 제품 결손·도입 비용·신뢰 경계에 대한 판단이다.

## 라이선스와 도입 비용

| 구분 | 확인한 최상위 라이선스 | 추가 검토 |
|---|---|---|
|herdr / agent-browser|Apache-2.0|NOTICE·vendor·bindings. agent-browser bundled axe의 MPL-2.0 등은 별도 조건|
|Codewhale / oh-my-claudecode / cloner / oh-my-pi|MIT|copyright/NOTICE 보존, native dependency·third-party adaptation·font/asset·브라우저 배포 조건|
|PDFMathTranslate|AGPL-3.0|수정/배포/네트워크 제공 구성과 Corresponding Source 의무, PyMuPDF·모델·폰트·BabelDOC/next 별도 검토|

MIT/Apache라는 이유로 dependency/모델/사이트 asset까지 자유롭게 가져올 수 있는 것은 아니다.
AGPL도 상업 사용 불가라는 뜻은 아니며, 별도 subprocess/container가 의무를 자동 없애는 것도 아니다.
각 상세 보고서에서 실제 LICENSE와 확인 범위를 구분했다.

## 공통 도입 원칙

- **대화 / 제어 / 실행 증거 / 산출물**을 다른 계약으로 다룬다. agent 메시지를 실행 권한으로 승격하지 않는다.
- `worker_reported`와 서버 확인·OS 관측·독립 검증을 구분한다. CLI 출력은 테스트 통과의 서버 영수증이 아니다.
- R3 one-shot start와 uncertain no-retry를 유지한다. 새 팀/task 시스템이 승인 예산을 우회하지 않게 한다.
- R4 vault는 TalkAbout bearer만 다룬다. browser cookie·provider key·PDF cache로 저장 범위를 자동 확대하지 않는다.
- prompt-based permission, UI confirm, worktree/COW, detach 가능한 PTY는 각각 OS sandbox가 아니다.
- 최초 기능은 작은 opt-in capability와 fail-closed schema로 만들고, MCP/plugins/web를 전면 켜지 않는다.
- 비용은 최상위 모델 호출뿐 아니라 내부 retry·subagent fanout·browser/PDF 작업까지 계층별 예산을 둔다.
- 외부 코드 복사·배포 전 actual license/NOTICE·dependency·asset 권리를 확인한다. 이 보고서는 법률 의견이 아니다.

## 제안 실행 순서와 수용 기준

| 우선순위 | 독립 구현할 것 | 완료를 판단할 최소 근거 |
|---|---|---|
|P0|W1 typed tool-event parser|work profile의 command/file-change 성공·실패·취소 synthetic stream, text profile 거절 유지, unknown tool 차단|
|P1|Work evidence·검증 receipt|task/attempt/start reservation·base revision·command digest·exit·diff/output hash·시간·생략 기록; secret redaction; reported/verified 구분|
|P1|Coding task DAG와 write scope|순환/미완료 blocker 거절, 현재 ACL, lease+revision CAS, stale 결과 거절, 동시 쓰기 충돌·별도 workspace·명시적 merge review|
|P1|사람의 uncertain 해결 경로|자동 재실행 금지 유지, 결정 이유/대상 attempt 감사 기록, 더 늦은 결과와 결정 충돌 규칙|
|P2|관찰 화면 재접속|observer detach≠cancel, task/process 상태 분리, stale view 복원, 재접속이 provider 재호출을 만들지 않음|
|P2|Browser capability pilot|소유 process/profile·room scope, OS egress 제한, 계정 쿠키 미상속, task-bound 인간 승인, untrusted page context, screenshot artifact ACL|
|P3|PDF/document pilot|quarantine·parser OS 격리·자원/재시도 예산·room-scoped cache/출력·asset pin·라이선스 검토; 기존 text-only 기본 유지|

### 공동 코딩 task에 필요한 핵심 데이터 예시

```text
room / task / dependency_ids / assignee / attempt / generation / membership_revision
workspace_ref / base_commit / allowed_write_scopes / work_mode
reservation_id / server_start_granted_at
worker_reported_events / verification_receipts / artifact_version_ids
review_state / human_decision / uncertainty_reason
```

위 schema는 설계 초안이다. 로컬 경로/명령 원문/credentials를 그대로 서버에 보내라는 뜻은 아니다.
workspace_ref는 다른 참여자의 filesystem 경로가 아니라 권한 범위가 있는 논리 참조여야 한다.
verification receipt는 증거 생성자와 한계를 명시해야 하며 서버가 실제 실행을 보지 않았다면 `worker_reported`다.
