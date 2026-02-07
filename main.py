from __future__ import annotations

import os
import json
import subprocess
import threading
import time
from datetime import datetime
import requests
from dotenv import load_dotenv

from agent.comm_log import CommLog
from agent.compress import CompressionPipeline
from agent.config import AgentConfig
from agent.jules_client import JulesClient
from agent.llm import LlmClient
from agent.loop import AgentLoop, SharedState
from agent.models import TraceBuffer, TraceEvent
from agent.repo_manager import RepoManager
from agent.ui.server import UiServer


def main() -> None:
    repo_path = os.path.abspath(os.getcwd())
    load_dotenv(os.path.join(repo_path, ".env"))
    load_dotenv(os.path.join(repo_path, "example.env"), override=False)
    config = AgentConfig.from_env(repo_path)
    trace = TraceBuffer()
    shared_state = SharedState()
    llm_process: subprocess.Popen | None = None

    data_dir = os.path.join(repo_path, ".lpm")
    comm_log = CommLog(os.path.join(data_dir, "comm_log.jsonl"))
    system_message_path = os.path.join(data_dir, "system_message.txt")
    if os.path.isfile(system_message_path):
        with open(system_message_path, "r", encoding="utf-8") as handle:
            shared_state.system_message = handle.read().strip()
    if not shared_state.system_message:
        shared_state.system_message = (
            "You are LocalProjectManager. Your job is to pursue project completion by "
            "engaging external agents to complete parts of the project."
        )
        os.makedirs(data_dir, exist_ok=True)
        with open(system_message_path, "w", encoding="utf-8") as handle:
            handle.write(shared_state.system_message)
    trace.add(
        TraceEvent(
            kind="context_system_message",
            message="System message set",
            payload={"length": len(shared_state.system_message)},
        )
    )

    def is_llm_ready() -> bool:
        url = f"{config.llm.base_url.rstrip('/')}/v1/models"
        try:
            response = requests.get(url, timeout=2)
            return response.status_code == 200
        except requests.RequestException:
            return False

    llm = LlmClient(config.llm, trace=trace)
    compression = CompressionPipeline(config.compression, llm, trace=trace)
    jules = JulesClient(config.jules)
    repo = RepoManager(config.repo, config.compression)
    loop = AgentLoop(
        config=config,
        llm=llm,
        compression=compression,
        jules=jules,
        repo=repo,
        trace=trace,
        shared_state=shared_state,
        comm_log=comm_log,
    )

    loop_thread: threading.Thread | None = None

    def start_loop(selected_source: str) -> str:
        nonlocal loop_thread
        shared_state.selected_source = selected_source
        shared_state.system_message_locked = True
        if not shared_state.llm_ready:
            return "Model not ready yet."
        if loop_thread and loop_thread.is_alive():
            return "Loop already running."
        try:
            if not shared_state.session_id:
                shared_state.session_id = jules.resolve_session_id_for_source(shared_state.selected_source)
            session_id = jules.start_session(
                "Start session.",
                session_id=shared_state.session_id,
                source=shared_state.selected_source,
            )
            if session_id:
                shared_state.session_id = session_id
        except Exception as exc:
            return f"Session start failed: {exc}"
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        return "Loop started."

    def fetch_sources() -> list[dict]:
        try:
            sources = jules.list_sources()
            trace.add(TraceEvent(kind="jules_sources", message="Sources loaded", payload={"count": len(sources)}))
            return sources
        except Exception as exc:
            trace.add(TraceEvent(kind="jules_sources_error", message="Failed to load sources", payload={"error": str(exc)}))
            raise

    def status_payload() -> dict:
        return {
            "llm_ready": shared_state.llm_ready,
            "selected_source": shared_state.selected_source,
            "session_id": shared_state.session_id,
            "system_message_locked": shared_state.system_message_locked,
        }

    dump_dir = os.path.join(repo_path, "dump")

    def dump_state() -> str:
        os.makedirs(dump_dir, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        filename = f"dump-{timestamp}.json"
        path = os.path.join(dump_dir, filename)
        agent_output = None
        if shared_state.last_agent_output:
            agent_output = {
                "mess_out_USER": shared_state.last_agent_output.mess_out_user,
                "mess_out_JULES": shared_state.last_agent_output.mess_out_jules,
                "mess_out_LOG": shared_state.last_agent_output.mess_out_log,
            }
        payload = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "context": {
                "system_message": shared_state.system_message,
                "comm_channel": shared_state.comm_channel,
                "goals_plans": shared_state.goals_plans,
                "rolling_context": shared_state.rolling_context,
            },
            "agent_output": agent_output,
            "trace": [
                {
                    "kind": event.kind,
                    "message": event.message,
                    "payload": event.payload,
                    "timestamp": event.timestamp,
                }
                for event in trace.snapshot()
            ],
            "comm_log": comm_log.snapshot(),
        }
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, indent=2))
        return path

    ui = UiServer(
        config.ui.host,
        config.ui.port,
        shared_state,
        trace,
        repo_path,
        sources_fetcher=fetch_sources,
        start_callback=start_loop,
        status_provider=status_payload,
        comm_log=comm_log,
        system_message_path=system_message_path,
        dump_handler=dump_state,
    )
    ui.attach_trace_hook()
    ui.start_in_background()
    trace.add(TraceEvent(kind="startup", message=f"UI running at http://{config.ui.host}:{config.ui.port}"))
    print(f"LocalProjectManager UI running at http://{config.ui.host}:{config.ui.port}")

    if config.llm.start_cmd:
        trace.add(TraceEvent(kind="startup", message="Starting llama.cpp server"))
        llm_process = subprocess.Popen(
            config.llm.start_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        wait_until = time.time() + config.llm.start_wait_seconds
        while time.time() < wait_until:
            if is_llm_ready():
                shared_state.llm_ready = True
                trace.add(TraceEvent(kind="startup", message="llama.cpp server ready"))
                break
            time.sleep(1)
        else:
            shared_state.llm_ready = False
            trace.add(TraceEvent(kind="startup", message="llama.cpp server did not respond in time"))
    else:
        shared_state.llm_ready = is_llm_ready()
        if shared_state.llm_ready:
            trace.add(TraceEvent(kind="startup", message="LLM server ready"))
        else:
            trace.add(TraceEvent(kind="startup", message="LLM server not reachable"))

    print("UI ready. Select a source and click Start.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        trace.add(TraceEvent(kind="shutdown", message="KeyboardInterrupt received"))
    finally:
        if llm_process:
            llm_process.terminate()
        ui.shutdown()


if __name__ == "__main__":
    main()
