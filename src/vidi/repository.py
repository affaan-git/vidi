"""Repository discovery and git-backed data access."""

from __future__ import annotations

import os
import subprocess
import time
from functools import lru_cache
from typing import Iterator

from .data import parse_patch_sections
from .models import (
    BranchData,
    CommitDetail,
    CommitFileChange,
    CommitRecord,
    LanguageStat,
    RepoContext,
    RepoScanSnapshot,
)
from .utils import (
    detect_language,
    parse_iso_datetime,
    random_color,
    replace_terminal_control,
    unquote_git_path,
)


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
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".astro": "Astro",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".tf": "HCL",
    ".tfvars": "HCL",
    ".hcl": "HCL",
    ".ipynb": "Jupyter Notebook",
    ".zig": "Zig",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hrl": "Erlang",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".mli": "OCaml",
    ".jl": "Julia",
    ".nix": "Nix",
    ".proto": "Protocol Buffer",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".cljc": "Clojure",
    ".groovy": "Groovy",
    ".mk": "Makefile",
    ".cmake": "CMake",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
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
    "Vue": "#41b883",
    "Svelte": "#ff3e00",
    "Astro": "#ff5a03",
    "SCSS": "#c6538c",
    "Sass": "#a53b70",
    "Less": "#1d365d",
    "HCL": "#844FBA",
    "Jupyter Notebook": "#DA5B0B",
    "Zig": "#ec915c",
    "Elixir": "#6e4a7e",
    "Erlang": "#B83998",
    "Haskell": "#5e5086",
    "OCaml": "#ef7a08",
    "Julia": "#a270ba",
    "Nix": "#7e7eff",
    "Clojure": "#db5855",
    "Groovy": "#4298b8",
    "Makefile": "#427819",
    "CMake": "#DA3434",
}


def run_git(repo_dir: str, args: list[str], *, errors: str = "replace") -> str:
    """Run git and return its output with non-ASCII paths left unescaped.

    Bytes that are not UTF-8 become U+FFFD unless `errors` is "strict".
    """
    try:
        output = subprocess.check_output(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.quotePath=false",
                "-c",
                "log.showSignature=false",
                *args,
            ],
            cwd=repo_dir,
            encoding="utf-8",
            errors=errors,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "unknown git error"
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}") from exc
    return replace_terminal_control(output)


def get_branch_heads(repo_dir: str) -> tuple[list[str], str, dict[str, str]]:
    output = run_git(
        repo_dir,
        ["for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads"],
    )
    branch_heads: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, object_name = line.partition("\t")
        if name and object_name:
            branch_heads[name] = object_name
    try:
        head = run_git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except RuntimeError:
        # An unborn HEAD in a new repo or on an orphan branch.
        head = ""
    if head and head != "HEAD" and head not in branch_heads:
        branch_heads[head] = run_git(repo_dir, ["rev-parse", head]).strip()
    branches = sorted(branch_heads)
    if not branches:
        if not head:
            raise RuntimeError(f"no commits yet: {repo_dir}")
        raise RuntimeError("No branches found in repository.")
    return branches, head if head else branches[0], branch_heads


def list_worktree_paths(repo_dir: str) -> list[str]:
    """Tracked and untracked files that git does not ignore, each listed once."""
    output = run_git(
        repo_dir,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    )
    # A file with a merge conflict is listed once per stage.
    return list(dict.fromkeys(path for path in output.split("\0") if path))


def _in_ignored_dir(path: str, ignore_dirs: set[str]) -> bool:
    return any(component in ignore_dirs for component in path.split("/"))


def _scanned_files(
    repo_dir: str, exclude_languages: set[str], ignore_dirs: set[str]
) -> Iterator[tuple[str, str, str]]:
    """The relative path, full path, and language of each work-tree file the scan counts."""
    for rel_path in list_worktree_paths(repo_dir):
        if _in_ignored_dir(rel_path, ignore_dirs):
            continue
        full_path = os.path.join(repo_dir, rel_path)
        if not os.path.isfile(full_path):
            continue
        language = detect_language(rel_path, EXTENSION_TO_LANGUAGE)
        if language not in exclude_languages:
            yield rel_path, full_path, language


