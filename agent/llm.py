from __future__ import annotations

import json
from typing import Iterable
import requests

from .config import LlmConfig
from .models import TraceBuffer, TraceEvent


class LlmClient:
    def __init__(self, config: LlmConfig, trace: TraceBuffer | None = None) -> None:
        self.config = config
        self.trace = trace

    def _record(self, kind: str, message: str, payload: dict | None = None) -> None:
        if self.trace is None:
            return
        self.trace.add(TraceEvent(kind=kind, message=message, payload=payload))

    def chat_complete(self, messages: list[dict]) -> str:
        url = f"{self.config.base_url.rstrip('/')}/v1/chat/completions"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        self._record("llm_request", "Sending chat completion", {"url": url, "body": body})
        response = requests.post(
            url,
            json=body,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        self._record("llm_response", "Chat completion received", {"response": payload})
        return payload["choices"][0]["message"]["content"]

    def stream_chat_complete(self, messages: list[dict]) -> Iterable[str]:
        url = f"{self.config.base_url.rstrip('/')}/v1/chat/completions"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        self._record("llm_request", "Sending streaming chat completion", {"url": url, "body": body})
        with requests.post(
            url,
            json=body,
            stream=True,
            timeout=self.config.timeout_seconds,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[len("data: ") :]
                if line.strip() == "[DONE]":
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta_payload = payload.get("choices", [{}])[0].get("delta", {})
                delta = delta_payload.get("content")
                channel = "content"
                if not delta:
                    delta = delta_payload.get("reasoning_content") or delta_payload.get("reasoning")
                    channel = "reasoning"
                if delta:
                    self._record("llm_stream", "Streaming delta", {"delta": delta, "channel": channel})
                    yield delta

    def chat_complete_streaming(self, messages: list[dict]) -> str:
        chunks: list[str] = []
        for delta in self.stream_chat_complete(messages):
            chunks.append(delta)
        return "".join(chunks)

    def summarize(self, text: str, target_tokens: int) -> str:
        system_prompt = (
            "Summarize the input faithfully and concisely. "
            "Preserve decisions, requirements, and next actions."
        )
        user_prompt = (
            f"Target length: ~{target_tokens} tokens.\n\n"
            f"Input:\n{text}"
        )
        return self.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
