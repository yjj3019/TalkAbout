# Room files that agents can read

> 2026-10-17 development update: the opt-in UTF-8 text API/GUI/private-storage slice is now implemented in [TEXT_ATTACHMENT_VERTICAL.md](TEXT_ATTACHMENT_VERTICAL.md). This document retains the earlier foundation/general-binary proposal scope. Fake tools/scanners remain unwired; malware scan, images/PDF/Office and operating deployment remain NOTRUN.

Status: **PROPOSED / NOT IMPLEMENTED**. Reviewed: **2026-10-17**.

실제 채팅 첨부 기능은 아직 연결하지 않았습니다. 이번 브랜치의 [내부 기반 계약](INTERNAL_FOUNDATION_CONTRACT.md)은 메모리 저장소·합성 검사·UTF-8 구간 읽기만 다룹니다. 아래 API/DB·이미지/PDF·실제 검사와 저장소는 후속 제안입니다.

사람이 채팅방에 올린 이미지·문서·코드 파일을 권한 있는 에이전트가 읽고 토론에 활용하는 것이 목표다. agent의 결과 파일 업로드와 산출물 버전 보존도 같은 객체 권한 모델로 다룬다. 단순 보관·다운로드 기능으로 완료하지 않는다.

이 계획은 PR #8의 검증된 수정과 별도다. 현재 API는 uploads 요청을 거절하고 health의 binary_uploads는 false다. 이 문서는 실제 파일 업로드, 외부 모델 전송, scanner 실행, 계정·저장소 생성 또는 운영 DB/보안 설정 변경을 수행하지 않는다.

## 기존 설계와의 연결

[기존 API 초안](API_DRAFT.md)의 upload intent/complete/status, [아키텍처](ARCHITECTURE.md)의 private quarantine 저장소, [보안 설계](MODERATION_AND_SECURITY.md)의 검사·다운로드 ACL, [보존 경계](RETENTION_BOUNDS.md)를 구체화한다. 추가되는 범위는 메시지 attachment 참조, agent 파일 읽기 권한, task context 연결과 bounded retrieval이다. 파일 내용으로 [ToolBroker 정책](TOOL_BROKER_PLAN.md)을 변경하거나 명령 실행을 승인하지 않는다.

## 최소 사용자 흐름

1. 사람이 방에서 파일을 선택하면 형식·용량 한도, 공유할 agent, 원본/추출문/이미지 접근 범위와 외부 provider 전송 가능 범위를 확인한다. 지원하지 않는 형식은 업로드 전에 설명한다.
2. 서버가 생성한 upload/file ID로 제한된 bytes를 private quarantine에 저장한다. 원래 파일명은 표시 metadata이며 경로·object key가 아니다.
3. 실제 형식 검사와 malware 검사 상태를 별도로 기록한다. 검사 미실행·실패·timeout은 pending/unknown/rejected 상태로 남기고 안전하다고 표시하지 않는다. ready 파일도 절대적 무해함을 보증하지 않는다.
4. 접근 가능한 파일만 메시지에 `attachment reference`로 연결한다. 메시지에는 immutable file version과 hash를 묶고 파일 bytes나 공개 URL을 삽입하지 않는다. 다른 방 file ID와 승인되지 않은 참조는 거절한다.
5. task에는 접근 가능한 attachment 목록과 metadata만 기본 제공한다. agent는 허용된 원본/페이지·구간/이미지만 명시적으로 요청한다. runtime은 capability와 전송 정책을 검증하고 실제 전송 내용을 감사 기록에 남긴다.
6. 삭제·권한 철회 시 새 다운로드·미리보기·검색·context assembly를 즉시 차단한다. 이미 외부 provider에 보낸 내용은 회수됐다고 주장하지 않는다. 해당 전송의 범위와 provider 보존 한계를 사람에게 설명한다.

## 읽기와 전송 capability

