"""Replay master's history once and track every production line.

master is squash-merged, so each commit is one PR. For each file we keep a list
holding the commit that wrote each line, then apply the diff of every commit.
That gives three facts for each line:

  corrected   a later fix or revert replaced it
  superseded  a later non-fix change replaced it (not a failure, tracking stops)
  alive       it is still there at the end of the history

It also records "built on": when a later commit edits within a few lines of a
line. Only diffs are read, never whole files, so this takes minutes.
"""
import collections
import json
import re
import subprocess
from datetime import datetime
from typing import NamedTuple

from config import WEEK

FIX_TITLE = re.compile(r"^\s*(fix|hotfix|revert)\b", re.I)
PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
BUILT_ON_RADIUS = 3  # lines either side of an edit that count as built on
MIN_MOVE_LENGTH = 12
BEFORE_HISTORY = -1  # marks a line older than the tracked history

# Plain patterns are far faster than glob ones, but their * crosses "/", so they
# only suit pure suffixes. Anything with a directory in it must stay a glob.
EXCLUDED_SUFFIXES = [
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.svg", "*.mp4", "*.wasm",
    "*.ambr", "*.snap", "*.lock", "*.md", "*.mdx", "*.csv", "*.parquet",
    "*.min.js", "*.map", "*.zst", "*.bin", "*.xml", "*.pdf",
    "*_pb2.py", "*.pb.go", "*_test.py", "*_test.go",
]
EXCLUDED_GLOBS = [
    "**/*.generated.*", "**/*-generated.*", "**/*_generated.*", "**/generated.*", "**/*.gen.*",
    "**/pnpm-lock.yaml", "**/__snapshots__/**", "**/generated/**", "**/migrations/**",
    "**/test/**", "**/tests/**", "**/__tests__/**", "**/fixtures/**",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py",
]


class Hunk(NamedTuple):
    index: int  # where the change starts in the old file
    removed: list  # fingerprint of each removed line
    added: list  # fingerprint of each added line


def area_of(path):
    """Group a file path into a product or subsystem area."""
    parts = path.split("/")
    if parts[0] in ("products", "rust", "services", "ee", "posthog", "common", "plugin-server") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    if parts[:3] == ["frontend", "src", "scenes"] and len(parts) > 4:
        return f"frontend/{parts[3]}"
    if parts[:2] == ["frontend", "src"] and len(parts) > 3:
        return f"frontend/{parts[2]}"
    if parts[:2] == ["nodejs", "src"] and len(parts) > 3:
        return f"nodejs/{parts[2]}"
    return parts[0] if len(parts) > 1 else "root"


class Replay:
    """Commits are numbered by position, and an "origin" is the commit that wrote a line."""

    def __init__(self):
        self.commits = []
        self.files = {}  # path -> origin of each line
        self.ended = collections.Counter()  # (origin, area, kind, weeks) -> lines
        self.built_on = []  # per commit: origin -> lines next to its edits
        self.areas = []  # per commit: the areas it wrote new lines in
        self.last_touch = {}  # path -> latest commit that changed the file

    def apply_commit(self, commit, renames, changes):
        """Apply one commit. A line deleted here and re-added elsewhere has moved:
        it keeps its original author and does not count as an ending."""
        commit_id = len(self.commits)
        self.commits.append(commit)
        self.built_on.append(collections.Counter())
        self.areas.append(set())
        for old_path, new_path in renames:
            self.files[new_path] = self.files.pop(old_path, [])
            if old_path in self.last_touch:
                self.last_touch[new_path] = self.last_touch.pop(old_path)

        wanted = collections.Counter(
            text for _, hunks, _ in changes for hunk in hunks for text in hunk.added if text
        )
        moved = collections.defaultdict(list)
        moved_positions = set()
        for path, hunks, _ in changes:
            lines = self.files.setdefault(path, [])
            for hunk in hunks:
                # Files older than the tracked history have lines we never saw.
                lines.extend([BEFORE_HISTORY] * (hunk.index + len(hunk.removed) - len(lines)))
                for offset, text in enumerate(hunk.removed):
                    if text and wanted[text] > 0:
                        wanted[text] -= 1
                        moved[text].append(lines[hunk.index + offset])
                        moved_positions.add((path, hunk.index + offset))

        for path, hunks, deleted in changes:
            self.apply_file(commit_id, commit, path, hunks, deleted, moved, moved_positions)

    def apply_file(self, commit_id, commit, path, hunks, deleted, moved, moved_positions):
        lines = self.files[path]
        area = area_of(path)
        kind = "corrected" if FIX_TITLE.search(commit["title"]) else "superseded"
        self.last_touch[path] = commit_id
        # Bottom to top, so earlier hunks keep their old-file positions.
        for hunk in reversed(hunks):
            index = hunk.index
            end = index + len(hunk.removed)
            neighbours = lines[max(0, index - BUILT_ON_RADIUS):index] + lines[end:end + BUILT_ON_RADIUS]
            for origin in neighbours:
                if origin != BEFORE_HISTORY:
                    self.built_on[commit_id][origin] += 1

            for position in range(index, end):
                origin = lines[position]
                if origin != BEFORE_HISTORY and (path, position) not in moved_positions:
                    weeks = (commit["date"] - self.commits[origin]["date"]) // WEEK
                    self.ended[(origin, area, kind, weeks)] += 1

            fresh = []
            for text in hunk.added:
                if text and moved.get(text):
                    fresh.append(moved[text].pop())
                else:
                    fresh.append(commit_id)
                    self.areas[commit_id].add(area)
            lines[index:end] = fresh
        if deleted:
            del self.files[path]

    def finish(self, end_time):
        for path, lines in self.files.items():
            area = area_of(path)
            last_touch = self.last_touch.get(path, BEFORE_HISTORY)
            for origin in lines:
                # A file nobody came back to tells us nothing about durability:
                # idle code cannot be fixed, so skip it rather than reward it.
                if BEFORE_HISTORY < origin < last_touch:
                    weeks = (end_time - self.commits[origin]["date"]) // WEEK
                    self.ended[(origin, area, "alive", weeks)] += 1


