# 내부 도구·첨부 기반 구현 계약

> 2026-10-17 development update: the opt-in UTF-8 text API/GUI/private-storage slice is now implemented in [TEXT_ATTACHMENT_VERTICAL.md](TEXT_ATTACHMENT_VERTICAL.md). This document retains the earlier foundation/general-binary proposal scope. Fake tools/scanners remain unwired; malware scan, images/PDF/Office and operating deployment remain NOTRUN.

기준일: 2026-10-17. 범위: **미연결 내부 기반**. GUI/API·실제 runtime·운영 DB에 연결하지 않습니다.

[ToolBroker 계획](TOOL_BROKER_PLAN.md)과 [첨부 계획](FILE_ATTACHMENTS_PLAN.md)의 첫 단위를 이 계약으로 한정합니다. 실제 명령 실행, 파일 parser, scanner, MCP 계정 인증과 외부 전송은 제공하지 않습니다. 기존 text-only 프로필, uploads 거절과 기존 runtime 정책을 유지합니다.

## 신뢰 경계

내부 서비스 호출자는 인증된 adapter 역할을 맡습니다. 신원·멤버십·task·lease·정책·message 가시성은 서비스에 주입한 신뢰된 resolver에서 가져옵니다. 메시지나 모델이 제출한 권한 목록으로 승인하지 않습니다. 이번 메모리 resolver는 합성 테스트용이며 실제 인증 계층이 아닙니다.

도구 요청은 agent/room/task, generation, membership revision, attempt, lease ID, policy revision과 만료에 묶습니다. 인자는 유한한 JSON으로 복사·고정하고 digest를 계산합니다. 쓰기 승인은 human authorizer가 확인한 주체와 정확한 요청 digest에 결합합니다. 승인 대상이나 인자를 바꾸면 새 요청과 승인이 필요합니다.

기본 text_only 정책에는 도구가 없습니다. fake_tools는 고정된 메모리 도구만 등록합니다. 도구·동작·대상 allowlist는 교집합으로 확인합니다. 실행 직전과 결과 반환 직전에 현재 권한을 다시 검사합니다. unknown 쓰기는 같은 키나 새 키의 같은 요청으로 자동 재시도하지 않습니다. 실행 중 동시 호출도 두 번째 adapter 호출을 만들지 않아야 합니다.

## 첨부 계약

서버가 생성한 file/version ID와 hash로만 bytes를 식별합니다. 표시 이름은 경로가 아닙니다. 이 단위는 제한된 UTF-8 text/code만 받으며 binary/image/PDF는 지원하지 않는 형식으로 명시적으로 거절합니다. 파일 내용은 비신뢰 데이터이며 실행하지 않습니다.

업로드 결과는 quarantine입니다. fake scanner/extractor의 합성 통과 결과만 fixture_ready로 구분합니다. 실제 안전 판정인 clean으로 표시하지 않습니다. 미검사·unknown·실패 자료는 bytes와 추출문 읽기를 거절합니다. 원본과 추출문 접근 권한·runtime capability·provider 전송 동의를 각각 확인합니다.

메시지 첨부는 같은 방의 실제 draft와 불변 file/version/hash를 연결합니다. task context에는 신뢰된 task-message 연결과 message 가시성을 통과한 metadata만 넣습니다. 구간 읽기는 문자 수와 task 누적 한도를 강제하고 hash·문자/line 범위와 잘림 여부를 반환합니다. 메모리의 UTF-8 추출은 PDF/image parser 지원을 의미하지 않습니다.

삭제·공유 철회·멤버십/lease/정책 변경은 검색·context·구간 읽기·다운로드·미리보기에 다시 적용합니다. 이미 반환된 bytes를 회수했다고 주장하지 않습니다. provider용 입력은 별도의 전송 대상·동의 검사를 거쳐 prepared로 기록하며, 실제 전송이나 provider 소비를 확인한 기록이 아닙니다.

현재 메모리 구현의 기본 상한은 파일 1 MiB, 공유 actor 16개, 메시지 참조 4개, task 참조 8개, 구간 8,000자와 task 누적 24,000자입니다. 형식은 text/plain UTF-8인 .txt/.md/.py/.js로 제한합니다. 업로드마다 불변 version 하나를 만들며 파일 version 체인과 산출물 DB 연결은 후속 범위입니다.

메서드의 actor_id와 fence는 인증된 host adapter가 구성해야 합니다. 모델 JSON의 actor ID를 그대로 연결하면 안 됩니다. 현재 합성 resolver 자체는 로그인·tenant 인증을 제공하지 않습니다.

## 공개 연동 전에 검토할 변경

| 경계 | 후속 변경 계획 |
| --- | --- |
| 도구 API | 정책/연결 조회, 정확한 요청에 대한 human 승인·철회, invocation 상태 조회를 additive v1 경로로 제안. 일반 메시지로 승인하지 않음 |
| 파일 API | 기존 upload 비활성 경로의 intent/content/complete/status, 인증된 download/preview/retrieve, message attachment_refs 추가. 기본 feature flag는 off |
| task/lease | 서버 발행 소유권 증명과 정책 revision을 인증된 실행 문맥에 결합. 기존 client의 버전 전환과 만료 계약 검토 필요 |
| 운영 DB | policy/grant/approval/invocation·감사 이력, files/versions/checks/extractions/chunks, message/task 첨부 연결, 삭제 tombstone과 outbox 제안 |
| 저장소 | webroot 밖 private quarantine·가용 blob 분리, quota와 만료·orphan 정리, 별도 preview origin 및 다운로드 ACL 설계 |
| 출시 | schema migration·구버전 client 호환·rollback·영속 idempotency와 재시작 후 unknown 복구를 검토한 뒤 실제 adapter 연결 |

현재 단계에서는 위 경로·DB migration·저장소 설정을 추가하지 않습니다. 메모리 상태는 재시작하면 소실되므로 외부 쓰기와 실제 파일 보존에 사용할 수 없습니다. 미래의 durable ledger와 원자적 lease/승인 소비 계약이 준비되기 전에는 실제 adapter를 등록하지 않습니다.

## 검증 범위

합성 테스트는 정확한 allowlist, 다른 agent/room/task/revision/lease 차단, 만료·철회, 승인 인자 불변성, 동시 실행 1회, 결과 불명 쓰기 재시도 금지를 확인합니다. 첨부는 quarantine 차단, 실패 상태, 형식·크기·경로 입력, 방/agent ACL, 실제 draft 참조, task 범위와 문자/누적 한도, 출처, 삭제·공유 철회와 전송 동의도 확인합니다. 기존 Python·Node·Chrome 회귀는 그대로 실행합니다.

English: internal memory-only foundation, not a connected feature. Trusted resolvers must supply authenticated authority. Only fake tools and bounded UTF-8 fixture files are supported. No public API, DB migration, real parser/scanner, provider transfer, shell or MCP authentication is enabled.
