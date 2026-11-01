"""Line history, replayed from real git repositories built to a known shape."""
import json
import subprocess

import pytest

import find_large_paths
import replay
from helpers import days_ago, numbered_lines

CORE = "products/core/engine.py"


def run(repo_path, tmp_path, large_paths=()):
    out = tmp_path / "replay.json"
    replay.run(str(repo_path), days_ago(100).date().isoformat(), list(large_paths), out)
    return History(json.loads(out.read_text()))


class History:
    def __init__(self, data):
        self.data = data
        self.prs = [c["pr"] for c in data["commits"]]

    def ended(self, pr, kind):
        origin = self.prs.index(pr)
        return sum(lines for row_origin, _, k, _, lines in self.data["ended"] if row_origin == origin and k == kind)

    def built_on(self, pr):
        """Earlier PR -> lines next to this PR's edits."""
        return {self.prs[origin]: lines for origin, lines in self.data["built_on"][self.prs.index(pr)]}


@pytest.fixture
def core(repo):
    repo.write(CORE, numbered_lines("core", 30))
    repo.commit(60, "Alice", "feat: add engine (#1)")
    return repo


def test_an_edit_builds_on_the_lines_around_it(core, tmp_path):
    core.edit(CORE, 10, "changed by bob and long enough")
    core.commit(50, "Bob", "feat: tune engine (#2)")

    history = run(core.path, tmp_path)

    assert history.built_on(2) == {1: 6}  # three lines either side
    assert history.ended(1, "superseded") == 1
    assert history.ended(1, "corrected") == 0


@pytest.mark.parametrize("title, kind", [
    ("fix: broken engine (#2)", "corrected"),
    ("fix(core): broken engine (#2)", "corrected"),
    ("hotfix: engine (#2)", "corrected"),
    ('Revert "feat: add engine (#1)" (#2)', "corrected"),
    ("fixture: new sample data (#2)", "superseded"),
    ("feat: fix the docs wording (#2)", "superseded"),
])
def test_only_fix_and_revert_titles_count_as_corrections(core, tmp_path, title, kind):
    core.edit(CORE, 10, "changed line that is long enough")
    core.commit(50, "Bob", title)

    assert run(core.path, tmp_path).ended(1, kind) == 1


def test_moved_lines_keep_their_author_and_never_end(core, tmp_path):
    lines = core.read(CORE)
    core.write(CORE, lines[:5] + lines[15:])
    core.write("products/core/moved.py", lines[5:15])
    core.commit(50, "Dave", "refactor: split engine (#2)")
    core.edit("products/core/moved.py", 5, "edit inside the moved block")
    core.commit(40, "Erin", "feat: extend moved code (#3)")

    history = run(core.path, tmp_path)

    assert history.ended(1, "superseded") == 1  # only Erin's edit ended a line
    assert history.built_on(3) == {1: 6}  # credit reaches Alice, not Dave


def test_short_lines_are_not_treated_as_moves(repo, tmp_path):
    repo.write("products/a/one.py", ["}", "}", "}"])
    repo.commit(60, "Alice", "feat: one (#1)")
    repo.write("products/a/one.py", ["x"])
    repo.write("products/a/two.py", ["}", "}", "}"])
    repo.commit(50, "Bob", "feat: two (#2)")

    assert run(repo.path, tmp_path).ended(1, "superseded") == 3


def test_renamed_files_keep_their_history(core, tmp_path):
    (core.path / "products/renamed").mkdir()
    core.git("mv", CORE, "products/renamed/engine.py")
    core.commit(50, "Dave", "chore: rename (#2)")
    core.edit("products/renamed/engine.py", 10, "edit after the rename here")
    core.commit(40, "Erin", "feat: extend (#3)")

    history = run(core.path, tmp_path)

    assert history.built_on(3) == {1: 6}
    assert history.ended(1, "superseded") == 1


def test_deleting_a_file_ends_all_its_lines(core, tmp_path):
    (core.path / CORE).unlink()
    core.commit(50, "Bob", "chore: remove engine (#2)")

    assert run(core.path, tmp_path).ended(1, "superseded") == 30


def test_idle_code_says_nothing_about_durability(repo, tmp_path):
    repo.write("products/idle/a.py", numbered_lines("idle", 10))
    repo.commit(60, "Alice", "feat: idle (#1)")
    repo.write("products/busy/b.py", numbered_lines("busy", 10))
    repo.commit(59, "Alice", "feat: busy (#2)")
    repo.edit("products/busy/b.py", 1, "someone came back to this file")
    repo.commit(50, "Bob", "feat: touch busy (#3)")

    history = run(repo.path, tmp_path)

    assert history.ended(1, "alive") == 0
    assert history.ended(2, "alive") == 9


def test_every_change_in_a_commit_lands_on_the_right_line(repo, tmp_path):
    """An insert high in a file must not shift a later edit in the same commit."""
    repo.write(CORE, numbered_lines("core", 30))
    repo.commit(60, "Alice", "feat: add engine (#1)")
    repo.edit(CORE, 20, "line twenty rewritten by bob")
    repo.commit(55, "Bob", "feat: tune engine (#2)")
    lines = repo.read(CORE)
    lines[19] = "line twenty rewritten by carol"
    repo.write(CORE, lines[:5] + ["inserted line one is here", "inserted line two is here"] + lines[5:])
    repo.commit(50, "Carol", "feat: tune again (#3)")

    history = run(repo.path, tmp_path)

    assert history.ended(2, "superseded") == 1  # Carol replaced Bob's line
    assert history.ended(1, "superseded") == 1  # and Bob had replaced one of Alice's


