"""Repository discovery and git-backed data access."""

from __future__ import annotations

import collections
import os
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from .data import parse_patch_sections
from .models import (
    BranchData,
    CommitDetail,
    CommitFileChange,
    CommitRecord,
    RepoContext,
)
from .utils import detect_language, parse_iso_datetime, random_color


DEFAULT_IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "coverage",
    ".next",
    ".nuxt",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
}

EXTENSION_TO_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".java": "Java",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".go": "Go",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".rs": "Rust",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".fish": "Shell",
    ".json": "JSON",
    ".jsonc": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".toml": "TOML",
    ".md": "Markdown",
    ".sql": "SQL",
    ".r": "R",
    ".dart": "Dart",
    ".scala": "Scala",
    ".pl": "Perl",
    ".pm": "Perl",
    ".m": "Objective-C/MATLAB",
    ".mm": "Objective-C++",
    ".vb": "Visual Basic",
    ".ps1": "PowerShell",
    ".bat": "Batchfile",
    ".cmd": "Batchfile",
    ".dockerfile": "Dockerfile",
}

LANGUAGE_COLORS = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "Java": "#b07219",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#178600",
    "Ruby": "#701516",
    "Go": "#00ADD8",
    "PHP": "#4F5D95",
    "TypeScript": "#3178c6",
    "Swift": "#ffac45",
    "Kotlin": "#A97BFF",
    "Rust": "#dea584",
    "Lua": "#000080",
    "Shell": "#89e051",
    "JSON": "#292929",
    "YAML": "#cb171e",
    "XML": "#0060ac",
    "Markdown": "#083fa1",
    "SQL": "#e38c00",
    "R": "#198CE7",
    "Dart": "#00B4AB",
    "Scala": "#c22d40",
    "Perl": "#0298c3",
    "PowerShell": "#012456",
    "Batchfile": "#C1F12E",
    "Dockerfile": "#384d54",
}


def run_git(repo_dir: str, args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_dir,
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}") from exc


def get_branches(repo_dir: str) -> tuple[list[str], str]:
    output = run_git(repo_dir, ["branch", "--format=%(refname:short)"])
    branches = [line.strip() for line in output.splitlines() if line.strip()]
    head = run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if head and head not in branches:
        branches.append(head)
    branches = sorted(set(branches))
    if not branches:
        raise RuntimeError("No branches found in repository.")
    return branches, head if head else branches[0]


def parse_gitignore(repo_dir: str) -> set[str]:
    path = Path(repo_dir) / ".gitignore"
    names: set[str] = set()
    if not path.exists():
        return names
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "/" in line or "*" in line or line.startswith("!"):
            continue
        names.add(line.rstrip("/"))
    return names


def scan_languages(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    ignore_dirs: set[str],
) -> tuple[list[dict[str, object]], dict[str, list[str]]]:
    totals: dict[str, int] = {}
    files_by_language: dict[str, list[str]] = {}
    total_value = 0

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [directory for directory in dirs if directory not in ignore_dirs]
        for filename in files:
            full_path = os.path.join(root, filename)
            if not os.path.isfile(full_path):
                continue
            language = detect_language(filename, EXTENSION_TO_LANGUAGE)
            if language in exclude_languages:
                continue
            rel_path = os.path.relpath(full_path, repo_dir)
            files_by_language.setdefault(language, []).append(rel_path)
            if language_mode == "files":
                value = 1
            else:
                try:
                    value = os.path.getsize(full_path)
                except OSError:
                    value = 0
            totals[language] = totals.get(language, 0) + value
            total_value += value

    stats: list[dict[str, object]] = []
    for language, value in totals.items():
        percentage = (value / total_value * 100.0) if total_value else 0.0
        stats.append(
            {
                "language": language,
                "value": value,
                "percentage": percentage,
                "color": LANGUAGE_COLORS.get(language, random_color(language)),
            }
        )
    stats.sort(key=lambda item: float(item["percentage"]), reverse=True)
    for language in files_by_language:
        files_by_language[language].sort()
    return stats, files_by_language


def build_repo_context(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    extra_ignored_dirs: set[str],
) -> tuple[RepoContext, "RepoService"]:
    branches, current_branch = get_branches(repo_dir)
    ignore_dirs = set(DEFAULT_IGNORED_DIRS)
    ignore_dirs.update(parse_gitignore(repo_dir))
    ignore_dirs.update(extra_ignored_dirs)
    language_stats, files_by_language = scan_languages(
        repo_dir,
        language_mode,
        exclude_languages,
        ignore_dirs,
    )
    context = RepoContext(
        repo_dir=repo_dir,
        project_name=os.path.basename(os.path.abspath(repo_dir)),
        repo_commit_count=int(run_git(repo_dir, ["rev-list", "--all", "--count"]).strip() or "0"),
        branches=branches,
        current_branch=current_branch,
        language_stats=language_stats,
        files_by_language=files_by_language,
    )
    return context, RepoService(
        repo_dir,
        language_mode=language_mode,
        exclude_languages=exclude_languages,
        ignore_dirs=ignore_dirs,
        initial_scanned_file_count=sum(len(files) for files in files_by_language.values()),
    )


