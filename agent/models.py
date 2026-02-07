from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class JulesStatus(str, Enum):
    IN_PROCESS = "inProcess"
    NEEDS_INPUT = "needsInput"
    READY_FOR_REVIEW = "readyForReview"
    UNKNOWN = "unknown"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass
class TraceEvent:
    kind: str
    message: str
    payload: dict[str, Any] | None = None
    timestamp: str | None = None


@dataclass
class ProjectState:
    docs_summary: str = ""
    codebase_summary: str = ""
    current_goals: str = ""
    last_jules_status: JulesStatus = JulesStatus.UNKNOWN
    requirements_met: bool = False


@dataclass
class CommMessage:
    message_id: str
    source: str
    role: str
    content: str
    timestamp: str
    read: bool = False
    external_id: str | None = None
    session_id: str | None = None


@dataclass
class LoopDecision:
    status: JulesStatus
    action: str


@dataclass
class JulesRequest:
    request_id: str
    content: str


@dataclass
class PrInfo:
    pr_id: str
    branch: str
    url: str | None = None
    title: str | None = None
    description: str | None = None


@dataclass
class ReviewResult:
    decision: ReviewDecision
    rationale: str
    raw: str = ""


@dataclass
class TraceBuffer:
    max_events: int = 500
    events: list[TraceEvent] = field(default_factory=list)
    hook: Callable[[TraceEvent], None] | None = None

    def add(self, event: TraceEvent) -> None:
        if event.timestamp is None:
            event.timestamp = datetime.now().astimezone().isoformat()
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events :]
        if self.hook:
            self.hook(event)

    def snapshot(self) -> list[TraceEvent]:
        return list(self.events)


@dataclass
class AgentTurnOutput:
    mess_out_user: str
    mess_out_jules: dict
    mess_out_log: str
