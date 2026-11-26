"""Repository discovery and git-backed data access."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from functools import lru_cache
from typing import Callable

from . import languages
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
    parse_iso_datetime,
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

def _git_command(args: list[str]) -> list[str]:
    return [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.quotePath=false",
        "-c",
        "log.showSignature=false",
        *args,
    ]


def run_git(
    repo_dir: str, args: list[str], *, errors: str = "replace", input: str | None = None
) -> str:
    """Run git and return its output with non-ASCII paths left unescaped.

    Bytes that are not UTF-8 become U+FFFD unless `errors` is "strict".
    """
    # Python 3.9 turns input=None into b"" and then fails to encode it in text mode.
    extra = {} if input is None else {"input": input}
    try:
        output = subprocess.check_output(
            _git_command(args),
            cwd=repo_dir,
            encoding="utf-8",
            errors=errors,
            stderr=subprocess.PIPE,
            **extra,
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


LINGUIST_ATTRIBUTES = (
    "linguist-language",
    "linguist-vendored",
    "linguist-generated",
    "linguist-documentation",
    "linguist-detectable",
)
# Whether git supports `check-attr --source`, which needs git 2.40. Set by the first commit view.
_check_attr_source: bool | None = None


def _linguist_attributes(
    repo_dir: str, paths: list[str], source: str | None = None
) -> dict[str, dict[str, str]]:
    """The linguist attributes .gitattributes sets on each path, from `source` when given."""
    global _check_attr_source
    if not paths:
        return {}
    options = ["check-attr", "-z", "--stdin"]
    request = "\0".join(paths)
    output = None
    if source and _check_attr_source is not False:
        try:
            output = run_git(
                repo_dir, [*options, f"--source={source}", *LINGUIST_ATTRIBUTES], input=request
            )
            _check_attr_source = True
        except RuntimeError:
            _check_attr_source = False
    if output is None:
        output = run_git(repo_dir, [*options, *LINGUIST_ATTRIBUTES], input=request)
    fields = output.split("\0")
    found: dict[str, dict[str, str]] = {}
    for index in range(0, len(fields) - 2, 3):
        path, name, value = fields[index : index + 3]
        if value != "unspecified":
            found.setdefault(path, {})[name] = value
    return found


def _flag(value: str | None) -> bool | None:
    if value in ("set", "true"):
        return True
    if value in ("unset", "false"):
        return False
    return None


# Languages detected from file contents, keyed by path and content version.
_detected: dict[tuple, str | None] = {}
DETECTED_LIMIT = 200_000
_UNREAD = object()


def _classify(
    paths: list[str],
    attributes: dict[str, dict[str, str]],
    read: Callable[[list[str]], dict[str, str]],
    sizes: dict[str, int],
    versions: dict[str, tuple],
) -> dict[str, str]:
    """The name each counted path is listed under, following Linguist's rules.

    `versions` identifies each file's contents, for reusing earlier detections.
    """
    pending: list[tuple[str, str | None, bool | None]] = []
    wanted: list[str] = []
    known: dict[str, str | None] = {}
    for path in paths:
        marks = attributes.get(path, {})
        vendored = _flag(marks.get("linguist-vendored"))
        documentation = _flag(marks.get("linguist-documentation"))
        if vendored or (vendored is None and languages.is_vendored(path)):
            continue
        if documentation or (documentation is None and languages.is_documentation(path)):
            continue
        if _flag(marks.get("linguist-generated")):
            continue
        forced = languages.find(marks.get("linguist-language"))
        # Linguist leaves empty files without a language unless one is set.
        if forced is None and sizes[path] == 0:
            continue
        detectable = _flag(marks.get("linguist-detectable"))
        pending.append((path, forced, detectable))
        if forced is None and languages.needs_content(path, detectable):
            cached = _detected.get(versions[path], _UNREAD)
            if cached is _UNREAD:
                wanted.append(path)
            else:
                known[path] = cached
    if wanted:
        if len(_detected) > DETECTED_LIMIT:
            _detected.clear()
        contents = read(wanted)
        items = []
        for path in wanted:
            content = contents.get(path)
            if content is not None and "\0" in content:
                known[path] = None
            else:
                items.append((path, content))
        for (path, _), language in zip(items, languages.detect_contents(items)):
            known[path] = language
        for path in wanted:
            _detected[versions[path]] = known[path]
    names: dict[str, str] = {}
    for path, forced, detectable in pending:
        language = forced or (known[path] if path in known else languages.detect(path))
        if language and languages.counted(language, detectable):
            names[path] = languages.group(language)
    return names


def _read_worktree(repo_dir: str, paths: list[str]) -> dict[str, str]:
    contents = {}
    for path in paths:
        try:
            with open(os.path.join(repo_dir, path), "rb") as handle:
                start = handle.read(languages.CONTENT_LIMIT)
        except OSError:
            continue
        contents[path] = start.decode("utf-8", errors="replace")
    return contents


def _read_blobs(repo_dir: str, object_ids: dict[str, str]) -> dict[str, str]:
    """The start of each blob's text, keyed like `object_ids`.

    Blobs are asked for one at a time and only the start of each is kept in memory.
    """
    contents: dict[str, str] = {}
    if not object_ids:
        return contents
    try:
        process = subprocess.Popen(
            _git_command(["cat-file", "--batch"]),
            cwd=repo_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return contents
    with process:
        requests, replies = process.stdin, process.stdout
        try:
            for key, object_id in object_ids.items():
                requests.write(f"{object_id}\n".encode())
                requests.flush()
                header = replies.readline().split()
                if not header:
                    break
                # A missing object has a two-field header and no contents.
                if len(header) < 3:
                    continue
                size = int(header[2])
                start = replies.read(min(size, languages.CONTENT_LIMIT))
                # The rest of the object and the newline after it.
                left = size - len(start) + 1
                while left > 0:
                    skipped = replies.read(min(left, 1 << 16))
                    if not skipped:
                        break
                    left -= len(skipped)
                if header[1] == b"blob":
                    contents[key] = start.decode("utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    return contents


def _totals(
    names: dict[str, str],
    sizes: dict[str, int],
    language_mode: str,
    exclude_languages: set[str],
) -> tuple[list[LanguageStat], dict[str, list[str]]]:
    totals: dict[str, int] = {}
    files_by_language: dict[str, list[str]] = {}
    for path, name in names.items():
        if name in exclude_languages:
            continue
        files_by_language.setdefault(name, []).append(path)
        value = 1 if language_mode == "files" else sizes.get(path, 0)
        totals[name] = totals.get(name, 0) + value
    return _language_stats(totals, files_by_language)


def scan_languages(
    repo_dir: str,
    language_mode: str,
    exclude_languages: set[str],
    ignore_dirs: set[str],
) -> tuple[list[LanguageStat], dict[str, list[str]]]:
    """The work tree's languages, counted the way GitHub's Linguist counts them."""
    sizes: dict[str, int] = {}
    versions: dict[str, tuple] = {}
    for path in list_worktree_paths(repo_dir):
        if _in_ignored_dir(path, ignore_dirs):
            continue
        try:
            info = os.lstat(os.path.join(repo_dir, path))
        except OSError:
            continue
        # Sym links are skipped like Linguist skips them.
        if not stat.S_ISREG(info.st_mode):
            continue
        sizes[path] = info.st_size
        versions[path] = (repo_dir, path, info.st_size, info.st_mtime_ns)
    paths = list(sizes)
    names = _classify(
        paths,
        _linguist_attributes(repo_dir, paths),
        lambda wanted: _read_worktree(repo_dir, wanted),
        sizes,
        versions,
    )
    return _totals(names, sizes, language_mode, exclude_languages)


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
                "color": languages.color(language),
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
        count = ["rev-list", "--count", "--end-of-options"]
        try:
            ahead = int(run_git(self.repo_dir, [*count, f"{base}..{branch}"]).strip() or "0")
            behind = int(run_git(self.repo_dir, [*count, f"{branch}..{base}"]).strip() or "0")
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
        with the same rules and excludes as the working-tree scan."""
        output = run_git(self.repo_dir, ["ls-tree", "-r", "-l", "-z", commit_hash])
        sizes: dict[str, int] = {}
        object_ids: dict[str, str] = {}
        for record in output.split("\0"):
            meta, _, path = record.partition("\t")
            parts = meta.split()
            # Linguist skips submodules and sym links which git stores with mode 120000.
            if not path or len(parts) < 4 or parts[1] != "blob" or parts[0] == "120000":
                continue
            if _in_ignored_dir(path, self.ignore_dirs):
                continue
            sizes[path] = int(parts[3]) if parts[3].isdigit() else 0
            object_ids[path] = parts[2]
        paths = list(sizes)
        names = _classify(
            paths,
            _linguist_attributes(self.repo_dir, paths, source=commit_hash),
            lambda wanted: _read_blobs(self.repo_dir, {path: object_ids[path] for path in wanted}),
            sizes,
            {path: (path, object_id) for path, object_id in object_ids.items()},
        )
        return _totals(names, sizes, self.language_mode, self.exclude_languages)

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
        _, files_by_language = scan_languages(
            self.repo_dir, self.language_mode, self.exclude_languages, self.ignore_dirs
        )
        return sum(len(files) for files in files_by_language.values())

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
        # Older git ignores the config and repeats a merge once per parent.
        # Only the first list is kept.
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
