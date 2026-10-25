"""Provider registry: every way an agent can be attached to a room.

Two transports exist:

* ``cli``  - a local executable the worker supervises (Codex, Claude Code, Grok CLI).
* ``http`` - an OpenAI-compatible chat-completions endpoint. This one adapter
  covers DeepSeek, xAI Grok, OpenRouter, Groq, Together, Mistral, OpenAI,
  Ollama, LM Studio, vLLM and any other compatible gateway.

The registry is metadata only. Runtime construction lives in ``http_runtime``
and ``agent_runtime``; the server never trusts a provider name for evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    transport: str
    status: str
    key_env: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    note: str = ""
    aliases: tuple[str, ...] = field(default=())

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "transport": self.transport,
            "status": self.status,
            "key_env": self.key_env,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "note": self.note,
            "aliases": list(self.aliases),
        }


PROVIDERS: dict[str, Provider] = {
    # --- supervised local CLIs ------------------------------------------------
    "codex": Provider(
        "codex", "ChatGPT · Codex CLI", "cli", "ready",
        key_env="OPENAI_API_KEY", note="exec JSONL, text-only profile",
    ),
    "claude": Provider(
        "claude", "Claude · Claude Code CLI", "cli", "ready",
        key_env="ANTHROPIC_API_KEY", note="stream-json, text-only profile",
    ),
    "grok": Provider(
        "grok", "Grok · Grok CLI", "cli", "gated",
        key_env="XAI_API_KEY", note="ACP; native login and workspace isolation remain gated",
    ),
    # --- OpenAI-compatible HTTP endpoints -------------------------------------
    "deepseek": Provider(
        "deepseek", "DeepSeek", "http", "ready",
        key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat", note="OpenAI-compatible chat completions",
    ),
    "xai": Provider(
        "xai", "Grok · xAI API", "http", "ready",
        key_env="XAI_API_KEY", base_url="https://api.x.ai/v1",
        default_model="grok-3-mini", note="API path for Grok; the CLI path stays gated",
    ),
    "openrouter": Provider(
        "openrouter", "OpenRouter", "http", "ready",
        key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1",
        default_model=None, note="model is required, e.g. anthropic/claude-sonnet-4",
    ),
    "groq": Provider(
        "groq", "Groq Cloud", "http", "ready",
        key_env="GROQ_API_KEY", base_url="https://api.groq.com/openai/v1",
        default_model=None, note="model is required",
    ),
    "together": Provider(
        "together", "Together AI", "http", "ready",
        key_env="TOGETHER_API_KEY", base_url="https://api.together.xyz/v1",
        default_model=None, note="model is required",
    ),
    "mistral": Provider(
        "mistral", "Mistral", "http", "ready",
        key_env="MISTRAL_API_KEY", base_url="https://api.mistral.ai/v1",
        default_model="mistral-large-latest", note="",
    ),
    "openai": Provider(
        "openai", "OpenAI API", "http", "ready",
        key_env="OPENAI_API_KEY", base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini", note="direct API; Codex CLI is a separate runtime",
    ),
    "ollama": Provider(
        "ollama", "Ollama (local)", "http", "ready",
        key_env=None, base_url="http://127.0.0.1:11434/v1",
        default_model="llama3.2", note="local; no key required",
    ),
    "lmstudio": Provider(
        "lmstudio", "LM Studio (local)", "http", "ready",
        key_env=None, base_url="http://127.0.0.1:1234/v1",
        default_model=None, note="local; no key required; model is required",
    ),
    "compatible": Provider(
        "compatible", "OpenAI-compatible endpoint", "http", "ready",
        key_env="PROVIDER_API_KEY", base_url=None,
        default_model=None, note="requires --provider-url; vLLM, LiteLLM, gateways",
    ),
}

ALIASES = {
    "chatgpt": "codex",
    "codex-cli": "codex",
    "claude-code": "claude",
    "anthropic": "claude",
    "grok-cli": "grok",
    "grok-api": "xai",
    "open-router": "openrouter",
    "groq-cloud": "groq",
    "lm-studio": "lmstudio",
    "openai-compatible": "compatible",
}


def resolve(kind: str) -> str | None:
    """Return a canonical provider id or None."""
    if not isinstance(kind, str):
        return None
    key = kind.strip().lower()
    key = ALIASES.get(key, key)
    return key if key in PROVIDERS else None


def get(kind: str) -> Provider | None:
    canonical = resolve(kind)
    return PROVIDERS.get(canonical) if canonical else None


def cli_ids() -> list[str]:
    return [pid for pid, provider in PROVIDERS.items() if provider.transport == "cli"]


def http_ids() -> list[str]:
    return [pid for pid, provider in PROVIDERS.items() if provider.transport == "http"]


def catalog() -> list[dict]:
    return [provider.public() for provider in PROVIDERS.values()]
