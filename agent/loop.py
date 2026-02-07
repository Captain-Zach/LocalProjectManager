from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from .comm_log import CommLog
from .compress import CompressionPipeline
from .config import AgentConfig
from .jules_client import JulesClient
from .llm import LlmClient
from .models import (
    AgentTurnOutput,
    JulesStatus,
    ProjectState,
    ReviewDecision,
    ReviewResult,
    TraceBuffer,
    TraceEvent,
)
from .repo_manager import RepoManager


class SharedState:
    def __init__(self) -> None:
        self.interrupts: list[str] = []
        self.current_context: str = ""
        self.stop_requested: bool = False
        self.selected_source: str | None = None
        self.session_id: str | None = None
        self.system_message: str = ""
        self.system_message_locked: bool = False
        self.comm_channel: str = ""
        self.goals_plans: str = ""
        self.rolling_context: str = ""
        self.last_unread_response: str = ""
        self.llm_ready: bool = False
        self.last_agent_output: AgentTurnOutput | None = None
        self.no_jules_sessions: bool = False

    def add_interrupt(self, message: str) -> None:
        self.interrupts.append(message)
        if message.strip().upper() == "__STOP__":
            self.stop_requested = True

    def build_interrupt_block(self) -> str:
        if not self.interrupts:
            return ""
        return "User interrupts:\n" + "\n".join(f"- {msg}" for msg in self.interrupts)


