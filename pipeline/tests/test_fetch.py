"""Daily PR sync, against the local stand-in for GitHub."""
import json
from datetime import date, timedelta

import pytest

import fetch_prs
from helpers import make_pr

TODAY = date(2026, 10, 31)


def day(offset):
    return (TODAY - timedelta(offset)).isoformat()


def fill_days(folder, skip=()):
    for offset in range(fetch_prs.KEEP_DAYS):
        if offset not in skip:
            (folder / f"{day(offset)}.json").write_text("[]")


def saved(folder, offset):
    return json.loads((folder / f"{day(offset)}.json").read_text())


@pytest.mark.parametrize("body, expected", [
    ("**Autonomy:** Human-driven (agent-assisted)", "directed"),
    ("**Autonomy:** Fully autonomous", "autonomous"),
    ("**Autonomy:** Human-driven (agent-assisted) - or - Fully autonomous", "unknown"),
    ("no template here", "unknown"),
    (None, "unknown"),
])
def test_reads_declared_autonomy(body, expected):
    assert fetch_prs.declared_autonomy(body) == expected


def test_first_run_fetches_every_day_and_follows_pages(github, tmp_path):
    github.prs_by_day[day(10)] = [make_pr(n, "alice", autonomy="Fully autonomous") for n in range(5)]

    fetch_prs.sync(TODAY, tmp_path, "token")

    assert len(list(tmp_path.glob("*.json"))) == fetch_prs.KEEP_DAYS
    prs = saved(tmp_path, 10)
    assert [pr["number"] for pr in prs] == list(range(5))
    assert prs[0]["autonomy"] == "autonomous"
    assert "body" not in prs[0]


def test_bot_reviews_are_not_stored(github, tmp_path):
    fill_days(tmp_path)
    pr = make_pr(1, "alice", reviews=[("coderabbitai", "long bot text"), ("bob", "A real review.")])
    pr["reviewThreads"]["nodes"] = [{"comments": {"nodes": [
        {"author": {"login": "stamphog", "__typename": "User"}, "bodyText": "bot"},
        {"author": None, "bodyText": "deleted account"},
        {"author": {"login": "carol", "__typename": "User"}, "bodyText": "A real comment."},
    ]}}]
    github.prs_by_day[day(0)] = [pr]

    fetch_prs.sync(TODAY, tmp_path, "token")

    stored = saved(tmp_path, 0)[0]
    assert [r["author"]["login"] for r in stored["reviews"]["nodes"]] == ["bob"]
    assert [c["author"]["login"] for c in stored["reviewThreads"]["nodes"][0]["comments"]["nodes"]] == ["carol"]


def test_later_runs_only_refresh_recent_days_and_drop_old_ones(github, tmp_path):
    fill_days(tmp_path)
    (tmp_path / f"{day(fetch_prs.KEEP_DAYS)}.json").write_text("[]")  # just left the window
    github.prs_by_day[day(0)] = [make_pr(1, "alice")]

    fetch_prs.sync(TODAY, tmp_path, "token")

    assert github.days_queried() == {day(n) for n in range(fetch_prs.REFRESH_DAYS)}
    assert not (tmp_path / f"{day(fetch_prs.KEEP_DAYS)}.json").exists()
    assert saved(tmp_path, 0)[0]["number"] == 1


def test_fills_a_gap_left_by_a_failed_run(github, tmp_path):
    fill_days(tmp_path, skip={40})

    fetch_prs.sync(TODAY, tmp_path, "token")

    assert day(40) in github.days_queried()


def test_refuses_to_save_a_day_with_missing_prs(github, tmp_path):
    github.prs_by_day[day(0)] = [make_pr(1, "alice")]
    github.overcount[day(0)] = 1

    with pytest.raises(RuntimeError, match="got 1 of 2"):
        fetch_prs.sync(TODAY, tmp_path, "token")

    assert not (tmp_path / f"{day(0)}.json").exists()


def test_retries_when_github_has_a_bad_moment(github, tmp_path, no_wait):
    fill_days(tmp_path)
    github.prs_by_day[day(0)] = [make_pr(1, "alice")]
    github.fail_next = 2

    fetch_prs.sync(TODAY, tmp_path, "token")

    assert saved(tmp_path, 0)[0]["number"] == 1


def test_gives_up_when_github_stays_down(github, tmp_path, no_wait):
    github.fail_next = 10_000
    with pytest.raises(RuntimeError, match="gave up"):
        fetch_prs.sync(TODAY, tmp_path, "token")


def test_stops_at_once_on_a_bad_token(github, tmp_path):
    github.fail_next, github.fail_status = 10_000, 401
    with pytest.raises(RuntimeError, match="rejected GH_TOKEN"):
        fetch_prs.sync(TODAY, tmp_path, "token")
    assert github.attempts < fetch_prs.KEEP_DAYS / 2  # the queued days were cancelled