def scan_languages(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    ignore_dirs: set[str],
) -> tuple[list[LanguageStat], dict[str, list[str]]]:
    totals: dict[str, int] = {}
    files_by_language: dict[str, list[str]] = {}
    for rel_path, full_path, language in _scanned_files(repo_dir, exclude_languages, ignore_dirs):
        files_by_language.setdefault(language, []).append(rel_path)
        if language_mode == "files":
            value = 1
        else:
            try:
                value = os.path.getsize(full_path)
            except OSError:
                value = 0
        totals[language] = totals.get(language, 0) + value
    return _language_stats(totals, files_by_language)


def _language_stats(
    totals: dict[str, int], files_by_language: dict[str, list[str]]
) -> tuple[list[LanguageStat], dict[str, list[str]]]:
    """Per-language shares of the totals, largest first, and the sorted file lists."""
    total_value = sum(totals.values())
    stats: list[LanguageStat] = []
    for language, value in totals.items():
        percentage = (value / total_value * 100.0) if total_value else 0.0
        stats.append(
            {
                "language": language,
                "value": value,
                "percentage": percentage,
                "count": len(files_by_language[language]),
                "color": LANGUAGE_COLORS.get(language, random_color(language)),
            }
        )
    stats.sort(key=lambda item: float(item["percentage"]), reverse=True)
    for language in files_by_language:
        files_by_language[language].sort()
    return stats, files_by_language


def _repo_commit_count(repo_dir: str) -> int:
    return int(run_git(repo_dir, ["rev-list", "--all", "--count"]).strip() or "0")


def _repo_context(
    repo_dir: str,
    language_stats: list[LanguageStat],
    files_by_language: dict[str, list[str]],
) -> RepoContext:
    branches, current_branch, branch_heads = get_branch_heads(repo_dir)
    return RepoContext(
        repo_dir=repo_dir,
        project_name=os.path.basename(os.path.abspath(repo_dir)),
        repo_commit_count=_repo_commit_count(repo_dir),
        branches=branches,
        branch_heads=tuple(sorted(branch_heads.items())),
        current_branch=current_branch,
        language_stats=language_stats,
        files_by_language=files_by_language,
    )


def _resolve_ignore_dirs(extra_ignored_dirs: set[str]) -> set[str]:
    return DEFAULT_IGNORED_DIRS | set(extra_ignored_dirs)