| 입력 | 별도 검증과 사용자에게 보일 결과 |
| --- | --- |
| 원본 bytes | agent 원본 읽기 권한과 provider 전송 승인을 따로 확인. 다운로드 권한이 곧 모델 전송 승인은 아님 |
| 추출문 | extractor 종류·버전, 원본 hash, page/line/offset, truncation과 오류 기록. 추출 실패를 빈 문서나 성공으로 표시하지 않음 |
| 이미지 | 실제 raster decode, 해상도·pixel·frame 한도와 변환 결과 확인. image-input capability가 없는 runtime에는 지원 불가를 표시 |
| 코드·plain text | UTF-8 등 명시한 인코딩·텍스트 검증, line 범위 참조. 코드를 읽어도 실행하지 않음 |

최초 사용 가능 형식은 제한된 plain text/code와 PNG/JPEG 등 raster 후보부터 선정한다. PDF는 parser 격리·페이지/리소스 한도·능동 콘텐츠 제거 또는 거절 정책을 검증한 다음 추가한다. macro 문서, 암호화 문서, HTML/SVG, 실행물, archive는 초기 제외한다. 코드 확장자는 source-text allowlist에 포함된 경우에만 데이터로 수용한다.

제안 초기 한도는 파일 10 MiB, 메시지당 4개, agent retrieval 1회 8,000자·task 누적 24,000자, 이미지 최대 16 megapixels와 frame 1개다. 이 수치는 구현 확정값이 아니며 저장량·방 quota·동시 처리·추출 메모리/시간 한도와 함께 검증한다. bytes 수·이미지 pixel·추출 결과 한도는 독립적으로 강제한다. 파일 전체를 매번 프롬프트에 복제하지 않는다.

검색 결과에는 file/version/hash와 page/line/offset 출처, 반환 범위, 더 읽을 수 있는 범위 및 truncation을 제공한다. summary와 index도 원본의 ACL·삭제 상태·revision을 따라야 한다. 방 전체 공유와 agent별 공유를 구분하고, provider ID·입력 종류·범위·용량을 실행 전 화면과 감사 이력에 보여 준다. 실제 읽지 않은 파일을 읽었다고 표시하지 않는다.

## 제안 API 계약 — 구현 전 검토 대상

| 경로/변경 | 계약 초안 |
| --- | --- |
| POST /api/v1/rooms/{room}/uploads | upload 권한, 방 quota, generation/membership revision, 크기·형식·공유 범위 검증 후 서버 ID 발급. idempotency 필수 |
| PUT /api/v1/rooms/{room}/uploads/{upload}/content | uploader 인증과 bytes streaming 상한. filename/path/외부 URL을 저장 대상에 사용하지 않음 |
| POST /api/v1/rooms/{room}/uploads/{upload}/complete | 실제 bytes 크기·hash 확인 후 검사 queue. 중복 complete는 같은 upload에 대해 재현 가능 |
| GET /api/v1/rooms/{room}/files/{file}/versions/{version} | uploader가 볼 수 있는 처리 상태와 agent에게 공개 가능한 metadata를 구분 |
| GET .../download 또는 /preview | 같은 room/file/agent ACL·현재 철회 상태·검사 상태. download로 preview 정책을 우회하지 않음 |
| POST .../retrieve | 현재 task/lease/정책·agent별 읽기 권한을 확인해 제한된 chunk/이미지 파생물 반환. 임의 URL fetch 없음 |
| 메시지 생성/완료 body | attachment_refs 추가 제안. file/version/hash, 참조 허용 상태와 방을 서버에서 검증 |
| DELETE .../files/{file} | expected_revision과 삭제 권한, 즉시 tombstone 및 검색/context 차단, 후속 물리 삭제 |

scope는 기존 room:contribute와 별도 file upload/read-original/read-extracted/send-to-provider 권한의 관계를 먼저 확정한다. self-registered capability는 권한 증명이 아니다. agent-origin 업로드는 실행 중 task/lease에 묶고, 사람의 일반 업로드는 human actor·멤버십·방 정책에 묶는다. 이 경로와 body 확장은 기존 text-only 응답 및 client 호환성, feature flag와 오류 코드를 검토한 후 구현한다.

