# git-viewer-textual

A read-only, terminal-based viewer for a local Git repository. It opens on a
GitHub-style dashboard for a repo you already have on disk: a contribution
graph, the commit history for the selected branch and year, a details panel for
the highlighted commit, and a language breakdown. Drill into any commit to read
its diff and full patch. Nothing is ever written to the repository.

Built with [Textual](https://textual.textualize.io/). Where a general-purpose
git TUI like lazygit or tig is built around running git commands, this one is
built around reading a repository at a glance.

> Add a screenshot or short GIF here (for example `docs/demo.gif`).

## Layout

```
+-----------------------------------+---------------------------+
| Contribution Activity             | Commit Details            |
|  (year heatmap, one cell per day) |  (author, message, files  |
|                                   |   changed for the current |
+-----------------------------------+   commit)                 |
| Year Commits                      |                           |
|  (commit table for the selected   +---------------------------+
|   branch, year and range)         | Languages                 |
|                                   |  (percentage bar and per- |
|                                   |   language breakdown)     |
+-----------------------------------+---------------------------+
```

Move between the commit table, details, and languages panels with `Tab`.
Selecting a day on the graph, or highlighting a row in the commit table, updates
the details panel. Press `Enter` on a commit to open its diff, then `[` / `]` to
step to the previous / next commit without leaving the detail view. Press `Enter`
on a language to list the files that make it up.

## Install

```bash
pip install -e .
```

Requires Python 3.12 or newer. The only runtime dependency is `textual`.

## Run

```bash
git-viewer-textual /path/to/repo
```

Options:

```bash
git-viewer-textual /path/to/repo \
    --language-mode files \        # count languages by file instead of bytes
    --exclude-languages JSON,YAML \
    --ignore-dirs vendor,generated
```

## Themes

Ships with three themes: `git-viewer` (default), `one-night`, and `pro-black`.
Switch from the command palette (`Ctrl+P` -> "Change theme"); the choice is
remembered between runs. Reset to the default from the palette
("Reset theme to default").

The command palette also has "Graph: browse / select on move" to toggle whether
moving over the contribution graph selects a commit immediately (default) or
only previews it until you press Enter.

## Keys

Press `?` at any time for the in-app reference.

### Main screen

| Key | Action |
| --- | --- |
| `j` / `k` (or arrows) | Move within the focused panel |
| `Tab` / `Shift+Tab` | Switch between panels |
| `Enter` | Open the commit, or the highlighted language's files |
| `Left` / `Right` | Languages: prev / next language; Graph: back / forward a week |
| `h` / `l` | Select the older / newer active day |
| `[` / `]` | Step through years |
| `r` | Cycle the date range |
| `b` | Pick a branch |
| `?` | Show help |
| `q` | Quit |

### Commit detail

| Key | Action |
| --- | --- |
| `j` / `k` (or arrows) | Move within the focused pane |
| `Tab` / `Shift+Tab` | Switch between panes |
| `[` / `]` | Previous / next commit |
| `p` | Toggle file diff and full patch |
| `PageUp` / `PageDown` | Scroll by a page |
| `Home` / `End` | Jump to the top or bottom |
| `Esc` / `q` | Back to the main screen |

### Branch picker

| Key | Action |
| --- | --- |
| type | Filter the branch list |
| `Up` / `Down` | Move the highlight |
| `Enter` | Switch to the highlighted branch |
| `Esc` / `q` | Back to the main screen |

### Language files

| Key | Action |
| --- | --- |
| `Up` / `Down` | Scroll the file list |
| `Esc` / `q` | Back to the main screen |

## Development

```bash
pip install -e .
python3 -m pytest -q tests/test_navigation.py
```