def build_initial_repo_context(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    extra_ignored_dirs: set[str],
) -> tuple[RepoContext, "RepoService"]:
    """The branch and commit counts without the language scan or any branch history."""
    return _repo_context(repo_dir, [], {}), RepoService(
        repo_dir,
        language_mode=language_mode,
        exclude_languages=exclude_languages,
        ignore_dirs=_resolve_ignore_dirs(extra_ignored_dirs),
        extra_ignored_dirs=extra_ignored_dirs,
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
        extra_ignored_dirs: set[str] | None = None,
    ):
        self.repo_dir = repo_dir
        self.language_mode = language_mode
        self.exclude_languages = set(exclude_languages or set())
        self.ignore_dirs = set(ignore_dirs or set())
        # The user-supplied extras alone, without the defaults.
        self.extra_ignored_dirs = set(extra_ignored_dirs or set())
        self._cached_scanned_file_count: int | None = None
        self._origin_head: str | None = None
        # Per-instance caches. A class-level lru_cache would keep every service alive.
        self.get_branch_data = lru_cache(maxsize=64)(self._branch_data)
        self.get_commit_detail = lru_cache(maxsize=256)(self._commit_detail)
        self.get_commit_language_stats = lru_cache(maxsize=128)(self._commit_language_stats)
        self.get_commit_files = lru_cache(maxsize=128)(self._commit_files)
        self.get_branch_divergence = lru_cache(maxsize=256)(self._branch_divergence)

    def invalidate_cached_git_data(self) -> None:
        self.get_branch_data.cache_clear()
        self.get_commit_detail.cache_clear()
        self.get_commit_language_stats.cache_clear()
        self.get_commit_files.cache_clear()
        self.get_branch_divergence.cache_clear()
        self._origin_head = None

    def _branch_divergence(self, branch: str, base: str) -> tuple[int, int]:
        """Commits `branch` is ahead of and behind `base`, both 0 when they match or
        are unknown."""
        if not base or branch == base:
            return (0, 0)
        try:
            ahead = int(run_git(self.repo_dir, ["rev-list", "--count", "--end-of-options", f"{base}..{branch}"]).strip() or "0")
            behind = int(run_git(self.repo_dir, ["rev-list", "--count", "--end-of-options", f"{branch}..{base}"]).strip() or "0")
        except (RuntimeError, ValueError):
            return (0, 0)
        return (ahead, behind)

    def trunk_divergence(self, branch: str, branches: list[str]) -> tuple[int, int]:
        """Commits `branch` is ahead of and behind the default branch."""
        base = self.default_base_branch(branches)
        return self.get_branch_divergence(branch, base) if base else (0, 0)

    def default_base_branch(self, branches: list[str]) -> str | None:
        """The trunk to measure branches against, or None if there is none.

        Prefers the local branch that origin/HEAD points at, then main, then master.
        """
        if self._origin_head is None:
            self._origin_head = self._read_origin_head()
        for candidate in (self._origin_head, "main", "master"):
            if candidate and candidate in branches:
                return candidate
        return None

    def _read_origin_head(self) -> str:
        """The branch name origin/HEAD points at, or an empty string."""
        try:
            target = run_git(
                self.repo_dir, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]
            ).strip()
        except RuntimeError:
            return ""
        prefix = "refs/remotes/origin/"
        return target.removeprefix(prefix) if target.startswith(prefix) else ""

    def update_language_settings(
        self,
        language_mode: str,
        exclude_languages: set[str],
        extra_ignored_dirs: set[str],
    ) -> None:
        """Re-point the language scan at new settings and drop the stale cache."""
        self.language_mode = language_mode
        self.exclude_languages = set(exclude_languages)
        self.extra_ignored_dirs = set(extra_ignored_dirs)
        self.ignore_dirs = _resolve_ignore_dirs(self.extra_ignored_dirs)
        self.get_commit_language_stats.cache_clear()
        self.get_commit_files.cache_clear()
        # Recount the header file count with the new excludes.
        self._cached_scanned_file_count = self._count_scanned_files()

    def _commit_language_stats(
        self, commit_hash: str
    ) -> tuple[list[LanguageStat], dict[str, list[str]]]:
        """Language breakdown of the whole tree at a commit from `git ls-tree`,
        with the same excludes as the working-tree scan."""
        output = run_git(self.repo_dir, ["ls-tree", "-r", "-l", "-z", commit_hash])
        totals: dict[str, int] = {}
        files_by_language: dict[str, list[str]] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            if not path:
                continue
            parts = meta.split()
            if len(parts) < 4 or parts[1] != "blob":
                continue
            if _in_ignored_dir(path, self.ignore_dirs):
                continue
            language = detect_language(path, EXTENSION_TO_LANGUAGE)
            if language in self.exclude_languages:
                continue
            files_by_language.setdefault(language, []).append(path)
            if self.language_mode == "files":
                value = 1
            else:
                try:
                    value = int(parts[3])
                except ValueError:
                    value = 0
            totals[language] = totals.get(language, 0) + value
        return _language_stats(totals, files_by_language)

    def _commit_files(self, commit_hash: str) -> list[str]:
        """Every file in the tree at a commit, sorted and without ignored directories."""
        output = run_git(self.repo_dir, ["ls-tree", "-r", "--name-only", "-z", commit_hash])
        paths = (path for path in output.split("\0") if path)
        return sorted(path for path in paths if not _in_ignored_dir(path, self.ignore_dirs))

    def get_cached_scanned_file_count(self) -> int | None:
        return self._cached_scanned_file_count

    def scanned_file_count(self, context: RepoContext) -> int:
        """Scanned file count, preferring the cached scan and falling back to
        the per-language file totals from the context."""
        return self.get_cached_scanned_file_count() or sum(
            len(files) for files in context.files_by_language.values()
        )

    def get_file_at_commit(self, commit_hash: str, path: str) -> str | None:
        """The text of a file at a commit, or None if it is binary, missing, or
        unreadable."""
        try:
            content = run_git(self.repo_dir, ["show", f"{commit_hash}:{path}"], errors="strict")
        except (RuntimeError, UnicodeDecodeError):
            return None
        return None if "\x00" in content else content

    def _count_scanned_files(self) -> int:
        files = _scanned_files(self.repo_dir, self.exclude_languages, self.ignore_dirs)
        return sum(1 for _ in files)

    def scan_repo_state(self) -> tuple[RepoScanSnapshot, float]:
        started = time.perf_counter()
        branches, current_branch, branch_heads = get_branch_heads(self.repo_dir)
        repo_commit_count = _repo_commit_count(self.repo_dir)
        scanned_file_count = self._count_scanned_files()
        self._cached_scanned_file_count = scanned_file_count
        snapshot = RepoScanSnapshot(
            branch_heads=tuple(sorted(branch_heads.items())),
            current_branch=current_branch,
            repo_commit_count=repo_commit_count,
            scanned_file_count=scanned_file_count,
        )
        return snapshot, time.perf_counter() - started

    def refresh_context(self) -> RepoContext:
        language_stats, files_by_language = scan_languages(
            self.repo_dir,
            self.language_mode,
            self.exclude_languages,
            self.ignore_dirs,
        )
        scanned_file_count = sum(len(files) for files in files_by_language.values())
        self._cached_scanned_file_count = scanned_file_count
        return _repo_context(self.repo_dir, language_stats, files_by_language)

    def _branch_data(self, branch: str) -> BranchData:
        meta_out = run_git(
            self.repo_dir,
            [
                "log",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%ad%x1f%an%x1f%ae%x1f%s",
                "--end-of-options",
                branch,
            ],
        )
        # A merge lists its files against the first parent, like the commit detail.
        # Older git ignores the config and repeats a merge once per parent. Only the first list is kept.
        files_out = run_git(
            self.repo_dir,
            [
                "-c",
                "log.diffMerges=first-parent",
                "log",
                "-m",
                "--find-renames",
                "--pretty=format:%x01%H",
                "--name-only",
                "--end-of-options",
                branch,
            ],
        )

        files_by_commit: dict[str, list[str]] = {}
        current_hash = ""
        for line in files_out.split("\n"):
            if not line:
                continue
            # Git quotes paths with control characters. No path line starts with \x01.
            if line.startswith("\x01"):
                commit_hash = line[1:]
                current_hash = "" if commit_hash in files_by_commit else commit_hash
                files_by_commit.setdefault(commit_hash, [])
                continue
            if current_hash:
                files_by_commit[current_hash].append(unquote_git_path(line))

        commits: list[CommitRecord] = []
        for line in meta_out.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f", 4)
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

    def _commit_detail(self, commit_hash: str) -> CommitDetail:
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
        meta_lines = meta_out.splitlines()
        if len(meta_lines) < 6:
            raise RuntimeError(f"Could not parse commit metadata for {commit_hash}")
        full_hash, parents_raw, author_name, author_email, authored_raw, subject = meta_lines[:6]
        body = "\n".join(meta_lines[6:]).strip()
        parents = tuple(part for part in parents_raw.split() if part)

        # A merge is compared with its first parent, like the commit table.
        compared = (
            ["--end-of-options", parents[0], full_hash]
            if parents
            else ["--root", "--end-of-options", full_hash]
        )
        diff_tree = ["diff-tree", "-r", "--no-commit-id", "--find-renames", "--no-textconv"]
        numstat_out = run_git(self.repo_dir, [*diff_tree, "--numstat", "-z", *compared])
        patch_out = run_git(self.repo_dir, [*diff_tree, "--patch", *compared])

        file_changes: list[CommitFileChange] = []
        records = numstat_out.split("\0")
        index = 0
        while index < len(records):
            parts = records[index].split("\t", 2)
            index += 1
            if len(parts) != 3:
                continue
            added_raw, deleted_raw, path = parts
            if not path:
                # A rename lists its old and new paths as the next two records.
                path = records[index + 1] if index + 1 < len(records) else ""
                index += 2
            if not path:
                continue
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
            parents=parents,
            author_name=author_name,
            author_email=author_email,
            authored_at=parse_iso_datetime(authored_raw),
            subject=subject,
            body=body,
            file_changes=tuple(file_changes),
            patch_sections=parse_patch_sections(patch_out),
            patch=patch_out.rstrip(),
        )