## DB와 저장소 변경 제안

files/file_versions: owner·room·uploader actor, 불변 version/hash/bytes/MIME, 표시 이름, 공유 agent 범위, 상태·policy revision·tombstone. upload_sessions: 제한·만료·검증된 bytes와 idempotency. file_checks/extractions/chunks: scanner/extractor 버전, 결과와 오류, 원본 hash, page/line 범위. message_attachments/artifact_file_versions: 메시지 및 결과물과 불변 버전 연결. access/transfer audit: actor, room/task/lease, 범위, provider, 승인과 결과.

운영 DB migration과 공개 API 변경은 이 제안에 대한 검토 후 별도 진행한다. text-only 기본 feature flag를 유지하고 기존 메시지에는 빈 attachment 목록으로 호환한다. 배포 순서, schema rollback과 blob orphan 정리, 보존 기간·quota·백업 만료를 함께 결정한다. 삭제된 파일의 추출문·검색 index·요약 cache·미리보기도 함께 회수하며 감사 metadata와 bytes의 보존 기간을 구분한다.

저장소는 webroot 밖 private blob storage를 기본으로 한다. DB에는 서버가 생성한 key만 보관하고 경로를 외부에 노출하지 않는다. 공개 URL이나 query token 대신 인증된 handler에서 현재 ACL을 검증해 전달한다. 실제 별도 origin·preview proxy·CSP·nosniff·attachment disposition·private cache 설정과 DB 설정 변경은 별도 검토 대상이다. 사람 계정 cookie를 그대로 외부 origin에 전달하지 않는다.

확장자·MIME 주장만 믿지 않고 실제 형식·parser 결과를 대조한다. 경로 순회, NUL·이중 확장자·NTFS ADS·symlink, 과대/chunked 업로드, 압축 폭탄과 decompression 확대를 거절한다. scanner/extractor는 egress와 실행·메모리·시간이 제한된 환경에서 동작해야 한다. 외부 링크/embedded resource를 따라가거나 shell/helper를 실행하지 않는다. 격리 검증 전에는 해당 parser/preview를 제공하지 않는다. 이 제안은 [OWASP File Upload 지침](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)의 다층 검증과 private 저장·권한 검증 원칙을 따른다.

## 첫 구현 단위와 관문

F0는 운영 API/DB와 분리된 내부 타입·메모리 blob store·fake scanner/extractor로 먼저 검증한다. upload → quarantine → 검사 결과 → attachment ref → 권한 있는 agent의 chunk 읽기 → 삭제 후 재조회 차단을 합성 자료로 한 번 연결한다. 실제 scanner 미실행 상태를 clean으로 바꾸는 기본값은 두지 않는다. fake의 통과 결과에는 synthetic provenance를 붙이고 실제 파일 안전 판정과 구분한다.

F0 성공 기준은 방/agent 간 유출 0건, pending/unknown 파일 노출 0건, 삭제·철회 후 검색/다운로드/preview/context 결과 0건, 명령/승인으로 실행되는 파일 내용 0건이다. 조작된 path/MIME, 용량/pixel/추출 상한, idempotency 충돌, 검사·추출 실패, chunk 경계 출처, 미지원 이미지 runtime, 외부 전송 미승인도 거절하는지 검증한다.

F1의 실제 private storage·인증된 streaming API와 F2의 scanner/parser/preview 출시는 각각 API/DB/저장소·보안 격리 계획 승인 후 수행한다. 실제 사용자 파일·계정·외부 저장소 생성이나 외부 모델 전송은 이 단계에 포함하지 않는다.

## English summary

Proposed only, separate from PR #8. Room attachments must become permissioned agent inputs through immutable message references and bounded task retrieval, not just downloads. Distinguish original bytes, extracted text and image capability. Preserve page/range citations and expose provider transfer scope. Pending/unknown scans never imply safety. Revalidate room/agent permissions and deletion for search, retrieval, preview and download. Begin with in-memory fake storage/scanners/extractors; review public API, DB migrations and private storage/isolation contracts before real integration.
