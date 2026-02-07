from __future__ import annotations

import requests

from .config import JulesConfig
from .models import JulesRequest, JulesStatus, PrInfo


class JulesClient:
    def __init__(self, config: JulesConfig) -> None:
        self.config = config
        self._google_api = "jules.googleapis.com" in config.base_url

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            if self._google_api:
                headers["X-Goog-Api-Key"] = self.config.api_key
            else:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return requests.get(
            self._url(path),
            headers=self._headers(),
            params=params,
            timeout=30,
        )

    def _post(self, path: str, payload: dict | None = None) -> requests.Response:
        return requests.post(
            self._url(path),
            headers=self._headers(),
            json=payload,
            timeout=30,
        )

    def _resolve_session_id(self, session_id: str | None = None) -> str | None:
        if session_id:
            return session_id
        if self.config.session_id:
            return self.config.session_id
        if not self._google_api:
            return None
        response = self._get("/sessions", params={"pageSize": 1})
        response.raise_for_status()
        payload = response.json()
        sessions = payload.get("sessions", [])
        if not sessions:
            return None
        return str(sessions[0].get("id") or sessions[0].get("name", "")).split("/")[-1]

    def resolve_session_id_for_source(self, source: str, page_size: int = 50) -> str | None:
        if not self._google_api or not source:
            return None
        response = self._get("/sessions", params={"pageSize": page_size})
        response.raise_for_status()
        payload = response.json()
        sessions = payload.get("sessions", [])
        for session in sessions:
            source_context = session.get("sourceContext", {})
            if source_context.get("source") == source:
                return str(session.get("id") or session.get("name", "")).split("/")[-1]
        return None

    def _get_session(self, session_id: str) -> dict:
        response = self._get(f"/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    def _list_activities(self, session_id: str, page_size: int = 30) -> list[dict]:
        response = self._get(f"/sessions/{session_id}/activities", params={"pageSize": page_size})
        response.raise_for_status()
        payload = response.json()
        return payload.get("activities", [])

    def _find_pull_request(self, session: dict) -> dict | None:
        for output in session.get("outputs", []):
            pr = output.get("pullRequest")
            if pr:
                return pr
        return None

    def _extract_activity_content(self, activity: dict) -> str:
        for key in ("prompt", "message", "content", "text"):
            if key in activity and activity[key]:
                return str(activity[key])
        for container_key in (
            "messageSent",
            "userMessage",
            "assistantMessage",
            "progressUpdated",
        ):
            container = activity.get(container_key)
            if isinstance(container, dict):
                for key in ("prompt", "message", "content", "description", "title"):
                    if key in container and container[key]:
                        return str(container[key])
        return ""

    def get_status(self, session_id: str | None = None) -> JulesStatus:
        if self._google_api:
            session_id = self._resolve_session_id(session_id)
            if not session_id:
                return JulesStatus.UNKNOWN
            session = self._get_session(session_id)
            if self._find_pull_request(session):
                return JulesStatus.READY_FOR_REVIEW
            return JulesStatus.IN_PROCESS
        response = self._get("/status")
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status", "unknown")
        try:
            return JulesStatus(status)
        except ValueError:
            return JulesStatus.UNKNOWN

    def get_request(self, session_id: str | None = None) -> JulesRequest:
        if self._google_api:
            session_id = self._resolve_session_id(session_id) or ""
            return JulesRequest(request_id=session_id, content="")
        response = self._get("/request")
        response.raise_for_status()
        payload = response.json()
        return JulesRequest(
            request_id=str(payload.get("id", "")),
            content=str(payload.get("content", "")),
        )

    def send_feedback(self, request_id: str, feedback: str, session_id: str | None = None) -> None:
        if self._google_api:
            session_id = self._resolve_session_id(session_id)
            if not session_id:
                raise RuntimeError("No Jules session available for feedback.")
            payload = {"prompt": feedback}
            response = self._post(f"/sessions/{session_id}:sendMessage", payload)
            response.raise_for_status()
            return
        payload = {"id": request_id, "feedback": feedback}
        response = self._post("/feedback", payload)
        response.raise_for_status()

    def get_pr_info(self, session_id: str | None = None) -> PrInfo:
        if self._google_api:
            session_id = self._resolve_session_id(session_id)
            if not session_id:
                raise RuntimeError("No Jules session available for PR info.")
            session = self._get_session(session_id)
            pr = self._find_pull_request(session)
            if not pr:
                raise RuntimeError("No pull request found in Jules session outputs.")
            return PrInfo(
                pr_id=str(pr.get("id") or ""),
                branch=str(pr.get("branch") or ""),
                url=pr.get("url"),
                title=pr.get("title"),
                description=pr.get("description"),
            )
        response = self._get("/pr")
        response.raise_for_status()
        payload = response.json()
        return PrInfo(
            pr_id=str(payload.get("id", "")),
            branch=str(payload.get("branch", "")),
            url=payload.get("url"),
            title=payload.get("title"),
            description=payload.get("description"),
        )

    def start_session(self, context: str, session_id: str | None = None, source: str | None = None) -> str | None:
        if self._google_api:
            session_id = self._resolve_session_id(session_id)
            if session_id:
                payload = {"prompt": context}
                response = self._post(f"/sessions/{session_id}:sendMessage", payload)
                response.raise_for_status()
                return session_id
            source_name = source or self.config.source
            if not source_name:
                raise RuntimeError("No Jules source configured to create a session.")
            payload = {
                "prompt": context,
                "title": self.config.session_title or "LocalProjectManager Session",
                "sourceContext": {
                    "source": source_name,
                    "githubRepoContext": {"startingBranch": self.config.starting_branch},
                },
            }
            response = self._post("/sessions", payload)
            response.raise_for_status()
            payload = response.json()
            created_id = str(payload.get("id") or payload.get("name", "")).split("/")[-1]
            return created_id or None
        payload = {"context": context}
        response = self._post("/start_session", payload)
        response.raise_for_status()
        return None

    def list_sources(self) -> list[dict]:
        if not self._google_api:
            return []
        response = self._get("/sources")
        response.raise_for_status()
        payload = response.json()
        sources = payload.get("sources", [])
        results: list[dict] = []
        for source in sources:
            results.append(
                {
                    "name": source.get("name"),
                    "id": source.get("id"),
                }
            )
        return results

    def list_recent_messages(self, session_id: str | None, limit: int = 20) -> list[dict]:
        if not self._google_api:
            return []
        session_id = self._resolve_session_id(session_id)
        if not session_id:
            return []
        activities = self._list_activities(session_id, page_size=limit)
        results: list[dict] = []
        for activity in activities:
            content = self._extract_activity_content(activity)
            if not content:
                continue
            results.append(
                {
                    "id": activity.get("id") or activity.get("name"),
                    "role": activity.get("originator", "agent"),
                    "content": content,
                    "timestamp": activity.get("createTime"),
                }
            )
        return results
