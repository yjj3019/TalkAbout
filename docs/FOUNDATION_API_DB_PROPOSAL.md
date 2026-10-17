# 내부 기반을 실제 서비스에 연결하기 위한 변경 제안

상태: **PROPOSED / NOT IMPLEMENTED**, 2026-10-17. 검토 대상은 공개 계약과 영속 저장 경계입니다. 이번 브랜치는 아래 API·DDL·저장소 설정을 적용하지 않습니다.

현재 [내부 계약](INTERNAL_FOUNDATION_CONTRACT.md)은 합성 resolver와 메모리 상태만 사용합니다. 실제 인증·tenant 격리·재시작 복구를 제공하지 않습니다. 기존 인증된 actor와 방 멤버십을 조회하는 adapter가 준비돼야 실제 서비스의 권한 경계로 사용할 수 있습니다.

## Additive API 제안

| 경로/응답 확장 | 주체와 검증 |
| --- | --- |
| GET/PATCH /api/v1/rooms/{room}/tool-policies/{agent} | human policy 관리자. profile 기본 text_only, target/tool/action allowlist, 만료, expected_revision. PATCH는 감사 이력과 새 revision을 생성 |
| POST /api/v1/rooms/{room}/tool-invocations | 인증된 agent와 현재 task/lease. 서버가 fence를 구성하고 immutable 인자·대상 digest와 idempotency key를 결합. 쓰기는 awaiting_approval |
| POST .../tool-invocations/{id}/approvals | 실제 human session 및 CSRF. 정확한 request digest·fence·만료를 화면에서 확인. agent 또는 메시지의 role=admin 주장을 받지 않음 |
| POST .../tool-invocations/{id}/revocation | human 권한 검증. 실행 전 cancelled, 이미 executing이면 결과 불명 가능성을 기록. 외부 효과 회수를 보장하지 않음 |
| GET .../tool-invocations/{id} | 동일한 owner/room/agent 또는 승인된 human의 객체 ACL. 상태·차단 이유·승인/철회 이력 제공. 비밀값 제외 |
| POST .../uploads, PUT .../uploads/{id}/content, POST .../uploads/{id}/complete | 권한·quota·idempotency·bytes 상한·room revision 검증. 서버 ID만 사용. 기존 비활성 경로는 feature flag 승인 전 계속 거절 |
| GET .../files/{file}/versions/{version} | 상태·형식·hash·검사/추출 실패 이유 및 허용 representation metadata. 검사 미실행을 안전으로 표시하지 않음 |
| GET .../download, GET .../preview, POST .../retrieve | 다운로드와 미리보기의 공통 객체 ACL. agent 읽기는 task·lease·파일 공유·runtime capability를 추가 확인. retrieval에는 범위·task 누적 한도 |
| DELETE .../files/{file} | 삭제 권한·expected_revision. 즉시 tombstone과 검색·context·다운로드 차단, 후속 bytes 정리 |
| message/task 응답의 attachment_refs | 같은 방의 immutable file/version/hash만 참조. message 가시성과 읽기 시작 revision을 적용. task에는 metadata만 기본 제공 |

권한 이름과 endpoint는 확정 계약이 아닙니다. file:upload/read/read_original/retrieve/send_provider/manage와 tool policy/approval 권한을 기존 scope 체계와 대조한 뒤 결정합니다. provider 전송 동의는 파일 다운로드 권한과 별개이며 사람의 명시적 선택에 묶습니다.

task lease 소유권은 서버가 발행·검증하는 증명이 필요합니다. 단순 attempt나 클라이언트가 만든 claim 키를 외부 도구 실행 권한으로 사용하지 않습니다. 기존 text worker와 새 capability를 구분하고, 구버전 client에는 도구 권한을 자동으로 추가하지 않습니다.

## 운영 DB 제안

| 객체 | 필요한 영속 계약 |
| --- | --- |
| tool_policies / connection_grants | owner·room·agent·대상·scope·profile·정책 revision·만료·철회. 계정 비밀은 별도 저장 경계의 handle만 참조 |
| tool_invocations / approvals / audit | immutable request digest와 fence, human actor, 단일 사용 승인, 상태·시간·외부 결과 ID. idempotency unique key에 owner/room/agent를 포함 |
| files / file_versions / uploads | owner/room/uploader, 서버 object key, immutable version/hash/MIME/bytes, 공유 범위·provider 동의, 처리 revision·만료·tombstone |
| file_checks / extractions / chunks | 검사기/추출기 버전, 원본 hash, 성공/실패/unknown, 페이지·line·문자 범위, private 파생물 key |
| message_attachments / task_file_links / artifact_file_versions | 같은 방·가시성 revision·불변 version 참조. 메시지 및 산출물과 파일 생명주기의 연결 |
| outbox / transfer_audit / deletion_jobs | 감사 event와 queue의 원자적 기록, provider 전송 준비/전송 결과 구분, 삭제·cache/index 회수와 orphan 정리 |

ready → executing 전이는 DB transaction에서 현재 lease/정책/승인을 재확인하고 승인 소비·호출 예산 예약과 함께 한 번만 수행해야 합니다. 프로세스가 executing 상태에서 종료되면 외부 쓰기를 unknown으로 복구합니다. 결과 조회·대조 없이 새 호출로 재시도하지 않습니다. 메모리 lock을 다중 worker의 영속 동시성 제어로 대체해 설명하지 않습니다.

처음에는 추가 테이블과 nullable/빈 attachment 필드만 검토합니다. feature flag off 상태로 migration을 먼저 확인하고, 기존 메시지·권한·text-only runtime 회귀를 실행합니다. schema 버전 협상, 이전 worker와 공존, rollback 후 남는 ledger와 unknown 작업 처리도 적용 전에 결정합니다. 이 문서에는 실행할 migration SQL을 포함하지 않습니다.

## 저장소·UI·출시 관문

실제 blob storage는 webroot 밖 private quarantine과 검사 통과 자료를 분리해야 합니다. 서버 ID와 hash를 사용하고 quota·streaming bytes·pixel/페이지/추출 한도·물리 삭제·backup 만료를 검증합니다. 공용 URL 대신 현재 ACL을 확인하는 handler로 제공하며 preview는 cookie/자격증명을 유출하지 않는 별도 경계를 설계합니다.

실제 scanner/parser와 명령 실행 환경은 독립 격리 검증을 거칩니다. Windows Job Object만으로 파일·네트워크 sandbox를 제공한다고 표시하지 않습니다. MCP OAuth, Notion/GitHub 계정, provider 파일 전송과 컨테이너/네트워크 설정 변경은 현재 단계에서 활성화하지 않습니다.

UI는 연결 대상·읽기/쓰기 scope·승인 대기·차단 이유·철회·unknown 상태를 KO/EN으로 보여 줘야 합니다. 파일은 원본·추출문·이미지 지원 여부, 추출 실패, 실제 provider에 전송될 범위와 이미 전송된 자료의 회수 한계를 구분합니다. 실제 adapter·API·DB 검증 전에는 내부 합성 기반을 사용자 기능 완료로 안내하지 않습니다.

English: proposed additive contracts only. Review server-issued lease authority, authenticated human approval, transactional invocation/approval consumption, durable unknown recovery, private file storage and API/DB compatibility before integration. No endpoints, migrations or real connections are enabled by this branch.
