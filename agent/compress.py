from __future__ import annotations

from typing import Iterable
import json

from .config import CompressionConfig
from .llm import LlmClient
from .models import TraceBuffer, TraceEvent


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def chunk_text(text: str, max_tokens: int) -> list[str]:
    if not text:
        return []
    approx_chars = max_tokens * 4
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + approx_chars)
        chunks.append(text[start:end])
        start = end
    return chunks


class CompressionPipeline:
    def __init__(
        self,
        config: CompressionConfig,
        llm: LlmClient,
        trace: TraceBuffer | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.trace = trace

    def _record(self, kind: str, message: str, payload: dict | None = None) -> None:
        if self.trace is None:
            return
        self.trace.add(TraceEvent(kind=kind, message=message, payload=payload))

    def compress(self, text: str, target_total_tokens: int | None = None) -> str:
        if not text:
            return ""
        target_total = target_total_tokens or self.config.target_total_tokens
        current = text
        self._record("compress_start", "Starting compression", {"tokens": estimate_tokens(text)})
        while estimate_tokens(current) > target_total:
            chunks = chunk_text(current, self.config.max_input_tokens)
            summaries = []
            for idx, chunk in enumerate(chunks):
                self._record(
                    "compress_chunk",
                    "Summarizing chunk",
                    {"index": idx, "tokens": estimate_tokens(chunk)},
                )
                summaries.append(self.llm.summarize(chunk, self.config.target_chunk_tokens))
            current = "\n\n".join(summaries)
            self._record("compress_pass", "Compression pass completed", {"tokens": estimate_tokens(current)})
            if len(chunks) == 1:
                break
        self._record("compress_done", "Compression finished", {"tokens": estimate_tokens(current)})
        return current

    def compress_many(self, texts: Iterable[str], target_total_tokens: int | None = None) -> str:
        combined = "\n\n".join(texts)
        return self.compress(combined, target_total_tokens=target_total_tokens)

    def summarize_comm_history(self, history_text: str, target_tokens: int = 1000) -> str:
        if not history_text:
            return ""
        system_prompt = (
            "Summarize the communication history. Preserve decisions, open questions, and action items. "
            "Be concise and avoid repetition."
        )
        user_prompt = f"Target length: ~{target_tokens} tokens.\n\nHistory:\n{history_text}"
        return self.llm.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

    def update_goals_plans(self, current_text: str, events_text: str, target_tokens: int = 1000) -> str:
        system_prompt = (
            "Maintain the goals and plan for the project manager. "
            "Only change the goals or plan if the events indicate a goal was reached, failed, or needs revision. "
            "Do not include internal maintenance steps like compression or summarization in goals or plans."
        )
        user_prompt = (
            f"Target length: ~{target_tokens} tokens.\n\nCurrent goals and plan:\n{current_text}\n\n"
            f"New events:\n{events_text}\n\n"
            "Return updated goals and plan."
        )
        return self.llm.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

    def update_rolling_context(self, previous_text: str, events_text: str, target_tokens: int = 1200) -> str:
        system_prompt = (
            "Update the rolling context for a project manager. "
            "Preserve the most important facts, decisions, and current state. "
            "Fold in new events, remove stale details, and keep it concise."
        )
        user_prompt = (
            f"Target length: ~{target_tokens} tokens.\n\nPrevious rolling context:\n{previous_text}\n\n"
            f"New events:\n{events_text}\n\n"
            "Return the updated rolling context."
        )
        return self.llm.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

    def format_unread_response(self, messages_text: str, target_tokens: int = 300) -> str:
        system_prompt = (
            "You are LocalProjectManager. Respond to unread user messages. "
            "Use this format:\n"
            "Response:\n"
            "- Summary: <short summary>\n"
            "- Actions:\n"
            "  - <action 1>\n"
            "  - <action 2>\n"
            "- Questions:\n"
            "  - <question 1>\n"
            "Keep it concise and actionable."
        )
        user_prompt = f"Target length: ~{target_tokens} tokens.\n\nUnread messages:\n{messages_text}"
        return self.llm.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

    def agent_turn_output(self, turn_inputs: dict, unread_messages_text: str) -> str:
        inputs_json = json.dumps(turn_inputs, ensure_ascii=True)
        system_prompt = (
            "You are LocalProjectManager. Produce a strict JSON object only, with keys: "
            "mess_out_USER, mess_out_JULES, mess_out_LOG. "
            "mess_out_USER is a concise user-facing message (empty if no unread messages). "
            "mess_out_JULES is an object with fields: action (sendMessage|startSession|none) "
            "and payload (object). If no action, use action=\"none\" and empty payload. "
            "mess_out_LOG is a concise log message describing the last action taken; it must be non-empty. "
            "Output JSON only, no extra text."
        )
        user_prompt = (
            "Inputs (JSON):\n"
            f"{inputs_json}\n\n"
            "Unread user messages:\n"
            f"{unread_messages_text}\n\n"
            "Return the JSON object."
        )
        return self.llm.chat_complete_streaming(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
