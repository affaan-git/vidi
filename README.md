# vidi

A read-only, terminal-based viewer for a local Git repository. It opens on a
GitHub-style dashboard for a repo you already have on disk: a contribution
graph, the commit history for the selected branch and year, a details panel for
the highlighted commit, and the language breakdown of that commit's tree. Drill
into any commit to read its diff and full patch, or open any file as it was at
that commit. Nothing is ever written to the repository.

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
step to the previous / next commit without leaving the detail view. The Languages
panel is a snapshot of the highlighted commit's tree; press `Enter` on a language
to list its files at that commit, and `Enter` on a file to view its contents.
Press `f` (from any panel, or in the commit detail) to browse the whole tree; in
the file list `[` / `]` step between commits.

## Install

```bash
pip install -e .
```

Requires Python 3.12 or newer. The only runtime dependency is `textual`.

## Run

```bash
vidi /path/to/repo
```

It's short enough to type as-is; or alias it. Both `vd` (its initials) and `gv`
("git view", to sit next to `ga`, `gd`, ...) work, since the alias and the
command name are independent:

```bash
alias vd='vidi'   # or: alias gv='vidi'
```

Everything else is set from inside the app (command palette, `Ctrl+P`) and
remembered between runs -- there are no other flags.

## Themes

Ships with three themes: `vidi` (default), `one-night`, and `pro-black`.
Switch from the command palette (`Ctrl+P` -> "Change theme"); the choice is
remembered between runs. Reset to the default from the palette
("Reset theme to default").

The command palette also has:

- "Transparent background" - use the terminal's background (so a translucent
  terminal shows through) while keeping the theme's borders, selection, and text.
- "Graph: browse / select on move" - toggle whether moving over the contribution
  graph moves the commit-table selection immediately (default) or only on Enter.
- "Language settings" - choose whether languages are measured by bytes or file
  count, exclude languages, and add directories to ignore in the scan.

All of these persist between runs.

## Keys

Press `?` at any time for the in-app reference.

### Main screen

| Key | Action |
| --- | --- |
| `j` / `k` (or arrows) | Move within the focused panel |
| `Tab` / `Shift+Tab` | Switch between panels |
| `Enter` | Open the commit, or the highlighted language's files |
| `f` | Browse the highlighted commit's files |
| `/` | Filter the commit table by message, author, hash or date (`Esc` clears) |
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
| `f` | Browse this commit's files |
| `/` | Filter the files list (on the files pane) or search the patch |
| `Enter` / `Shift+Enter` | Next / previous patch match while searching |
| `n` / `N` | Next / previous patch match |
| `PageUp` / `PageDown` | Scroll by a page |
| `Home` / `End` | Jump to the top or bottom |
| `Esc` / `q` | Back (`Esc` first closes the search) |

### Branch picker

Each branch shows how far it is `↑` ahead / `↓` behind `main` (or `master`).

| Key | Action |
| --- | --- |
| type | Filter the branch list |
| `Up` / `Down` | Move the highlight |
| `Enter` | Switch to the highlighted branch |
| `Esc` / `q` | Back to the main screen |

### Commit files

Reached with `f` (the whole tree) or `Enter` on a language (just that language).
Files changed in the commit are marked with a dot.

| Key | Action |
| --- | --- |
| `j` / `k` (or arrows) | Move between files |
| `/` | Filter the list (type to narrow, `Esc` clears) |
| `[` / `]` | Previous / next commit |
| `Enter` | Open the highlighted file |
| `Esc` / `q` | Back |

### File view

| Key | Action |
| --- | --- |
| `j` / `k` (or arrows) | Scroll (arrows scroll even while searching) |
| `/` | Search in the file |
| `Enter` / `Shift+Enter` | Next / previous match while searching |
| `n` / `N` | Next / previous match |
| `[` / `]` | Previous / next file |
| `PageUp` / `PageDown` | Scroll by a page |
| `Home` / `End` | Jump to the top or bottom |
| `Esc` / `q` | Back (`Esc` first closes the search) |

## Development

```bash
pip install -e .
python3 -m pytest -q tests/test_navigation.py
```