def write_without_final_newline(repo, name, lines):
    path = repo.path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def test_lines_without_a_final_newline_are_handled(repo, tmp_path):
    name = "products/a/script.py"
    write_without_final_newline(repo, name, ["first long enough line here", "second long enough line here"])
    repo.commit(60, "Alice", "feat: script (#1)")
    write_without_final_newline(repo, name, ["first long enough line here", "second line changed by bob"])
    repo.commit(50, "Bob", "feat: change script (#2)")

    assert run(repo.path, tmp_path).ended(1, "superseded") == 1


def test_git_notes_about_final_newlines_are_not_read_as_code(repo, tmp_path):
    """git prints a note after each such line, which must not hide the next one."""
    moved = "this line moves between two files"
    write_without_final_newline(repo, "products/a/one.py", ["context line that stays here", moved])
    repo.commit(60, "Alice", "feat: one (#1)")
    write_without_final_newline(repo, "products/a/two.py", ["context line for the other", "bobs original last line"])
    repo.commit(59, "Bob", "feat: two (#2)")
    write_without_final_newline(repo, "products/a/one.py", ["context line that stays here", "a replacement line"])
    write_without_final_newline(repo, "products/a/two.py", ["context line for the other", moved])
    repo.commit(50, "Carol", "refactor: move the line (#3)")

    history = run(repo.path, tmp_path)

    assert history.ended(1, "superseded") == 0  # Alice's line moved
    assert history.ended(2, "superseded") == 1  # Bob's was replaced


@pytest.mark.parametrize("path", [
    "products/core/tests/test_engine.py",
    "products/core/engine.test.ts",
    "products/core/engine_test.py",
    "frontend/src/schema.generated.ts",
    "products/core/migrations/0001_init.py",
    "products/core/__snapshots__/view.ambr",
    "docs/guide.md",
    "products/core/README.md",
    "frontend/public/data/sample.csv",
    "frontend/src/vendor/lib.min.js",
    "services/deep/nested/pnpm-lock.yaml",
])
def test_tests_generated_code_and_docs_are_ignored(repo, tmp_path, path):
    repo.write(path, numbered_lines("skip", 10))
    repo.commit(60, "Alice", "feat: skipped file (#1)")
    repo.edit(path, 5, "edit that must not be tracked at all")
    repo.commit(50, "Bob", "feat: skipped edit (#2)")
    repo.write("products/core/real.py", numbered_lines("real", 5))
    repo.commit(40, "Carol", "feat: tracked (#3)")

    assert run(repo.path, tmp_path).prs == [3]


def test_a_directory_named_like_a_suffix_is_not_excluded(repo, tmp_path):
    repo.write("products/test_fixtures/bundle/run.py", numbered_lines("real", 5))
    repo.commit(40, "Carol", "feat: tracked (#1)")

    assert run(repo.path, tmp_path).prs == [1]


def test_files_over_the_clone_limit_are_found_and_skipped(repo, tmp_path):
    """A real partial clone, with a path that would break if treated as a pattern."""
    big = "products/[slug]/données.json"
    repo.write(big, ["x" * 1000] * 1100)
    repo.commit(60, "Bob", "chore: big file (#1)")
    repo.edit(big, 1, "y" * 1000)
    repo.commit(55, "Bob", "chore: change big file (#2)")
    repo.write("products/core/real.py", numbered_lines("real", 5))
    repo.commit(50, "Carol", "feat: tracked (#3)")
    clone = tmp_path / "clone"
    subprocess.run([
        "git", "clone", "--quiet", "--single-branch", "--no-checkout", "--filter=blob:limit=1m",
        repo.path.as_uri(), str(clone),
    ], check=True)

    paths = find_large_paths.large_paths(str(clone))

    assert paths == [big]
    assert run(clone, tmp_path, large_paths=paths).prs == [3]


def test_commits_before_the_tracked_history_are_not_replayed(repo, tmp_path):
    repo.write(CORE, numbered_lines("core", 10))
    repo.commit(150, "Alice", "feat: ancient (#1)")
    repo.edit(CORE, 5, "edit after the tracked history starts")
    repo.commit(50, "Bob", "feat: recent (#2)")

    history = run(repo.path, tmp_path)

    assert history.prs == [2]
    assert history.built_on(2) == {}


@pytest.mark.parametrize("path, area", [
    ("products/web/backend/api.py", "products/web"),
    ("rust/capture/src/main.rs", "rust/capture"),
    ("frontend/src/scenes/insights/view.tsx", "frontend/insights"),
    ("frontend/src/lib/components/x.tsx", "frontend/lib"),
    ("nodejs/src/ingestion/run.ts", "nodejs/ingestion"),
    ("bin/start", "bin"),
    ("setup.py", "root"),
])
def test_paths_group_into_product_areas(path, area):
    assert replay.area_of(path) == area
