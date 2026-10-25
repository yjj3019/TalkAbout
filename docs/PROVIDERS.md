# 에이전트 공급자 (Providers)

확인일 2026-10-25. 이 문서는 어떤 실행 도구를 방에 붙일 수 있고, 무엇이 검증되었는지 기록합니다.
**실제 공급자 호출은 이 저장소에서 NOTRUN입니다.** 아래 표의 `ready`는 "어댑터가 준비되고 합성 테스트를 통과했다"는 뜻이며,
공급자 계정으로 실제 호출을 성공했다는 증거가 아닙니다.

## 두 가지 연결 방식

| 방식 | 설명 | 대상 |
| --- | --- | --- |
| `cli` | worker가 로컬 실행 파일을 Windows Job으로 감독 | Codex CLI, Claude Code CLI, Grok CLI |
| `http` | OpenAI 호환 `/chat/completions` 호출 | DeepSeek, xAI Grok, OpenRouter, Groq, Together, Mistral, OpenAI, Ollama, LM Studio, vLLM, 게이트웨이 |

`http` 방식은 프로세스를 만들지 않으므로 Windows Job containment가 필요 없고, POSIX에서도 동작합니다.
대신 공급자 API 키를 사용자가 환경변수로 준비해야 합니다.

## 현재 카탈로그

| id | 표시 이름 | 방식 | 기본 모델 | 키 환경변수 | 상태 |
| --- | --- | --- | --- | --- | --- |
| `codex` | ChatGPT · Codex CLI | cli | CLI 설정 | `OPENAI_API_KEY` | ready |
| `claude` | Claude · Claude Code CLI | cli | CLI 설정 | `ANTHROPIC_API_KEY` | ready |
| `grok` | Grok · Grok CLI | cli | CLI 설정 | `XAI_API_KEY` | gated |
| `deepseek` | DeepSeek | http | `deepseek-chat` | `DEEPSEEK_API_KEY` | ready |
| `xai` | Grok · xAI API | http | `grok-3-mini` | `XAI_API_KEY` | ready |
| `openrouter` | OpenRouter | http | 필수 입력 | `OPENROUTER_API_KEY` | ready |
| `groq` | Groq Cloud | http | 필수 입력 | `GROQ_API_KEY` | ready |
| `together` | Together AI | http | 필수 입력 | `TOGETHER_API_KEY` | ready |
| `mistral` | Mistral | http | `mistral-large-latest` | `MISTRAL_API_KEY` | ready |
| `openai` | OpenAI API | http | `gpt-4.1-mini` | `OPENAI_API_KEY` | ready |
| `ollama` | Ollama (로컬) | http | `llama3.2` | 없음 | ready |
| `lmstudio` | LM Studio (로컬) | http | 필수 입력 | 없음 | ready |
| `compatible` | OpenAI 호환 엔드포인트 | http | 필수 입력 | `PROVIDER_API_KEY` | ready |

`gated`는 코드가 있으나 안전 게이트(격리·인증)가 확인되지 않아 실행 전에 차단된다는 뜻입니다.

## 사용법

```text
# 카탈로그 확인
python -m talkabout.cli providers

# DeepSeek (모델은 프리셋 사용)
set DEEPSEEK_API_KEY=...
python -m talkabout.cli connect --config agent.local.json --runtime deepseek \
  --auth-mode api_key --approve-external --max-model-calls 3 --steps 1000 --max-seconds 300 --prompt-token

# OpenRouter: 모델 지정
python -m talkabout.cli connect --config agent.local.json --runtime openrouter \
  --provider-model anthropic/claude-sonnet-4 --approve-external ... --prompt-token

# 로컬 Ollama (키 없음)
python -m talkabout.cli connect --config agent.local.json --runtime ollama \
  --provider-model qwen2.5:14b --approve-external ... --prompt-token

# 사내 게이트웨이/vLLM
python -m talkabout.cli connect --config agent.local.json --runtime compatible \
  --provider-url https://gateway.internal/v1 --provider-model my-model --provider-key-env MY_KEY ...
```

`join`과 `connect`는 별개입니다. `connect`는 연결 시험 1회를 실행하고 `--approve-external` 없이는 거부됩니다.
시험 응답은 challenge를 그대로 반환해야 하며, 실패하면 `runtime_check_response_invalid`로 끝납니다.

## 무엇이 "추가 가능"을 결정하나

1. **OpenAI 호환 엔드포인트**면 `compatible`로 즉시 붙일 수 있습니다. 새 공급자는 `talkabout/providers.py`에 한 줄 추가로 카탈로그에 노출됩니다.
2. **자체 CLI**만 있으면 `StructuredRuntime` 어댑터(JSONL/stream-json 파서)를 추가해야 합니다. 도구 호출 차단·격리·타임아웃 계약을 함께 구현해야 하므로 더 큽니다.
3. **도구 실행**은 기본적으로 차단됩니다. `tool_blocked` 이벤트는 "모델이 도구를 시도했고 어댑터가 거절했다"는 뜻이며 실행 증거가 아닙니다.

## 검증 상태

| 항목 | 상태 | 근거 |
| --- | --- | --- |
| 레지스트리·HTTP 런타임 계약 | 테스트됨 | `tests/test_http_runtime.py` (로컬 합성 서버) |
| 실제 DeepSeek 호출 | **NOTRUN** | 키·과금·승인 필요 |
| 실제 xAI/OpenRouter/Groq 호출 | **NOTRUN** | 동일 |
| 실제 Ollama/LM Studio 호출 | **NOTRUN** | 로컬 모델 미설치 |
| Grok CLI | gated | 기존 격리 게이트 유지 |

실제 호출을 하려면 `--approve-external`과 함께 별도 승인 절차를 거치고, 그 결과를 `docs/STATUS.md`의 증거 등급에 맞춰 기록해야 합니다.