class RepoService:
    """Thin cached wrapper over git commands."""

    def __init__(
        self,
        repo_dir: str,
        *,
        language_mode: str = "bytes",
        exclude_languages: set[str] | None = None,
        ignore_dirs: set[str] | None = None,
        initial_scanned_file_count: int | None = None,
    ):
        self.repo_dir = repo_dir
        self.language_mode = language_mode
        self.exclude_languages = set(exclude_languages or set())
        self.ignore_dirs = set(ignore_dirs or set())
        self._cached_scanned_file_count = initial_scanned_file_count

    def get_cached_scanned_file_count(self) -> int | None:
        return self._cached_scanned_file_count

    def refresh_scanned_file_count(self) -> tuple[int, float]:
        started = time.perf_counter()
        output = run_git(
            self.repo_dir,
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        )
        count = 0
        repo_root = Path(self.repo_dir)
        for raw_path in output.split("\0"):
            if not raw_path:
                continue
            path = Path(raw_path)
            if any(part in self.ignore_dirs for part in path.parts):
                continue
            full_path = repo_root / path
            if not full_path.is_file():
                continue
            language = detect_language(raw_path, EXTENSION_TO_LANGUAGE)
            if language in self.exclude_languages:
                continue
            count += 1
        duration = time.perf_counter() - started
        self._cached_scanned_file_count = count
        return count, duration

    @lru_cache(maxsize=64)
    def get_branch_data(self, branch: str) -> BranchData:
        meta_out = run_git(
            self.repo_dir,
            [
                "log",
                branch,
                "--date=iso-strict",
                "--pretty=format:%H|%ad|%an|%ae|%s",
            ],
        )
        files_out = run_git(
            self.repo_dir,
            [
                "log",
                branch,
                "--pretty=format:__COMMIT__%H",
                "--name-only",
            ],
        )

        files_by_commit: dict[str, list[str]] = {}
        current_hash = ""
        for raw in files_out.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("__COMMIT__"):
                current_hash = line.removeprefix("__COMMIT__")
                files_by_commit.setdefault(current_hash, [])
                continue
            if current_hash:
                files_by_commit[current_hash].append(line)

        commits: list[CommitRecord] = []
        for line in meta_out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            commit_hash, authored_raw, author_name, author_email, message = parts
            commits.append(
                CommitRecord(
                    commit_hash=commit_hash,
                    authored_at=parse_iso_datetime(authored_raw),
                    message=message.strip(),
                    author_name=author_name.strip(),
                    author_email=author_email.strip(),
                    changed_files=tuple(files_by_commit.get(commit_hash, [])),
                )
            )

        years = sorted({item.authored_date.year for item in commits})
        return BranchData(branch=branch, commits=commits, years=years)

    @lru_cache(maxsize=256)
    def get_commit_detail(self, commit_hash: str) -> CommitDetail:
        meta_out = run_git(
            self.repo_dir,
            [
                "show",
                "--no-patch",
                "--date=iso-strict",
                "--format=%H%n%P%n%an%n%ae%n%ad%n%s%n%b",
                commit_hash,
            ],
        )
        numstat_out = run_git(
            self.repo_dir,
            [
                "show",
                "--format=",
                "--numstat",
                "--find-renames",
                commit_hash,
            ],
        )
        patch_out = run_git(
            self.repo_dir,
            [
                "show",
                "--format=",
                "--patch",
                "--find-renames",
                commit_hash,
            ],
        )

        meta_lines = meta_out.splitlines()
        if len(meta_lines) < 6:
            raise RuntimeError(f"Could not parse commit metadata for {commit_hash}")
        full_hash, parents_raw, author_name, author_email, authored_raw, subject = meta_lines[:6]
        body = "\n".join(meta_lines[6:]).strip()

        file_changes: list[CommitFileChange] = []
        for raw in numstat_out.splitlines():
            if not raw.strip():
                continue
            parts = raw.split("\t", 2)
            if len(parts) != 3:
                continue
            added_raw, deleted_raw, path = parts
            additions = None if added_raw == "-" else int(added_raw)
            deletions = None if deleted_raw == "-" else int(deleted_raw)
            file_changes.append(
                CommitFileChange(
                    path=path,
                    additions=additions,
                    deletions=deletions,
                )
            )

        return CommitDetail(
            commit_hash=full_hash,
            parents=tuple(part for part in parents_raw.split() if part),
            author_name=author_name,
            author_email=author_email,
            authored_at=parse_iso_datetime(authored_raw),
            subject=subject,
            body=body,
            file_changes=tuple(file_changes),
            patch_sections=parse_patch_sections(patch_out),
            patch=patch_out.rstrip(),
        )
