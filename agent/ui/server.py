from __future__ import annotations

import json
import os
import queue
import threading
from typing import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from ..comm_log import CommLog
from ..loop import SharedState
from ..models import TraceEvent, TraceBuffer


HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Local Agent UI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 16px; }
    #events { height: 240px; overflow: auto; border: 1px solid #ccc; padding: 8px; }
    .slot-box { white-space: pre-wrap; border: 1px solid #ccc; padding: 8px; height: 200px; overflow: auto; }
    .row { margin-bottom: 12px; }
  </style>
</head>
<body>
  <h2>Local Agent UI</h2>
  <div class="row">
    <button onclick="sendInterrupt('__STOP__')">Stop Loop</button>
  </div>
  <div class="row">
    <textarea id="interrupt" rows="3" cols="80" placeholder="User interrupt..."></textarea>
    <button onclick="sendInterrupt()">Send</button>
  </div>
  <div class="row">
    <h3>Trace Events</h3>
    <div id="events"></div>
  </div>
  <div class="row">
    <h3>LLM Stream</h3>
    <div id="llmStream" class="slot-box"></div>
  </div>
  <div class="row">
    <h3>LLM Prompt</h3>
    <div id="llmPrompt" class="slot-box"></div>
  </div>
  <div class="row">
    <h3>Jules Sources</h3>
    <select id="sources"></select>
    <button id="startButton" onclick="startAgent()" disabled>Start</button>
    <div id="startStatus"></div>
  </div>
  <div class="row">
    <h3>Recent User Messages</h3>
    <button onclick="purgeUserMessages()">Purge user messages</button>
    <div id="recentMessages"></div>
  </div>
  <div class="row">
    <h3>Environment (.env)</h3>
    <textarea id="env" rows="8" cols="80" placeholder="LPM_JULES_API_KEY=..."></textarea>
    <div>
      <button onclick="loadEnv()">Load</button>
      <button onclick="saveEnv()">Save</button>
      <button onclick="dumpState()">Dump state</button>
    </div>
  </div>
  <div class="row">
    <h3>Context Window</h3>
    <div>
      <h4>System Message</h4>
      <textarea id="systemMessage" rows="4" cols="80"></textarea>
      <div>
        <button id="saveSystemMessage" onclick="saveSystemMessage()">Save</button>
      </div>
    </div>
    <div>
      <h4>Communication Channel</h4>
      <div id="commChannel" class="slot-box"></div>
    </div>
    <div>
      <h4>Goals and Plans</h4>
      <div id="goalsPlans" class="slot-box"></div>
    </div>
    <div>
      <h4>Rolling Context</h4>
      <div id="rollingContext" class="slot-box"></div>
    </div>
  <div>
    <h4>Agent Turn Output (JSON)</h4>
    <pre id="agentOutput" class="slot-box"></pre>
  </div>
  </div>
  <script>
    const eventsBox = document.getElementById('events');
    const commChannelBox = document.getElementById('commChannel');
    const goalsPlansBox = document.getElementById('goalsPlans');
    const rollingContextBox = document.getElementById('rollingContext');
    const agentOutputBox = document.getElementById('agentOutput');
    const llmStreamBox = document.getElementById('llmStream');
    const llmPromptBox = document.getElementById('llmPrompt');
    const evtSource = new EventSource('/events');
    window.addEventListener('error', (event) => {
      document.getElementById('startStatus').textContent = `UI error: ${event.message}`;
    });
    evtSource.onmessage = function(event) {
      try {
        const data = JSON.parse(event.data);
        const stamp = data.timestamp ? `${data.timestamp} ` : '';
        const line = `${stamp}[${data.kind}] ${data.message}`;
        const div = document.createElement('div');
        div.textContent = line;
        eventsBox.appendChild(div);
        eventsBox.scrollTop = eventsBox.scrollHeight;
        if (data.kind === 'llm_request') {
          llmStreamBox.textContent = '';
          if (data.payload && data.payload.body && Array.isArray(data.payload.body.messages)) {
            const parts = [];
            let total = 0;
            const maxTotal = 12000;
            for (const msg of data.payload.body.messages) {
              const role = msg.role || 'unknown';
              let content = msg.content || '';
              if (content.length > 4000) {
                content = `${content.slice(0, 4000)}\n...[truncated]`;
              }
              const block = `[${role}]\n${content}`;
              total += block.length;
              if (total > maxTotal) {
                parts.push('...[prompt truncated]');
                break;
              }
              parts.push(block);
            }
            llmPromptBox.textContent = parts.join('\\n\\n');
          }
        }
        if (data.kind === 'llm_stream' && data.payload && data.payload.delta) {
          llmStreamBox.textContent += data.payload.delta;
          llmStreamBox.scrollTop = llmStreamBox.scrollHeight;
        }
      } catch (err) {
        document.getElementById('startStatus').textContent = 'UI stream error (see console).';
      }
    };
    async function refreshContext() {
      const response = await fetch('/context');
      const payload = await response.json();
      commChannelBox.textContent = payload.comm_channel || '';
      goalsPlansBox.textContent = payload.goals_plans || '';
      rollingContextBox.textContent = payload.rolling_context || '';
      agentOutputBox.textContent = payload.agent_output_json || '';
    }
    async function sendInterrupt(message) {
      const payload = message || document.getElementById('interrupt').value;
      if (!payload) return;
      await fetch('/interrupt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: payload })
      });
      document.getElementById('interrupt').value = '';
      await refreshContext();
    }
    async function loadEnv() {
      const response = await fetch('/env');
      if (!response.ok) {
        document.getElementById('startStatus').textContent = 'Env error: failed to load.';
        return;
      }
      const payload = await response.json();
      document.getElementById('env').value = payload.content || '';
    }
    async function saveEnv() {
      const content = document.getElementById('env').value || '';
      await fetch('/env', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      });
    }
    async function dumpState() {
      const response = await fetch('/dump', { method: 'POST' });
      const payload = await response.json();
      document.getElementById('startStatus').textContent = payload.message || 'Dumped.';
    }
    async function loadSystemMessage() {
      const response = await fetch('/system');
      const payload = await response.json();
      const textArea = document.getElementById('systemMessage');
      const saveButton = document.getElementById('saveSystemMessage');
      textArea.value = payload.content || '';
      const locked = Boolean(payload.locked);
      textArea.disabled = locked;
      saveButton.disabled = locked;
    }
    async function saveSystemMessage() {
      const content = document.getElementById('systemMessage').value || '';
      await fetch('/system', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
      });
      await loadSystemMessage();
    }
    async function loadSources() {
      const response = await fetch('/sources');
      let payload = {};
      try {
        payload = await response.json();
      } catch (err) {
        document.getElementById('startStatus').textContent = 'Sources error: invalid response.';
        return;
      }
      const select = document.getElementById('sources');
      select.innerHTML = '';
      const sources = payload.sources || [];
      const error = payload.error || '';
      if (response.ok) {
        document.getElementById('startStatus').textContent = '';
      } else if (error) {
        document.getElementById('startStatus').textContent = `Sources error: ${error}`;
      }
      if (!sources.length) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = error ? `No sources` : 'No sources available';
        select.appendChild(opt);
        return;
      }
      for (const source of sources) {
        const opt = document.createElement('option');
        opt.value = source.name || source.id || '';
        opt.textContent = source.name || source.id || '';
        select.appendChild(opt);
      }
    }
    async function refreshStatus() {
      const response = await fetch('/status');
      const payload = await response.json();
      const select = document.getElementById('sources');
      const startButton = document.getElementById('startButton');
      const sourceSelected = Boolean(select.value);
      const llmReady = Boolean(payload.llm_ready);
      startButton.disabled = !sourceSelected || !llmReady;
      if (!llmReady) {
        document.getElementById('startStatus').textContent = 'Model loading...';
      } else if (!sourceSelected) {
        document.getElementById('startStatus').textContent = 'Select a source to start.';
      }
      if (payload.system_message_locked) {
        await loadSystemMessage();
      }
    }
    async function startAgent() {
      const select = document.getElementById('sources');
      const source = select.value;
      if (!source) {
        document.getElementById('startStatus').textContent = 'Select a source first.';
        return;
      }
      const response = await fetch('/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source })
      });
      const payload = await response.json();
      document.getElementById('startStatus').textContent = payload.message || '';
    }
    async function loadRecentMessages() {
      const response = await fetch('/messages/recent');
      const payload = await response.json();
      const container = document.getElementById('recentMessages');
      container.innerHTML = '';
      const messages = payload.messages || [];
      if (!messages.length) {
        container.textContent = 'No recent user messages.';
        return;
      }
      for (const msg of messages) {
        const row = document.createElement('div');
        const status = msg.read ? 'read' : 'unread';
        row.textContent = `[${status}] ${msg.timestamp} ${msg.content}`;
        if (!msg.read) {
          const button = document.createElement('button');
          button.textContent = 'Mark read';
          button.onclick = async () => {
            await fetch('/messages/mark_read', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ids: [msg.message_id] })
            });
            await loadRecentMessages();
          };
          row.appendChild(button);
        }
        container.appendChild(row);
      }
    }
    async function purgeUserMessages() {
      const response = await fetch('/messages/purge_user', { method: 'POST' });
      const payload = await response.json();
      document.getElementById('startStatus').textContent = payload.message || '';
      await loadRecentMessages();
    }
    loadEnv();
    loadSystemMessage();
    loadSources().then(refreshStatus);
    document.getElementById('sources').addEventListener('change', refreshStatus);
    refreshContext();
    loadRecentMessages();
    setInterval(refreshStatus, 2000);
    setInterval(refreshContext, 5000);
    setInterval(loadRecentMessages, 5000);
    setInterval(loadSources, 15000);
  </script>
