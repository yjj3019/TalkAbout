# 공유 입력 컨텍스트 계약 / Shared input context

**IMPLEMENTED · 확인일: 2026-10-26**

에이전트에게 무엇을 보냈는지 서버가 **"보냈다"가 아니라 "그 참조가 실재하고, 이 방·이 열람자 범위에
속하며, 바이트 범위가 맞는다"** 는 데까지 확인한다. 모델이 읽었다는 증거는 아니다.

## 1. 구성

`talkabout/collaboration.py`가 worker와 core 양쪽에서 쓰인다.

| 함수 | 하는 일 |
| --- | --- |
| `shared_context(room, task)` | 보낼 입력을 **우선순위**(고정 메시지 → 최신 메시지, 채택 버전 → 최신 버전)로 고르고, UTF-8 경계에서 자르고, `input_manifest` 생성 |
| `add_file_sources(context, excerpts)` | 첨부에서 읽은 발췌와 **읽지 않은 나머지 개수**를 manifest에 기록 |
| `fit_context(context)` | 실제 직렬화 크기가 `MAX_INPUT`을 넘으면 큰 텍스트부터 절반씩 줄임. 메타데이터는 버리지 않고, 끝까지 안 되면 dispatch 전에 실패 |
| `validate_manifest(manifest, room, task)` | 서버가 참조를 재검증. 통과한 것만 `worker_reported_server_validated_refs_not_model_read_receipt`로 기록 |
| `project_manifest` / `project_task` | 열람자의 history 경계 밖 참조를 제거하고 `redacted_source_count`로 남김 |

## 2. 한도

| 항목 | 값 |
| --- | --- |
| 공유 텍스트 예산 | 72,000 bytes (버전별 최대 12,000 · 메시지별 최대 6,000) |
| 메시지 | 최대 8건 |
| artifact 버전 | 최대 4건 |
| 첨부 발췌 | 최대 4건 |
| 직렬화 프롬프트 | `agent_runtime.MAX_INPUT`(200,000 bytes) 이하 |

## 3. manifest

```json
{
  "snapshot_seq": 42,
  "event_range": [7, 42],
  "sources": [
    {"kind": "message", "room_id": "...", "message_id": "...", "actor_id": "...", "seq": 7,
     "utf8_start": 0, "utf8_end": 512, "total_utf8_bytes": 512, "truncated": false,
     "source_id": "talkabout:room:<room>:message:<id>"},
    {"kind": "artifact_version", "artifact_id": "...", "version_id": "...", "revision": 3,
     "seq": 21, "source_id": "talkabout:room:<room>:artifact:<id>:version:<id>"},
    {"kind": "file_excerpt", "ref": {...}, "start": 0, "end": 2000, "truncated": true,
     "source_id": "talkabout:room:<room>:file:<file>:version:<version>"}
  ],
  "summary": {"status": "not_generated", "covered_event_range": null},
  "omissions": {"messages": {"count": 4, "seq_range": [1, 4]},
                "artifact_versions": {"count": 0, "seq_range": null},
                "files": {"count": 1}},
  "evidence": "prepared_worker_input_not_model_read_receipt"
}
```

- `summary.status`는 항상 `not_generated`다. 요약은 만들지 않는다.
- `omissions`는 **빠진 개수와 seq 범위**를 숨기지 않고 기록한다.
- 모든 보낸 항목은 `untrusted_data: true`다.

## 4. 서버 검증 규칙

`validate_manifest`는 다음을 거절한다(`422`).

| 조건 | 코드 |
| --- | --- |
| manifest가 dict가 아니거나 `snapshot_seq`가 정수가 아니거나 방 범위를 벗어남 | `invalid_input_manifest` |
| source 개수 상한 초과 | `invalid_input_manifest` |
| 다른 방의 참조 | `invalid_input_source` |
| 존재하지 않는 메시지·버전 | `invalid_input_source` |
| `seq`·`actor_id`·`revision` 불일치 | `invalid_input_source` |
| UTF-8 경계(`utf8_end`)·`truncated` 표시가 실제 텍스트와 불일치 | `invalid_input_source` |
| `source_id` 위조 또는 중복 | `invalid_input_source` |
| 첨부 참조가 이 task의 `attachment_refs`에 없거나, 서버가 기록한 읽기 범위를 벗어남 | `invalid_input_source` |
| `review_target`이 입력에서 빠짐 | `review_target_missing_from_input` |

manifest를 **생략한 구형 worker**는 거절하지 않는다. 대신 `legacy_input_unverified`로 기록한다.
검증 실패 시 방 상태는 바뀌지 않는다(테스트로 고정).

## 5. 기록되는 증거

| 위치 | 값 |
| --- | --- |
| task `input_manifest` | 검증된 참조 목록(`snapshot_seq`, `sources`) + evidence 라벨 |
| 메시지 `input_evidence` | 같은 라벨 |
| artifact version `input_references` | 검증된 참조 + 원본 manifest |
| artifact version `sources` | `source_id` 목록 |

라벨은 두 가지이며, **모델이 읽었다는 뜻이 아니다**.

- `prepared_worker_input_not_model_read_receipt` — worker가 준비한 입력
- `worker_reported_server_validated_refs_not_model_read_receipt` — 서버가 참조를 재검증함

## 6. 주장하지 않는 것

- 모델이 입력을 **읽었거나 이해했다**는 증거가 아니다.
- 회수(revocation) 이전에 이미 전달된 바이트를 되돌릴 수 없다.
- 첨부 본문은 파일 단위 ACL·동의(`agent_read`)로만 전달되며, 이 모듈은 파일을 직접 읽지 않는다.

## 7. 보류: runner 예약/원장

같은 WIP 라인에 있던 **runner 예약(`runner/reservations`)·원장(`RunnerLedger`)·DPAPI vault·loopback
bridge**는 이번에 구현하지 않았다. DB 스키마와 공개 API를 바꾸는 별도 트랙이며,
[STATUS.md](STATUS.md) §4에 남아 있다.