def parse_header(line):
    author, date, title = line[3:].decode("utf-8", "replace").rstrip("\n").split("\x1f", 2)
    number = PR_NUMBER.search(title)
    return {
        "author": author,
        "date": int(datetime.fromisoformat(date).timestamp()),
        "title": title,
        "pr": int(number.group(1)) if number else None,
    }


def fingerprint(line):
    """Identify a line for move detection. Short lines like `}` prove nothing."""
    text = line[1:].strip()
    return hash(text) if len(text) >= MIN_MOVE_LENGTH else None


def unquote(path):
    path = path.strip()
    return path[1:-1] if path.startswith('"') and path.endswith('"') else path


def parse_log(stream):
    """Yield (commit, renames, changes) from `git log --unified=0` output."""
    commit, renames, changes = None, [], []
    path, old_path, hunks, deleted = None, None, [], False
    hunk, left = None, 0

    def end_file():
        nonlocal hunks, deleted
        if path and (hunks or deleted):
            changes.append((path, hunks, deleted))
        hunks, deleted = [], False

    for line in stream:
        if left:
            if line.startswith(b"\\"):  # "\ No newline at end of file"
                continue
            left -= 1
            (hunk.removed if line.startswith(b"-") else hunk.added).append(fingerprint(line))
        elif line.startswith(b"@@@"):
            end_file()
            if commit:
                yield commit, renames, changes
            commit, renames, changes = parse_header(line), [], []
            path = None
        elif line.startswith(b"diff --git"):
            end_file()
            path, old_path = None, None
        elif line.startswith(b"rename from "):
            old_path = unquote(line.removeprefix(b"rename from ").decode("utf-8", "replace"))
        elif line.startswith(b"rename to "):
            path = unquote(line.removeprefix(b"rename to ").decode("utf-8", "replace"))
            renames.append((old_path, path))
        elif line.startswith(b"deleted file mode"):
            deleted = True
        elif line.startswith((b"--- ", b"+++ ")):
            name = unquote(line[4:].decode("utf-8", "replace"))
            if name != "/dev/null":
                path = name[2:]
        elif match := HUNK.match(line):
            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)  # git omits a count of 1
            new_count = int(match.group(4) or 1)
            hunk = Hunk(old_start - 1 if old_count else old_start, [], [])
            hunks.append(hunk)
            left = old_count + new_count
    end_file()
    if commit:
        yield commit, renames, changes


def replay(repo, since, large_paths):
    pathspecs = (
        [f":(exclude){pattern}" for pattern in EXCLUDED_SUFFIXES]
        + [f":(exclude,glob){pattern}" for pattern in EXCLUDED_GLOBS]
        + [f":(exclude,literal){path}" for path in large_paths]
    )
    command = [
        "git", "--no-lazy-fetch", "-C", repo, "-c", "core.quotepath=off", "log", "--reverse", "--first-parent",
        f"--since={since}", "-w", "-M", "-l10000", "--unified=0", "--no-color",
        "--format=@@@%an%x1f%cI%x1f%s", "--", ".", *pathspecs,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=1 << 20)
    state = Replay()
    for commit, renames, changes in parse_log(process.stdout):
        state.apply_commit(commit, renames, changes)
    code = process.wait()
    if code:
        raise RuntimeError(f"git log exited with {code}")
    return state


def run(repo, since, large_paths, out):
    """Write the line history. Every "origin" is a position in `commits`.

    end_time   date of the newest commit
    commits    {author, date, title, pr}, oldest first
    ended      [origin, area, kind, weeks after the commit, lines]
    built_on   per commit: [[origin, lines next to its edits], ...]
    areas      per commit: the areas it wrote new lines in
    """
    state = replay(repo, since, large_paths)
    end_time = max(c["date"] for c in state.commits)
    state.finish(end_time)

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({
            "end_time": end_time,
            "commits": state.commits,
            "ended": [[*key, value] for key, value in state.ended.items()],
            "built_on": [list(counts.items()) for counts in state.built_on],
            "areas": [sorted(areas) for areas in state.areas],
        }, handle)
    print("commits", len(state.commits), "tracked files", len(state.files),
          "line outcomes", len(state.ended), flush=True)