</body>
</html>
"""


class TraceBroadcaster:
    def __init__(self) -> None:
        self._queues: list[queue.Queue[TraceEvent]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[TraceEvent]:
        q: queue.Queue[TraceEvent] = queue.Queue()
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[TraceEvent]) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def publish(self, event: TraceEvent) -> None:
        with self._lock:
            targets = list(self._queues)
        for q in targets:
            q.put(event)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class UiHandler(BaseHTTPRequestHandler):
    shared_state: SharedState
    trace_buffer: TraceBuffer
    broadcaster: TraceBroadcaster
    env_path: str
    system_message_path: str
    sources_fetcher: Callable[[], list[dict]] | None
    start_callback: Callable[[str], str | None] | None
    status_provider: Callable[[], dict] | None
    comm_log: CommLog
    dump_handler: Callable[[], str] | None

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/":
            data = HTML_PAGE.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/context":
            agent_json = ""
            if self.shared_state.last_agent_output:
                agent_json = json.dumps(
                    {
                        "mess_out_USER": self.shared_state.last_agent_output.mess_out_user,
                        "mess_out_JULES": self.shared_state.last_agent_output.mess_out_jules,
                        "mess_out_LOG": self.shared_state.last_agent_output.mess_out_log,
                    },
                    ensure_ascii=True,
                )
            self._send_json(
                {
                    "comm_channel": self.shared_state.comm_channel,
                    "goals_plans": self.shared_state.goals_plans,
                    "rolling_context": self.shared_state.rolling_context,
                    "agent_output_json": agent_json,
                }
            )
            return
        if self.path == "/system":
            self._send_json(
                {
                    "content": self.shared_state.system_message,
                    "locked": self.shared_state.system_message_locked,
                }
            )
            return
        if self.path == "/env":
            content = ""
            try:
                if os.path.isfile(self.env_path):
                    with open(self.env_path, "r", encoding="utf-8") as handle:
                        content = handle.read()
            except OSError:
                content = ""
            self._send_json({"content": content})
            return
        if self.path == "/sources":
            try:
                sources = self.sources_fetcher() if self.sources_fetcher else []
                self._send_json({"sources": sources})
            except Exception as exc:
                self._send_json({"sources": [], "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if self.path == "/status":
            payload = self.status_provider() if self.status_provider else {}
            self._send_json(payload)
            return
        if self.path == "/messages/recent":
            messages = self.comm_log.recent_user_messages(3)
            payload = {
                "messages": [
                    {
                        "message_id": msg.message_id,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                        "read": msg.read,
                        "source": msg.source,
                    }
                    for msg in messages
                ]
            }
            self._send_json(payload)
            return
        if self.path == "/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = self.broadcaster.subscribe()
            try:
                while True:
                    try:
                        event = q.get(timeout=10)
                        payload = json.dumps(
                            {
                                "kind": event.kind,
                                "message": event.message,
                                "payload": event.payload,
                                "timestamp": event.timestamp,
                            }
                        )
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except ConnectionError:
                pass
            finally:
                self.broadcaster.unsubscribe(q)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/interrupt":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            message = ""
            try:
                payload = json.loads(body)
                message = str(payload.get("message", "")).strip()
            except json.JSONDecodeError:
                message = body.strip()
            if message:
                self.shared_state.add_interrupt(message)
                self.comm_log.append(source="local_ui", role="user", content=message, read=False)
            self._send_json({"ok": True})
            return
        if self.path == "/system":
            if self.shared_state.system_message_locked:
                self._send_json({"ok": False, "message": "System message is locked."}, status=HTTPStatus.FORBIDDEN)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            content = ""
            try:
                payload = json.loads(body)
                content = str(payload.get("content", "")).strip()
            except json.JSONDecodeError:
                content = body.strip()
            if content:
                self.shared_state.system_message = content
                try:
                    with open(self.system_message_path, "w", encoding="utf-8") as handle:
                        handle.write(content)
                except OSError:
                    self._send_json({"ok": False}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
            self._send_json({"ok": True})
            return
        if self.path == "/env":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            content = ""
            try:
                payload = json.loads(body)
                content = str(payload.get("content", ""))
            except json.JSONDecodeError:
                content = body
            try:
                with open(self.env_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
            except OSError:
                self._send_json({"ok": False}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True})
            return
        if self.path == "/messages/mark_read":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            ids: list[str] = []
            try:
                payload = json.loads(body)
                ids = [str(item) for item in payload.get("ids", [])]
            except json.JSONDecodeError:
                ids = []
            if ids:
                self.comm_log.mark_read(ids)
            self._send_json({"ok": True})
            return
        if self.path == "/messages/purge_user":
            removed = self.comm_log.purge_user_messages()
            self._send_json({"ok": True, "message": f"Purged {removed} user messages."})
            return
        if self.path == "/dump":
            if not self.dump_handler:
                self._send_json({"ok": False, "message": "Dump not configured."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                filename = self.dump_handler()
            except Exception as exc:
                self._send_json({"ok": False, "message": f"Dump failed: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True, "message": f"Dumped to {filename}"})
            return
        if self.path == "/start":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            source = ""
            try:
                payload = json.loads(body)
                source = str(payload.get("source", "")).strip()
            except json.JSONDecodeError:
                source = body.strip()
            if not source:
                self._send_json({"ok": False, "message": "Source is required."}, status=HTTPStatus.BAD_REQUEST)
                return
            if not self.start_callback:
                self._send_json({"ok": False, "message": "Start not configured."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                message = self.start_callback(source)
            except Exception as exc:
                self._send_json({"ok": False, "message": f"Start failed: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json({"ok": True, "message": message or "Started"})
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()


class UiServer:
    def __init__(
        self,
        host: str,
        port: int,
        shared_state: SharedState,
        trace: TraceBuffer,
        repo_path: str,
        sources_fetcher: Callable[[], list[dict]] | None = None,
        start_callback: Callable[[str], str | None] | None = None,
        status_provider: Callable[[], dict] | None = None,
        comm_log: CommLog | None = None,
        system_message_path: str | None = None,
        dump_handler: Callable[[], str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.shared_state = shared_state
        self.trace = trace
        self.broadcaster = TraceBroadcaster()
        self.env_path = os.path.join(repo_path, ".env")
        self.system_message_path = system_message_path or os.path.join(repo_path, ".lpm", "system_message.txt")
        self.sources_fetcher = sources_fetcher
        self.start_callback = start_callback
        self.status_provider = status_provider
        self.comm_log = comm_log or CommLog(os.path.join(repo_path, ".lpm", "comm_log.jsonl"))
        self.dump_handler = dump_handler
        self._server = _ThreadedHTTPServer((self.host, self.port), UiHandler)
        UiHandler.shared_state = shared_state
        UiHandler.trace_buffer = trace
        UiHandler.broadcaster = self.broadcaster
        UiHandler.env_path = self.env_path
        UiHandler.system_message_path = self.system_message_path
        UiHandler.sources_fetcher = staticmethod(sources_fetcher) if sources_fetcher else None
        UiHandler.start_callback = staticmethod(start_callback) if start_callback else None
        UiHandler.status_provider = staticmethod(status_provider) if status_provider else None
        UiHandler.comm_log = self.comm_log
        UiHandler.dump_handler = staticmethod(dump_handler) if dump_handler else None

    def attach_trace_hook(self) -> None:
        def _hook(event: TraceEvent) -> None:
            self.broadcaster.publish(event)

        self.trace.hook = _hook

    def start_in_background(self) -> None:
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
