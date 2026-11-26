from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from vidi import languages, linguist_data, repository
from vidi.docs import README
from vidi.repository import RepoService, scan_languages

from sample_repo import git, new_repo, write_file

OBJECTIVE_C_HEADER = "#import <Foundation/Foundation.h>\n@interface Thing : NSObject\n@end\n"


def git_supports_check_attr_source() -> bool:
    output = subprocess.run(["git", "version"], capture_output=True, text=True).stdout
    version = output.split()[2].split(".")
    return (int(version[0]), int(version[1])) >= (2, 40)


class DetectionTests(unittest.TestCase):
    def test_detect_knows_common_extensions_and_filenames(self) -> None:
        cases = {
            "Makefile": "Makefile",
            "src/GNUmakefile": "Makefile",
            "CMakeLists.txt": "CMake",
            "Gemfile": "Ruby",
            "Rakefile": "Ruby",
            "Jenkinsfile": "Groovy",
            "Dockerfile": "Dockerfile",
            "web/App.vue": "Vue",
            "web/Page.svelte": "Svelte",
            "styles/site.scss": "SCSS",
            "infra/main.tf": "HCL",
            "notes/analysis.ipynb": "Jupyter Notebook",
            "lib/app.ex": "Elixir",
            "Main.hs": "Haskell",
            "api.proto": "Protocol Buffer",
            "src/App.PY": "Python",
        }
        for path, language in cases.items():
            with self.subTest(path=path):
                self.assertEqual(languages.detect(path), language)

    def test_ambiguous_extensions_are_settled_by_content(self) -> None:
        cases = [
            ("lib/a.h", OBJECTIVE_C_HEADER, "Objective-C"),
            ("lib/b.h", "#include <stdio.h>\nint f(void);\n", "C"),
            ("lib/c.h", "template <typename T> class Box {};\nnamespace n {}\n", "C++"),
            ("f.m", "% identity\nfunction y = f(x)\n  y = x;\nend\n", "MATLAB"),
            ("g.m", OBJECTIVE_C_HEADER, "Objective-C"),
            ("man/vidi.1", ".TH VIDI 1\n.SH NAME\nvidi\n", "Roff Manpage"),
        ]
        for path, content, language in cases:
            with self.subTest(path=path):
                self.assertTrue(languages.needs_content(path))
                self.assertEqual(languages.detect(path, content), language)

    def test_files_without_an_extension_use_the_shebang(self) -> None:
        cases = {
            "#!/usr/bin/env python3\nprint(1)\n": "Python",
            "#!/bin/bash\necho hi\n": "Shell",
            "#!/usr/bin/env -S node --flag\n": "JavaScript",
        }
        for content, language in cases.items():
            self.assertEqual(languages.detect("bin/tool", content), language)
        self.assertIsNone(languages.detect("bin/blob", "no shebang here\n"))
        self.assertIsNone(languages.detect("bin/empty"))

    def test_only_programming_and_markup_count_unless_detectable(self) -> None:
        for language in ("Python", "HTML", "CSS", "Shell"):
            self.assertTrue(languages.counted(language), language)
        for language in ("Markdown", "JSON", "YAML", "Text", "SQL"):
            self.assertFalse(languages.counted(language), language)
        self.assertTrue(languages.counted("JSON", detectable=True))
        self.assertFalse(languages.counted("Python", detectable=False))
        self.assertFalse(languages.needs_content("data.json"))
        self.assertTrue(languages.needs_content("data.json", detectable=True))

    def test_vendored_and_documentation_paths(self) -> None:
        for path in ("node_modules/x/a.js", "third_party/b.c", "web/c.min.js", ".github/demo.gif"):
            self.assertTrue(languages.is_vendored(path), path)
        documentation = ("docs/guide.py", "Docs/a.py", "man/vidi.1", "README.md", "CHANGELOG")
        for path in documentation:
            self.assertTrue(languages.is_documentation(path), path)
        for path in ("src/app.py", "tests/test_app.py", "src/documents.py"):
            self.assertFalse(languages.is_vendored(path) or languages.is_documentation(path), path)

    def test_groups_names_and_colors_follow_linguist(self) -> None:
        self.assertEqual(languages.group("TSX"), "TypeScript")
        self.assertEqual(languages.group("Roff Manpage"), "Roff")
        self.assertEqual(languages.group("Python"), "Python")
        self.assertEqual(languages.color("Python"), "#3572A5")
        self.assertEqual(languages.color("Vue"), "#41b883")
        self.assertEqual(languages.color("Text"), languages.UNKNOWN_COLOR)
        self.assertEqual(languages.find("objective-c"), "Objective-C")
        self.assertEqual(languages.find("JS"), "JavaScript")
        self.assertIsNone(languages.find("not a language"))

    def test_content_rules_run_in_a_worker_with_a_time_limit(self) -> None:
        crafted = "\n" * (50 * 1024) + "!"
        items = [
            ("a.h", "#include <stdio.h>\n"),
            ("b.h", crafted),
            ("c.h", OBJECTIVE_C_HEADER),
            ("bin/tool", "#!/bin/sh\n"),
            ("d.h", None),
        ]
        with mock.patch.object(languages, "CONTENT_SECONDS", 0.3):
            started = time.perf_counter()
            found = languages.detect_contents(items)
            took = time.perf_counter() - started
        self.assertEqual(found, ["C", "C", "Objective-C", "Shell", "C"])
        self.assertLess(took, 3.0)

    def test_files_after_repeated_timeouts_skip_their_content(self) -> None:
        crafted = "\n" * (50 * 1024) + "!"
        items = [(f"slow{i}.h", crafted) for i in range(3)] + [("late.h", OBJECTIVE_C_HEADER)]
        with mock.patch.object(languages, "CONTENT_SECONDS", 0.2):
            self.assertEqual(languages.detect_contents(items), ["C", "C", "C", "C"])

    def test_bundled_data_is_complete_and_ascii(self) -> None:
        self.assertTrue(linguist_data.LINGUIST_VERSION.startswith("v"))
        readme = " ".join(README.read_text(encoding="utf-8").split())
        self.assertIn(f"GitHub Linguist {linguist_data.LINGUIST_VERSION}.", readme)
        self.assertGreater(len(linguist_data.LANGUAGES), 500)
        with open(linguist_data.__file__, "rb") as handle:
            self.assertTrue(handle.read().isascii())


