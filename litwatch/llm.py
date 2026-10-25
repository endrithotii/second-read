"""Model access. Two providers so the checker can come from a different model
family than the writer. Plain HTTPS, no SDKs, so the project has no
dependencies beyond the Python standard library."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Protocol


class Model(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> dict: ...


USER_AGENT = "second-read/0.1 (+https://github.com/endrithotii/second-read)"


class QuotaExhausted(RuntimeError):
    """The provider's daily budget is used up. Not a per-paper failure: the run stops."""


def _post(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    data = json.dumps(body).encode("utf-8")
    # Some providers sit behind Cloudflare, which refuses requests without a
    # named client (error 1010), so every request identifies itself.
    headers = {"user-agent": USER_AGENT, "accept": "application/json", **headers}
    last_error: Exception | None = None
    # Free tiers limit tokens per minute, so a 429 is normal: wait as long as
    # the provider asks, for up to about ten minutes in total.
    for attempt in range(12):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        wait = min(2 ** attempt, 60)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_error = e
            # Retry rate limits and server errors; give up on anything else.
            if e.code not in (408, 429, 500, 502, 503, 504, 529):
                detail = e.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"{url} returned {e.code}: {detail}") from e
            retry_after = e.headers.get("retry-after")
            if retry_after:
                try:
                    asked = float(retry_after)
                except ValueError:
                    asked = 0
                if asked > 300:
                    # A long wait means a daily budget is used up, not a busy minute:
                    # stop the run; the cache keeps what is done for the next run.
                    detail = e.read().decode("utf-8", "replace")[:300]
                    raise QuotaExhausted(f"Daily limit reached, try again in {asked / 60:.0f} minutes. {detail}")
                wait = min(asked + 1, 90) if asked else wait
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
        reason = getattr(last_error, "code", None) or type(last_error).__name__
        print(f"    waiting {wait:.0f}s before retrying ({reason}: {str(last_error)[:120]})",
              file=sys.stderr, flush=True)
        time.sleep(wait)
    raise RuntimeError(f"{url} failed after retries: {last_error}")


def parse_json(text: str) -> dict:
    """Read a JSON object from model output, tolerating code fences or prose around it."""
    # Reasoning models may think out loud before answering; only the answer counts.
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


class AnthropicModel:
    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.name = f"anthropic/{model}"
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def complete_json(self, system: str, user: str) -> dict:
        out = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": self.model, "max_tokens": 2000, "temperature": 0,
             "system": system, "messages": [{"role": "user", "content": user}]},
        )
        text = "".join(b.get("text", "") for b in out.get("content", []))
        return parse_json(text)


# Providers that speak the OpenAI chat-completions format: base URL and the
# environment variable holding the key (None means no key is needed).
COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "ollama": (os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), None),
}


class CompatibleModel:
    def __init__(self, provider: str, model: str):
        base_url, key_env = COMPATIBLE[provider]
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.name = f"{provider}/{model}"
        self.api_key = os.environ.get(key_env) if key_env else "local"
        if not self.api_key:
            raise RuntimeError(f"{key_env} is not set")
        self.json_mode = True

    def complete_json(self, system: str, user: str) -> dict:
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        for attempt in range(3):
            try:
                out = _post(self.url, headers, body, timeout=300)
                return parse_json(out["choices"][0]["message"]["content"])
            except QuotaExhausted:
                raise
            except (RuntimeError, ValueError) as e:
                text = str(e)
                if "response_format" in text and "json_validate_failed" not in text:
                    # The provider does not offer JSON mode for this model at all:
                    # the prompts already ask for JSON, so stop sending it.
                    self.json_mode = False
                    body.pop("response_format", None)
                elif "json_validate_failed" in text or isinstance(e, ValueError):
                    # JSON mode rejected the model's own output, or the reply was not
                    # valid JSON: ask once more, then once without JSON mode.
                    if attempt == 1:
                        body.pop("response_format", None)
                else:
                    raise
        raise RuntimeError(f"{self.name} gave no valid JSON after 3 attempts")


class LocalModel:
    """A GGUF model file run in-process with llama.cpp (pip install llama-cpp-python).
    Free and offline. Small models are slower and less accurate than hosted ones."""

    _lock = None

    def __init__(self, path: str):
        import threading
        from pathlib import Path
        from llama_cpp import Llama  # optional dependency, only needed for local models
        self.name = f"local/{Path(path).stem}"
        self.llm = Llama(model_path=path, n_ctx=8192, n_threads=os.cpu_count(), verbose=False)
        self._lock = threading.Lock()  # one llama.cpp context is not thread-safe

    def complete_json(self, system: str, user: str) -> dict:
        with self._lock:
            out = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"}, temperature=0, max_tokens=1200)
        return parse_json(out["choices"][0]["message"]["content"])


def family(spec: str) -> str:
    """Who trained the model, which is what matters for independent checking.
    'groq:openai/gpt-oss-120b' -> 'openai'; 'anthropic:claude-...' -> 'anthropic'."""
    provider, _, model = spec.partition(":")
    if "/" in model:
        return model.split("/", 1)[0].lower()
    m = model.lower()
    for name, keys in {"meta": ("llama",), "alibaba": ("qwen",), "google": ("gemini", "gemma"),
                       "openai": ("gpt",), "anthropic": ("claude",), "mistral": ("mistral", "mixtral")}.items():
        if any(m.startswith(k) for k in keys):
            return name
    return provider


def make_model(spec: str) -> Model:
    """'<provider>:<model>', provider one of anthropic, openai, gemini, groq, ollama."""
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"model spec must look like 'provider:model', got {spec!r}")
    if provider == "anthropic":
        return AnthropicModel(model)
    if provider == "local":
        return LocalModel(model)
    if provider in COMPATIBLE:
        return CompatibleModel(provider, model)
    raise ValueError(f"unknown provider {provider!r}; use one of anthropic, {', '.join(COMPATIBLE)}")
