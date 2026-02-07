from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

from .models import CommMessage


class CommLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self._messages: list[CommMessage] = []
        self._external_ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    message = CommMessage(
                        message_id=str(payload.get("message_id", "")),
                        source=str(payload.get("source", "")),
                        role=str(payload.get("role", "")),
                        content=str(payload.get("content", "")),
                        timestamp=str(payload.get("timestamp", "")),
                        read=bool(payload.get("read", False)),
                        external_id=payload.get("external_id"),
                        session_id=payload.get("session_id"),
                    )
                    if message.external_id:
                        self._external_ids.add(str(message.external_id))
                    self._messages.append(message)
        except OSError:
            return

    def _append_line(self, message: CommMessage) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(message), ensure_ascii=True) + "\n")

    def _rewrite(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            for message in self._messages:
                handle.write(json.dumps(asdict(message), ensure_ascii=True) + "\n")

    def append(
        self,
        source: str,
        role: str,
        content: str,
        read: bool = False,
        external_id: str | None = None,
        session_id: str | None = None,
        timestamp: str | None = None,
    ) -> CommMessage | None:
        if external_id and external_id in self._external_ids:
            return None
        message = CommMessage(
            message_id=str(uuid4()),
            source=source,
            role=role,
            content=content,
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            read=read,
            external_id=external_id,
            session_id=session_id,
        )
        self._messages.append(message)
        if external_id:
            self._external_ids.add(external_id)
        self._append_line(message)
        return message

    def list_messages(self) -> list[CommMessage]:
        return list(self._messages)

    def recent_user_messages(self, limit: int = 3) -> list[CommMessage]:
        messages = [msg for msg in self._messages if msg.role == "user"]
        return messages[-limit:]

    def unread_user_messages(self) -> list[CommMessage]:
        return [msg for msg in self._messages if msg.role == "user" and not msg.read]

    def mark_read(self, message_ids: list[str]) -> None:
        updated = False
        for msg in self._messages:
            if msg.message_id in message_ids and not msg.read:
                msg.read = True
                updated = True
        if updated:
            self._rewrite()

    def history_text(self, session_id: str | None = None) -> str:
        lines = []
        for msg in self._messages:
            if session_id and msg.source == "jules" and msg.session_id != session_id:
                continue
            lines.append(f"{msg.timestamp} [{msg.source}:{msg.role}] {msg.content}")
        return "\n".join(lines)

    def snapshot(self) -> list[dict]:
        return [asdict(message) for message in self._messages]

    def purge_user_messages(self) -> int:
        before = len(self._messages)
        self._messages = [msg for msg in self._messages if msg.role != "user"]
        self._external_ids = {msg.external_id for msg in self._messages if msg.external_id}
        if len(self._messages) != before:
            self._rewrite()
        return before - len(self._messages)
