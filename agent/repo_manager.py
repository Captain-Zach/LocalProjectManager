from __future__ import annotations

import os
import subprocess

from .config import RepoConfig, CompressionConfig


class RepoManager:
    def __init__(self, config: RepoConfig, compression: CompressionConfig) -> None:
        self.config = config
        self.compression = compression

    def _run(self, args: list[str]) -> str:
        result = subprocess.run(
            args,
            cwd=self.config.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def pull_main(self) -> None:
        self._run(["git", "checkout", self.config.main_branch])
        self._run(["git", "pull", "origin", self.config.main_branch])

    def fetch_branch(self, branch: str) -> None:
        self._run(["git", "fetch", "origin", branch])

    def merge_branch(self, branch: str) -> None:
        self._run(["git", "checkout", self.config.main_branch])
        self._run(["git", "merge", f"origin/{branch}"])

    def diff_main_to_branch(self, branch: str) -> str:
        return self._run(["git", "diff", f"{self.config.main_branch}..origin/{branch}"])

    def list_tracked_files(self) -> list[str]:
        output = self._run(["git", "ls-files"])
        files = [line.strip() for line in output.splitlines() if line.strip()]
        return [os.path.join(self.config.repo_path, path) for path in files]

    def read_text_files(self) -> list[str]:
        contents = []
        for path in self.list_tracked_files():
            if not os.path.isfile(path):
                continue
            if os.path.getsize(path) > self.compression.max_file_bytes:
                continue
            with open(path, "rb") as handle:
                sample = handle.read(4096)
                if b"\x00" in sample:
                    continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    contents.append(f"File: {os.path.relpath(path, self.config.repo_path)}\n{handle.read()}")
            except UnicodeDecodeError:
                continue
        return contents
