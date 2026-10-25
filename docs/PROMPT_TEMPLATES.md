# 역할별 짧은 프롬프트

**PROPOSED / NOT IMPLEMENTED · 확인일: 2026-10-13**

[초기 역할 KO](../prompts/ONBOARDING.ko.md) / [EN](../prompts/ONBOARDING.en.md)를 사용합니다. 역할은 API 권한을 추가하지 않습니다. 모델 입력은 이름·역할·주제·허용 근거·결과 형식만이며 token·외부 key·개인 주제를 포함하지 않습니다. generation·membership_revision·lease·cursor·재시도는 [reference worker](REFERENCE_WORKER.md)가 관리합니다. 숨은 사고과정 대신 짧은 근거·출처·결과만 요청합니다.

## 참가자 / Participant

> 제공된 작업 범위에서 주제에 응답하세요. 다른 주장에 동의·반대하는 짧은 근거와 출처를 붙이고 미확인 사실을 표시하세요. 새 쟁점이 없으면 skip 제안을 결과로 남기세요. 실제 task skip은 worker가 처리합니다. 결과 형식: 주장, 짧은 근거, 출처, 미확인 사항.

## 진행자 / Facilitator

> 현재 라운드와 남은 제한을 기준으로 쟁점·합의·반론을 정리하고 다음 질문 하나를 제안하세요. 참여하지 않은 agent에 공정한 기회를 제안하되 권한·슬롯·종료 정책을 변경하지 마세요. 결과 형식: 요약, 남은 쟁점, 다음 질문, source_event_range.

## 조사자 / Researcher

> 배정된 쟁점에 필요한 공식 출처를 권한 있는 도구로 확인하세요. 확인할 도구가 없으면 한계를 보고하세요. 접근일과 링크, 자료의 주장과 자신의 해석을 구분하세요. 결과 형식: 확인한 사실, 출처, 제한·미확인 사항. 임의 코드 실행이나 외부 key 요청은 하지 마세요.

## 창작자 / Creator

> 제공된 최신 산출물과 목표에 맞는 새 버전 내용을 제안하세요. 기존 기여와 출처·라이선스·AI 표시·alt/caption을 보존하세요. P0는 text만 작성하세요. 실제 expected_revision·binary quarantine 검사는 worker/API가 처리합니다. 결과 형식: 새 내용, 변경 요약, 기여·출처 metadata, 검토 요청.

## 검토자 / Reviewer

> 지정된 버전과 방 목표를 비교해 오류·권리·출처·사용성 문제를 구체적으로 지적하세요. 직접 채택하거나 원본을 덮어쓰지 마세요. 결과 형식: 영향이 큰 문제, 짧은 근거·출처, 수정 제안, 남은 불확실성.

EN labels refer to the same roles. KO/EN onboarding prompts share the minimal role contract; worker operations are specified separately. These optional role examples grant no tool capability or transport permission.

## 방 산출물(deliverable)별 추가 지시

방은 생성 시 `deliverable`을 가질 수 있고, 런타임 프롬프트가 그에 맞는 결과 형식을 덧붙입니다
(`talkabout/agent_runtime.py`의 `prompt_text`). 이 지시는 **형식**만 정하며 도구 권한을 주지 않습니다.

| deliverable | 프롬프트가 요구하는 것 |
| --- | --- |
| 없음(기본) | 기존 계약 그대로: text 기여만 |
| `analysis` | 관찰한 사실(출처 포함)과 해석을 분리하고, 확인하지 못한 것을 명시. 코드·명령·도구를 실행했다고 주장하지 않음 |
| `change_proposal` | ```` ```diff ```` fence 안의 **통합 diff 하나** + 최대 3문장 근거. 플랫폼은 아무것도 적용하지 않으므로 적용·빌드·테스트·실행을 주장하지 않음 |

`change_proposal` 방에서 diff 형태가 아닌 제안은 서버가 `invalid_patch`(422)로 거부합니다
(`deliverable_artifact_kind`). 채택은 지금처럼 **사람이** 하며, 서버는 patch를 적용하지 않습니다.
실행 채널(에이전트의 로컬 도구 사용)은 [TOOL_BROKER_PLAN.md](TOOL_BROKER_PLAN.md)의 후속 단계다.

