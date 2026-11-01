"""The whole job on a small PostHog: git clone, PR sync, replay, score, check.

The story, so each expectation below can be worked out by hand:

  Alice wrote the engine and six colleagues built on it. Nobody else has that.
  Hugo shipped the most PRs, all autonomous and all ignored, so he ranks nowhere.
  A bot and Hugo also edited the engine, and neither may count as a builder.
  Grace reviewed everyone. Only her written reviews count, not LGTMs or agents.
  Alice reviewed a docs-only PR, which has no line history but still counts.
  In the last week everyone shipped something a colleague then built on.
"""
import json
from types import SimpleNamespace

import pytest

import check
import fetch_prs
import run
from helpers import FakeGitHub, Repo, days_ago, make_pr, numbered_lines

ENGINE = "products/core/engine.py"
TEAM = ["Bob", "Carol", "Dave", "Erin", "Frank", "Grace"]


class World:
    def __init__(self, path):
        self.repo = Repo(path)
        self.github = FakeGitHub()
        self.next_pr = 100

    def merge(self, age, name, login, title, **pr_fields):
        number = self.next_pr
        self.next_pr += 1
        self.repo.commit(age, name, f"{title} (#{number})")
        day = days_ago(age).date().isoformat()
        self.github.prs_by_day.setdefault(day, []).append(make_pr(number, login, days_ago(age), **pr_fields))
        return number

    def own_file(self, age, name, title="feat: own module"):
        """Each person also owns a small module in their own area."""
        login = name.lower()
        self.repo.write(f"products/{login}/module.py", numbered_lines(login, 20))
        return self.merge(age, name, login, title)


def build_world(path):
    world = World(path)
    repo = world.repo

    # Older than every window: teaches how credit builds up over time.
    repo.write("products/old/base.py", numbered_lines("old", 40))
    repo.commit(150, "Ivy", "feat: base (#1)")
    for offset, age in enumerate((149, 145, 135, 125)):
        repo.edit("products/old/base.py", 5 + offset * 8, f"old edit number {offset} goes here")
        repo.commit(age, "Jack", f"feat: edit base (#{2 + offset})")

    repo.write(ENGINE, numbered_lines("engine", 100))
    world.engine_pr = world.merge(80, "Alice", "alice", "feat: add engine")
    for offset, name in enumerate(TEAM):
        world.own_file(75 - offset, name)

    # Six colleagues build on the engine, and on each other's modules.
    for offset, name in enumerate(TEAM):
        repo.edit(ENGINE, 10 + offset * 14, f"improved by {name} in a meaningful way")
        world.merge(60 - offset, name, name.lower(), "feat: extend engine")
    for offset, name in enumerate(TEAM):
        neighbour = TEAM[(offset + 1) % len(TEAM)].lower()
        repo.edit(f"products/{neighbour}/module.py", 10, f"{name} extended this module today")
        world.merge(50 - offset, name, name.lower(), "feat: extend module")

    # Neither of these may count as a builder of the engine.
    repo.edit(ENGINE, 95, "dependency bump from the bot goes here")
    world.merge(40, "dependabot[bot]", "dependabot[bot]", "chore: bump", bot=True)
    repo.edit(ENGINE, 92, "autonomous edit from hugos agent here")
    world.merge(39, "Hugo", "hugo", "feat: agent tweak", autonomy="Fully autonomous")

    # Hugo is the most prolific person, and nobody ever touches his files.
    for n in range(20):
        repo.write(f"products/hugo/file{n}.py", numbered_lines(f"hugo{n}", 10))
        world.merge(38 - n % 30, "Hugo", "hugo", "feat: agent module", autonomy="Fully autonomous")

    # Docs are not tracked as code, but reviewing them is still a real review.
    repo.write("docs/guide.md", numbered_lines("guide", 5))
    world.merge(20, "Grace", "grace", "docs: add guide", reviews=[("alice", "The second step is out of date.")])

    # Grace reviews everyone; a disclosed agent review and an LGTM must not count.
    reviews = [("grace", "This retry can loop forever when the token expires.")]
    for name in TEAM[:5]:
        repo.write(f"products/{name.lower()}/review.py", numbered_lines(f"{name}review", 6))
        world.merge(15, name, name.lower(), "feat: reviewed work", reviews=reviews)
    repo.write("products/alice/review.py", numbered_lines("alicereview", 6))
    world.merge(14, "Alice", "alice", "feat: reviewed work", reviews=reviews + [
        ("bob", "Automated review: nothing found."),
        ("carol", "LGTM"),
    ])

    # A normal last week: everyone ships something and a colleague builds on it.
    week = ["Alice"] + TEAM
    for name in week:
        repo.write(f"products/{name.lower()}/weekly.py", numbered_lines(f"{name}weekly", 12))
        world.merge(6, name, name.lower(), "feat: weekly work")
    for offset, name in enumerate(week):
        neighbour = week[(offset + 1) % len(week)].lower()
        repo.edit(f"products/{neighbour}/weekly.py", 6, f"{name} extended the weekly work here")
        world.merge(5, name, name.lower(), "feat: extend weekly work")

    repo.write("products/erin/fresh.py", numbered_lines("fresh", 12))
    world.merge(3, "Erin", "erin", "feat: fresh module")
    repo.edit("products/erin/fresh.py", 6, "frank builds on the fresh module now")
    world.merge(1, "Frank", "frank", "feat: extend fresh module")
    return world


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    world = build_world(tmp_path_factory.mktemp("posthog"))
    yield world
    world.github.close()


