"""Git activity across one or more local repos.

Aggregates today's commit count and added/removed LOC for the configured author
across all repos. Branch name reflects the *first* repo in the list (the one
the user nominally cares about most). `minutes_since_last_commit` is the
freshest commit in any of the repos by anyone — a "have I shipped lately"
focus indicator, not a personal-only metric.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from dashd.collectors.base import Collector


async def _run(args: list[str], cwd: Path, timeout: float = 5.0) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return None


class GitCollector(Collector):
    key = "git"

    def __init__(self, enabled: bool = True, repos: list[str] | None = None,
                 author_email: str = "") -> None:
        super().__init__(enabled)
        self.repos = [Path(p).expanduser() for p in (repos or [])]
        self.author_email = author_email

    async def collect(self) -> dict[str, Any] | None:
        if not self.repos:
            return None
        # First repo whose .git exists wins the branch slot.
        valid_repos = [r for r in self.repos if (r / ".git").exists() or (r / ".git").is_file()]
        if not valid_repos:
            return None

        # Branch from first valid repo. `symbolic-ref --short HEAD` survives
        # the unborn-branch case (no commits yet) where `rev-parse` fails.
        branch = await _run(["git", "symbolic-ref", "--short", "HEAD"], valid_repos[0])
        if not branch:
            # Detached HEAD — fall back to short commit SHA.
            sha = await _run(["git", "rev-parse", "--short", "HEAD"], valid_repos[0])
            branch = f"@{(sha or '').strip()}" if sha else "(detached)"
        branch = branch.strip()

        # Parallel-fetch per-repo numstat & last-commit timestamp.
        async def per_repo(r: Path) -> tuple[int, int, int, int | None]:
            commits_out = await _run(
                ["git", "log", "--since=midnight",
                 f"--author={self.author_email}", "--numstat", "--pretty=format:__COMMIT__"],
                r,
            )
            commits = added = removed = 0
            if commits_out:
                for line in commits_out.splitlines():
                    if line == "__COMMIT__":
                        commits += 1
                    elif "\t" in line:
                        parts = line.split("\t")
                        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                            added += int(parts[0])
                            removed += int(parts[1])

            last_ts_out = await _run(
                ["git", "log", "-1", "--format=%ct"], r,
            )
            last_ts: int | None = None
            if last_ts_out:
                try:
                    last_ts = int(last_ts_out.strip())
                except ValueError:
                    last_ts = None
            return commits, added, removed, last_ts

        results = await asyncio.gather(*(per_repo(r) for r in valid_repos))
        commits_total = sum(r[0] for r in results)
        added_total = sum(r[1] for r in results)
        removed_total = sum(r[2] for r in results)
        last_ts = max((r[3] for r in results if r[3] is not None), default=None)

        minutes_since_last_commit: int | None = None
        if last_ts is not None:
            minutes_since_last_commit = max(0, int((time.time() - last_ts) / 60))

        return {
            "branch": branch,
            "commits_today": commits_total,
            "loc_added": added_total,
            "loc_removed": removed_total,
            "minutes_since_last_commit": minutes_since_last_commit,
        }