class AgentLoop:
    def __init__(
        self,
        config: AgentConfig,
        llm: LlmClient,
        compression: CompressionPipeline,
        jules: JulesClient,
        repo: RepoManager,
        trace: TraceBuffer,
        shared_state: SharedState,
        comm_log: CommLog,
    ) -> None:
        self.config = config
        self.llm = llm
        self.compression = compression
        self.jules = jules
        self.repo = repo
        self.trace = trace
        self.shared_state = shared_state
        self.comm_log = comm_log
        self.state = ProjectState()
        self.initialized = False

    def _record(self, kind: str, message: str, payload: dict | None = None) -> None:
        timestamp = datetime.now().astimezone().isoformat()
        self.trace.add(TraceEvent(kind=kind, message=message, payload=payload, timestamp=timestamp))

    def _load_docs(self) -> str:
        texts = []
        docs_path = os.path.join(self.config.repo.repo_path, self.config.docs.docs_path)
        if os.path.isdir(docs_path):
            for root, _, files in os.walk(docs_path):
                for name in files:
                    if not name.lower().endswith((".md", ".txt")):
                        continue
                    full_path = os.path.join(root, name)
                    try:
                        with open(full_path, "r", encoding="utf-8") as handle:
                            rel = os.path.relpath(full_path, self.config.repo.repo_path)
                            texts.append(f"File: {rel}\n{handle.read()}")
                    except OSError:
                        continue
        if self.config.docs.include_readme:
            readme_path = os.path.join(self.config.repo.repo_path, "README.md")
            if os.path.isfile(readme_path):
                with open(readme_path, "r", encoding="utf-8") as handle:
                    texts.append(f"File: README.md\n{handle.read()}")
        return "\n\n".join(texts)

    def _build_context(self, extra: str = "") -> str:
        blocks = [
            "System Message:\n" + self.shared_state.system_message,
            "Communication Channel:\n" + self.shared_state.comm_channel,
            "Goals and Plans:\n" + self.shared_state.goals_plans,
            "Rolling Context:\n" + self.shared_state.rolling_context,
        ]
        if extra:
            blocks.append(extra)
        interrupt_block = self.shared_state.build_interrupt_block()
        if interrupt_block:
            blocks.append(interrupt_block)
        context = "\n\n".join(blocks)
        self.shared_state.current_context = context
        return context

    def _sync_jules_messages(self) -> None:
        if not self.shared_state.session_id and self.shared_state.selected_source:
            self.shared_state.session_id = self.jules.resolve_session_id_for_source(
                self.shared_state.selected_source
            )
            if not self.shared_state.session_id:
                self.shared_state.no_jules_sessions = True
                return
        if not self.shared_state.session_id:
            return
        self.shared_state.no_jules_sessions = False
        try:
            activities = self.jules.list_recent_messages(self.shared_state.session_id, limit=30)
        except Exception as exc:
            self._record("comm_log", "Failed to fetch Jules activities", {"error": str(exc)})
            return
        for activity in activities:
            content = activity.get("content", "")
            if not content:
                continue
            self.comm_log.append(
                source="jules",
                role=activity.get("role", "agent"),
                content=content,
                read=False,
                external_id=activity.get("id"),
                timestamp=activity.get("timestamp"),
                session_id=self.shared_state.session_id,
            )

    def _build_comm_channel(self) -> None:
        history_text = "" if self.shared_state.no_jules_sessions else self.comm_log.history_text(
            session_id=self.shared_state.session_id
        )
        summary = ""
        if self.shared_state.no_jules_sessions:
            summary = "No Jules coding sessions have been done on this project yet."
        elif history_text and self.shared_state.llm_ready:
            summary = self.compression.summarize_comm_history(history_text, target_tokens=1000)
        elif history_text:
            summary = "Summary pending; LLM not ready."
        recent_messages = [] if self.shared_state.no_jules_sessions else self.comm_log.recent_user_messages(3)
        recent_lines = []
        for msg in recent_messages:
            status = "read" if msg.read else "unread"
            recent_lines.append(f"- ({status}) {msg.timestamp} {msg.content}")
        jules_messages = []
        if not self.shared_state.no_jules_sessions:
            jules_messages = [
                msg
                for msg in self.comm_log.list_messages()
                if msg.source == "jules" and msg.session_id == self.shared_state.session_id
            ]
        jules_lines = []
        for msg in jules_messages[-3:]:
            jules_lines.append(f"- {msg.timestamp} {msg.content}")
        if not jules_lines:
            jules_lines.append("No Jules messages for this source yet.")
        response_block = ""
        if self.shared_state.last_unread_response:
            response_block = "\n\nResponse to unread messages:\n" + self.shared_state.last_unread_response
        blocks = [
            "Summary:\n" + summary,
            "Recent user messages:\n" + ("\n".join(recent_lines) if recent_lines else "None"),
            "Jules messages:\n" + "\n".join(jules_lines),
        ]
        if response_block:
            blocks.append(response_block)
        self.shared_state.comm_channel = "\n\n".join(blocks)
        self._record(
            "context_comm_channel",
            "Communication channel updated",
            {"length": len(self.shared_state.comm_channel)},
        )

    def _update_goals_and_rolling(self, events: list[str]) -> None:
        events_text = "\n".join(f"- {line}" for line in events if line)
        if self.shared_state.llm_ready:
            if not self.shared_state.goals_plans:
                self.shared_state.goals_plans = (
                    "This is the first cycle; there are no plans yet. "
                    "Create an initial goals and plans section based on events."
                )
            if not self.shared_state.rolling_context:
                self.shared_state.rolling_context = (
                    "This is the first cycle; there is no rolling context yet. "
                    "Create an initial rolling context based on events."
                )
            self.shared_state.goals_plans = self.compression.update_goals_plans(
                self.shared_state.goals_plans,
                events_text,
                target_tokens=1000,
            )
            self.shared_state.rolling_context = self.compression.update_rolling_context(
                self.shared_state.rolling_context,
                events_text,
                target_tokens=1200,
            )
        else:
            if not self.shared_state.goals_plans:
                self.shared_state.goals_plans = "Goals and plans pending; LLM not ready."
            if not self.shared_state.rolling_context:
                self.shared_state.rolling_context = "Rolling context pending; LLM not ready."
        self._record(
            "context_goals_plans",
            "Goals and plans updated",
            {"length": len(self.shared_state.goals_plans)},
        )
        self._record(
            "context_rolling",
            "Rolling context updated",
            {"length": len(self.shared_state.rolling_context)},
        )

    def _respond_to_unread_messages(self) -> None:
        unread = self.comm_log.unread_user_messages()
        if not unread:
            self.shared_state.last_unread_response = ""
            return
        if not self.shared_state.llm_ready:
            return
        message_text = "\n".join(f"- {msg.content}" for msg in unread)
        response = self.compression.format_unread_response(message_text, target_tokens=300)
        self.shared_state.last_unread_response = response
        if self.shared_state.session_id:
            try:
                self.jules.send_feedback(
                    self.shared_state.session_id,
                    response,
                    session_id=self.shared_state.session_id,
                )
                self.comm_log.mark_read([msg.message_id for msg in unread])
            except Exception as exc:
                self._record("comm_log", "Failed to send unread response", {"error": str(exc)})

    def _parse_agent_turn_output(self, raw: str) -> AgentTurnOutput:
        mess_out_user = ""
        mess_out_jules: dict = {"action": "none", "payload": {}}
        mess_out_log = ""
        try:
            payload = json.loads(raw)
            mess_out_user = str(payload.get("mess_out_USER", "")).strip()
            mess_out_log = str(payload.get("mess_out_LOG", "")).strip()
            mess_out_jules_raw = payload.get("mess_out_JULES", {})
            if isinstance(mess_out_jules_raw, dict):
                action = str(mess_out_jules_raw.get("action", "none"))
                payload_obj = mess_out_jules_raw.get("payload", {})
                if not isinstance(payload_obj, dict):
                    payload_obj = {}
                mess_out_jules = {"action": action, "payload": payload_obj}
        except json.JSONDecodeError:
            mess_out_log = "Agent turn output failed to parse; stored raw output."
        if not mess_out_log:
            mess_out_log = "Agent turn completed."
        return AgentTurnOutput(
            mess_out_user=mess_out_user,
            mess_out_jules=mess_out_jules,
            mess_out_log=mess_out_log,
        )

    def _run_agent_turn(self) -> None:
        if not self.shared_state.llm_ready:
            return
        unread = self.comm_log.unread_user_messages()
        unread_text = "\n".join(f"- {msg.content}" for msg in unread)
        turn_inputs = {
            "system_message": self.shared_state.system_message,
            "comm_channel": self.shared_state.comm_channel,
            "goals_plans": self.shared_state.goals_plans,
            "rolling_context": self.shared_state.rolling_context,
        }
        raw = self.compression.agent_turn_output(turn_inputs, unread_text)
        parsed = self._parse_agent_turn_output(raw)
        self.shared_state.last_agent_output = parsed
        self._record(
            "agent_turn",
            "Agent turn produced",
            {"jules_action": parsed.mess_out_jules.get("action")},
        )
        self._record(
            "agent_turn_jules",
            "Jules action (not executed)",
            {"action": parsed.mess_out_jules},
        )
        self._record(
            "context_agent_output",
            "Agent output updated",
            {"length": len(parsed.mess_out_log)},
        )

    def _generate_feedback(self, request_text: str) -> str:
        context = self._build_context(f"Jules request:\n{request_text}")
        prompt = (
            "You are a project manager agent. Provide concise, actionable feedback "
            "to the Jules agent based on project details and the request."
        )
        response = self.llm.chat_complete(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": context},
            ]
        )
        return response

    def _review_pr(self, pr_diff: str, pr_info: str) -> ReviewResult:
        context = self._build_context(f"PR info:\n{pr_info}\n\nPR diff:\n{pr_diff}")
        prompt = (
            "Review the pull request for correctness, alignment with requirements, "
            "and code quality. Respond with JSON: {\"decision\": \"approve\"|\"reject\", "
            "\"rationale\": \"...\"}."
        )
        response = self.llm.chat_complete(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": context},
            ]
        )
        try:
            data = json.loads(response)
            decision = ReviewDecision(data.get("decision", "reject"))
            rationale = str(data.get("rationale", "")).strip()
            return ReviewResult(decision=decision, rationale=rationale, raw=response)
        except (json.JSONDecodeError, ValueError):
            return ReviewResult(decision=ReviewDecision.REJECT, rationale="Invalid review format", raw=response)

    def initialize(self) -> None:
        docs_text = self._load_docs()
        self._record("init_docs", "Compressing docs", {"bytes": len(docs_text)})
        self.state.docs_summary = self.compression.compress(
            docs_text,
            target_total_tokens=self.config.compression.target_total_tokens,
        )
        self.initialized = True

    def run_once(self) -> None:
        events: list[str] = []
        external_events: list[str] = []
        if not self.initialized:
            self.initialize()
        if self.shared_state.stop_requested:
            self.state.requirements_met = True
            return
        self._sync_jules_messages()
        self._record("loop", "Pulling main")
        self.repo.pull_main()
        events.append("Pulled main branch.")
        codebase_texts = self.repo.read_text_files()
        self._record("loop", "Compressing codebase", {"files": len(codebase_texts)})
        self.state.codebase_summary = self.compression.compress_many(
            codebase_texts,
            target_total_tokens=self.config.compression.target_total_tokens,
        )
        if self.shared_state.no_jules_sessions:
            status = JulesStatus.UNKNOWN
            self.state.last_jules_status = status
            self._record("jules_status", "No Jules session for selected source", {"status": "not_started"})
            external_events.append("Jules status: not started (no session for selected source).")
        else:
            status = self.jules.get_status(self.shared_state.session_id)
            self.state.last_jules_status = status
            self._record("jules_status", "Status received", {"status": status.value})
            external_events.append(f"Jules status: {status.value}.")
        if status == JulesStatus.IN_PROCESS:
            self._respond_to_unread_messages()
            self._build_comm_channel()
            self._update_goals_and_rolling(external_events)
            self._build_context()
            self._run_agent_turn()
            return
        if status == JulesStatus.NEEDS_INPUT:
            self._build_comm_channel()
            self._update_goals_and_rolling(external_events)
            request = self.jules.get_request(self.shared_state.session_id)
            compressed_request = self.compression.compress(
                request.content,
                target_total_tokens=self.config.compression.max_input_tokens,
            )
            feedback = self._generate_feedback(compressed_request)
            self.jules.send_feedback(request.request_id, feedback, self.shared_state.session_id)
            self._record("jules_feedback", "Feedback sent", {"request_id": request.request_id})
            events.append("Sent feedback to Jules.")
            self._respond_to_unread_messages()
            self._build_comm_channel()
            self._update_goals_and_rolling(external_events)
            self._build_context()
            self._run_agent_turn()
            return
        if status == JulesStatus.READY_FOR_REVIEW:
            self._build_comm_channel()
            self._update_goals_and_rolling(external_events)
            pr_info = self.jules.get_pr_info(self.shared_state.session_id)
            self.repo.fetch_branch(pr_info.branch)
            pr_diff = self.repo.diff_main_to_branch(pr_info.branch)
            compressed_diff = self.compression.compress(
                pr_diff,
                target_total_tokens=self.config.compression.max_input_tokens,
            )
            review = self._review_pr(compressed_diff, f"{pr_info.title}\n{pr_info.description}")
            self._record(
                "review_result",
                "PR reviewed",
                {"decision": review.decision.value, "rationale": review.rationale},
            )
            events.append(f"PR reviewed: {review.decision.value}.")
            if review.decision == ReviewDecision.APPROVE:
                self.repo.merge_branch(pr_info.branch)
                session_id = self.jules.start_session(
                    self._build_context("Continue development from the current state."),
                    session_id=self.shared_state.session_id,
                    source=self.shared_state.selected_source,
                )
                if session_id:
                    self.shared_state.session_id = session_id
                self._record("merge", "PR merged and new session started", {"branch": pr_info.branch})
                events.append(f"PR merged from {pr_info.branch}.")
            else:
                self.jules.send_feedback(
                    pr_info.pr_id,
                    review.rationale or "Please address review findings.",
                    self.shared_state.session_id,
                )
                self._record("review_reject", "Review rejected", {"branch": pr_info.branch})
                events.append("PR rejected with feedback.")
            self._respond_to_unread_messages()
            self._build_comm_channel()
            self._update_goals_and_rolling(external_events)
            self._build_context()
            self._run_agent_turn()
            return
        self._respond_to_unread_messages()
        self._build_comm_channel()
        self._update_goals_and_rolling(external_events)
        self._build_context()
        self._run_agent_turn()

    def run_forever(self) -> None:
        iterations = 0
        while True:
            if self.state.requirements_met:
                self._record("stop", "Requirements met; stopping")
                break
            if self.config.loop.max_iterations and iterations >= self.config.loop.max_iterations:
                self._record("stop", "Max iterations reached")
                break
            try:
                self.run_once()
            except Exception as exc:
                self._record("error", "Loop error", {"error": str(exc)})
                print(f"[error] Loop error: {exc}", file=sys.stderr)
            iterations += 1
            time.sleep(self.config.loop.poll_interval_seconds)