def run_job(world, base):
    run.main(world.repo.path.as_uri(), base / "data", base / "impact.json")


@pytest.fixture(scope="module")
def job(world, tmp_path_factory):
    """Run the real entry point twice, exactly as two days of the workflow would."""
    patch = pytest.MonkeyPatch()
    patch.setattr(fetch_prs, "API", world.github.url)
    patch.setenv("GH_TOKEN", "test-token")
    base = tmp_path_factory.mktemp("job")

    run_job(world, base)
    first = (base / "impact.json").read_text(encoding="utf-8")
    queries_first_run = len(world.github.queries)
    run_job(world, base)
    second = (base / "impact.json").read_text(encoding="utf-8")
    patch.undo()

    return SimpleNamespace(
        output=base / "impact.json",
        data=json.loads(first),
        first_output=first,
        second_output=second,
        queries_first_run=queries_first_run,
        queries_second_run=len(world.github.queries) - queries_first_run,
    )


def window(job, days):
    return next(w for w in job.data["windows"] if w["days"] == days)


def person(job, days, login):
    return next((p for p in window(job, days)["top"] if p["login"] == login), None)


def test_publishes_three_windows_with_a_top_five_each(job):
    assert [w["days"] for w in job.data["windows"]] == [90, 30, 7]
    assert all(len(w["top"]) == 5 for w in job.data["windows"])


def test_top_five_is_ordered_by_share(job):
    for days in (90, 30, 7):
        shares = [p["top5"] for p in window(job, days)["top"]]
        assert shares == sorted(shares, reverse=True)


def test_the_person_everyone_built_on_is_chosen_under_every_weighting(job):
    alice = person(job, 90, "alice")
    assert alice["name"] == "Alice"
    assert alice["top5"] == 100
    assert alice["builders"] == max(p["builders"] for p in window(job, 90)["top"])


def test_bots_and_autonomous_prs_are_not_builders(job):
    assert person(job, 90, "alice")["builders"] == len(TEAM)


def test_the_highest_volume_person_is_not_in_the_top_five(job):
    assert person(job, 90, "hugo") is None
    assert all(p["volumeRank"] > 1 for p in window(job, 90)["top"])


def test_the_evidence_links_to_the_right_pr(job, world):
    evidence = person(job, 90, "alice")["evidence"]
    assert evidence[0] == {"number": world.engine_pr, "title": "feat: add engine"}


def test_only_real_reviews_count(job):
    assert person(job, 90, "alice")["reviews"] == 1  # the docs PR
    reviewed_prs = 6 + 1  # Grace's six and Alice's one, not the LGTM or the agent's
    assert window(job, 90)["writtenReviewPrs"] == pytest.approx(100 * reviewed_prs / window(job, 90)["humanPrs"], abs=0.1)


def test_counts_people_and_prs_exactly(job):
    expected_prs = (
        1 + 6      # Alice's engine, one module each
        + 6 + 6    # engine and module edits
        + 1 + 20   # Hugo's edit and modules
        + 1        # the docs PR, which has no line history
        + 5 + 1    # reviewed work
        + 7 + 7    # the last week's work and follow-ups
        + 2        # the fresh module and its follow-up
    )
    assert window(job, 90)["humanPrs"] == expected_prs
    assert window(job, 90)["people"] == 8  # Alice, the team of six, Hugo


def test_short_windows_only_see_recent_work(job):
    assert window(job, 7)["humanPrs"] == 16  # the newest commit is a day old, so back to day 8
    assert window(job, 7)["people"] == 7  # Hugo was quiet
    titles = {pr["title"] for p in window(job, 7)["top"] for pr in p["evidence"]}
    assert titles <= {"feat: weekly work", "feat: fresh module"}


def test_every_commit_was_matched_to_its_pr(job):
    assert {w["coverage"] for w in job.data["windows"]} == {100.0}


def test_same_input_gives_the_same_bytes(job):
    assert job.first_output == job.second_output


def test_the_second_run_only_asks_github_for_recent_days(job):
    assert job.queries_second_run < job.queries_first_run / 4


def test_review_text_stays_out_of_the_page_data(job):
    assert "This retry can loop forever" not in job.first_output


class TestCheck:
    @pytest.fixture
    def data(self, job):
        return json.loads(job.first_output)

    def test_passes_a_good_run(self, job):
        check.check(job.output)

    def test_rejects_stale_data(self, data):
        data["snapshot"] -= 10 * 86400
        assert any("days old" in p for p in check.problems_in(data))

    def test_rejects_a_broken_pr_join(self, data):
        data["windows"][2]["coverage"] = 40
        assert any("7d: 40% of commits matched" in p for p in check.problems_in(data))


def test_a_failed_check_stops_the_job(world, tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_prs, "API", world.github.url)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(check, "MIN_COVERAGE", 101)
    with pytest.raises(ValueError, match="matched a PR"):
        run_job(world, tmp_path)


def test_a_github_outage_stops_the_job_before_scoring(world, tmp_path, monkeypatch, no_wait):
    monkeypatch.setattr(fetch_prs, "API", "http://127.0.0.1:1/graphql")
    monkeypatch.setattr(fetch_prs, "RETRIES", 1)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    with pytest.raises(RuntimeError, match="gave up"):
        run_job(world, tmp_path)
    assert not (tmp_path / "impact.json").exists()
