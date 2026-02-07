from __future__ import annotations

from dataclasses import dataclass
import os


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class CompressionConfig:
    max_input_tokens: int = 4000
    target_chunk_tokens: int = 1000
    target_total_tokens: int = 1000
    max_file_bytes: int = 2_000_000


@dataclass(frozen=True)
class LlmConfig:
    base_url: str = "http://localhost:8080"
    model: str = "qwen3-8b"
    temperature: float = 0.2
    max_tokens: int = 8192
    timeout_seconds: int = 600
    start_cmd: str | None = None
    start_wait_seconds: int = 20


@dataclass(frozen=True)
class JulesConfig:
    base_url: str = "https://jules.googleapis.com/v1alpha"
    api_key: str | None = None
    session_id: str | None = None
    source: str | None = None
    starting_branch: str = "main"
    session_title: str | None = None


@dataclass(frozen=True)
class RepoConfig:
    repo_path: str
    main_branch: str = "main"


@dataclass(frozen=True)
class UiConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class LoopConfig:
    poll_interval_seconds: int = 10
    max_iterations: int = 0


@dataclass(frozen=True)
class DocsConfig:
    docs_path: str = "docs"
    include_readme: bool = True


@dataclass(frozen=True)
class AgentConfig:
    compression: CompressionConfig
    llm: LlmConfig
    jules: JulesConfig
    repo: RepoConfig
    ui: UiConfig
    loop: LoopConfig
    docs: DocsConfig

    @staticmethod
    def from_env(repo_path: str) -> "AgentConfig":
        compression = CompressionConfig(
            max_input_tokens=_env_int("LPM_MAX_INPUT_TOKENS", 4000),
            target_chunk_tokens=_env_int("LPM_TARGET_CHUNK_TOKENS", 1000),
            target_total_tokens=_env_int("LPM_TARGET_TOTAL_TOKENS", 1000),
            max_file_bytes=_env_int("LPM_MAX_FILE_BYTES", 2_000_000),
        )
        llm = LlmConfig(
            base_url=os.getenv("LPM_LLM_BASE_URL", "http://localhost:8080"),
            model=os.getenv("LPM_LLM_MODEL", "qwen3-8b"),
            temperature=_env_float("LPM_LLM_TEMPERATURE", 0.2),
            max_tokens=_env_int("LPM_LLM_MAX_TOKENS", 8192),
            timeout_seconds=_env_int("LPM_LLM_TIMEOUT_SECONDS", 600),
            start_cmd=os.getenv("LPM_LLM_START_CMD") or None,
            start_wait_seconds=_env_int("LPM_LLM_START_WAIT_SECONDS", 20),
        )
        jules = JulesConfig(
            base_url=os.getenv("LPM_JULES_BASE_URL", "https://jules.googleapis.com/v1alpha"),
            api_key=os.getenv("LPM_JULES_API_KEY"),
            session_id=os.getenv("LPM_JULES_SESSION_ID"),
            source=os.getenv("LPM_JULES_SOURCE"),
            starting_branch=os.getenv("LPM_JULES_STARTING_BRANCH", "main"),
            session_title=os.getenv("LPM_JULES_SESSION_TITLE"),
        )
        repo = RepoConfig(
            repo_path=repo_path,
            main_branch=os.getenv("LPM_MAIN_BRANCH", "main"),
        )
        ui = UiConfig(
            host=os.getenv("LPM_UI_HOST", "127.0.0.1"),
            port=_env_int("LPM_UI_PORT", 8765),
        )
        loop = LoopConfig(
            poll_interval_seconds=_env_int("LPM_POLL_INTERVAL_SECONDS", 10),
            max_iterations=_env_int("LPM_MAX_ITERATIONS", 0),
        )
        docs = DocsConfig(
            docs_path=os.getenv("LPM_DOCS_PATH", "docs"),
            include_readme=os.getenv("LPM_INCLUDE_README", "1") != "0",
        )
        return AgentConfig(
            compression=compression,
            llm=llm,
            jules=jules,
            repo=repo,
            ui=ui,
            loop=loop,
            docs=docs,
        )