class ScanTests(unittest.TestCase):
    def build_repo(self, tmp: str) -> str:
        repo = new_repo(tmp)
        files = {
            "src/app.py": "print('app')\n",
            "web/view.tsx": "export const View = () => <div />;\n",
            "web/util.ts": "export const one = 1;\n",
            "lib/thing.h": OBJECTIVE_C_HEADER,
            "bin/tool": "#!/usr/bin/env python3\nprint('tool')\n",
            "README.md": "# Title\n",
            "data.json": "{}\n",
            "config.yml": "a: 1\n",
            "man/vidi.1": ".TH VIDI 1\n",
            "docs/guide.py": "print('guide')\n",
            "docs/notes.py": "print('notes')\n",
            "third_party/lib.js": "var lib;\n",
            "gen/out.py": "print('generated')\n",
            "odd.txt": "print('odd')\n",
            ".gitattributes": (
                "data.json linguist-detectable\n"
                "docs/guide.py -linguist-documentation\n"
                "gen/** linguist-generated\n"
                "odd.txt linguist-language=python\n"
            ),
        }
        for path, content in files.items():
            write_file(repo, path, content)
        with open(os.path.join(repo, "demo.gif"), "wb") as handle:
            handle.write(b"GIF89a\x01\x00\x01\x00\x00\x00\x00;")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "first")
        return repo

    EXPECTED = {
        "Python": ["bin/tool", "docs/guide.py", "odd.txt", "src/app.py"],
        "TypeScript": ["web/util.ts", "web/view.tsx"],
        "Objective-C": ["lib/thing.h"],
        "JSON": ["data.json"],
    }

    def test_work_tree_scan_follows_linguist_and_gitattributes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.build_repo(tmp)
            stats, files = scan_languages(repo, "bytes", set(), {".git"})
            self.assertEqual(files, self.EXPECTED)
            self.assertEqual(stats[0]["language"], "Python")
            colors = {stat["language"]: stat["color"] for stat in stats}
            self.assertEqual(colors["JSON"], "#292929")
            self.assertAlmostEqual(sum(stat["percentage"] for stat in stats), 100.0)

    def test_commit_view_matches_the_work_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.build_repo(tmp)
            tree_stats, tree_files = scan_languages(repo, "bytes", set(), {".git"})
            service = RepoService(repo, ignore_dirs={".git"})
            commit_stats, commit_files = service.get_commit_language_stats("HEAD")
            self.assertEqual(commit_files, tree_files)
            self.assertEqual(
                [(s["language"], s["value"]) for s in commit_stats],
                [(s["language"], s["value"]) for s in tree_stats],
            )

    def test_commit_view_reads_attributes_from_the_commit(self) -> None:
        if not git_supports_check_attr_source():
            self.skipTest("git check-attr --source needs git 2.40")
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.build_repo(tmp)
            write_file(repo, ".gitattributes", "")
            git(repo, "commit", "-q", "-am", "drop attributes")
            service = RepoService(repo, ignore_dirs={".git"})
            _, first = service.get_commit_language_stats("HEAD~1")
            _, second = service.get_commit_language_stats("HEAD")
            self.assertEqual(first, self.EXPECTED)
            self.assertNotIn("odd.txt", sum(second.values(), []))
            self.assertNotIn("JSON", second)

    def test_commit_view_falls_back_when_git_lacks_check_attr_source(self) -> None:
        real = repository.run_git

        def old_git(repo_dir, args, **kwargs):
            if any(arg.startswith("--source=") for arg in args):
                raise RuntimeError("error: unknown option `source'")
            return real(repo_dir, args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self.build_repo(tmp)
            service = RepoService(repo, ignore_dirs={".git"})
            with mock.patch.object(repository, "run_git", old_git), mock.patch.object(
                repository, "_check_attr_source", None
            ):
                _, files = service.get_commit_language_stats("HEAD")
                self.assertIs(repository._check_attr_source, False)
            self.assertEqual(files, self.EXPECTED)

    def test_empty_and_binary_files_are_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            write_file(repo, "src/app.py", "print('app')\n")
            write_file(repo, "src/__init__.py", "")
            write_file(repo, "lib/blob.h", "\0\1\2 binary\n")
            write_file(repo, "forced.py", "")
            write_file(repo, ".gitattributes", "forced.py linguist-language=Ruby\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "first")
            expected = {"Python": ["src/app.py"], "Ruby": ["forced.py"]}
            _, tree_files = scan_languages(repo, "files", set(), {".git"})
            service = RepoService(repo, ignore_dirs={".git"})
            _, commit_files = service.get_commit_language_stats("HEAD")
            self.assertEqual(tree_files, expected)
            self.assertEqual(commit_files, expected)

    def test_content_rules_read_only_the_start_of_a_file(self) -> None:
        # Linguist's heuristics read the first 50 KB.
        filler = "// note\n" * (50 * 1024 // 8 + 1)
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            write_file(repo, "a/late.h", filler + OBJECTIVE_C_HEADER)
            write_file(repo, "b/early.h", OBJECTIVE_C_HEADER + filler * 4)
            write_file(repo, "c/next.h", "template <typename T> class Box {};\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "first")
            expected = {"C": ["a/late.h"], "Objective-C": ["b/early.h"], "C++": ["c/next.h"]}
            _, tree_files = scan_languages(repo, "files", set(), {".git"})
            service = RepoService(repo, ignore_dirs={".git"})
            _, commit_files = service.get_commit_language_stats("HEAD")
            self.assertEqual(tree_files, expected)
            self.assertEqual(commit_files, expected)

    def test_an_edited_file_is_detected_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            write_file(repo, "lib/thing.h", "#include <stdio.h>\nint f(void);\n")
            _, before = scan_languages(repo, "files", set(), {".git"})
            write_file(repo, "lib/thing.h", OBJECTIVE_C_HEADER)
            _, after = scan_languages(repo, "files", set(), {".git"})
            self.assertEqual(before, {"C": ["lib/thing.h"]})
            self.assertEqual(after, {"Objective-C": ["lib/thing.h"]})

    def test_symbolic_links_are_not_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = new_repo(tmp)
            write_file(repo, "src/app.py", "print('app')\n")
            try:
                os.symlink("src/app.py", os.path.join(repo, "link.py"))
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "first")
            _, tree_files = scan_languages(repo, "files", set(), {".git"})
            service = RepoService(repo, ignore_dirs={".git"})
            _, commit_files = service.get_commit_language_stats("HEAD")
            self.assertEqual(tree_files, {"Python": ["src/app.py"]})
            self.assertEqual(commit_files, {"Python": ["src/app.py"]})


if __name__ == "__main__":
    unittest.main()
