"""The algorithm, on inputs small enough to work out by hand."""
import json
from datetime import datetime, timezone

import numpy as np
import pytest

import reliability
import score
from config import DAY
from helpers import make_pr

NOW = 1_800_000_000


def commit(pr, author, age_days):
    return {"author": author, "date": NOW - age_days * DAY, "title": f"feat: work (#{pr})", "pr": pr}


def model_of(commits, prs, built_on=None, ended=(), areas=None):
    """built_on maps a commit's position to {earlier position: lines}."""
    built_on = built_on or {}
    areas = areas or {}
    return score.Model({
        "end_time": NOW,
        "commits": commits,
        "built_on": [list(built_on.get(i, {}).items()) for i in range(len(commits))],
        "ended": [list(row) for row in ended],
        "areas": [areas.get(i, []) for i in range(len(commits))],
    }, prs)


def everyone(model):
    return [True] * len(model.commits)


def stored_pr(number, login, autonomy="directed", **fields):
    """A PR as the daily sync saves it, merged 50 days before NOW."""
    pr = make_pr(number, login, merged_at=datetime.fromtimestamp(NOW - 50 * DAY, timezone.utc), **fields)
    del pr["body"]
    pr["autonomy"] = autonomy
    return pr


def prs_for(*logins):
    return {i: stored_pr(i, login) for i, login in enumerate(logins, 1)}


class TestPagerank:
    def test_scores_sum_to_one(self):
        ranks = score.pagerank({("a", "b"): 1, ("b", "c"): 2}, ["a", "b", "c", "d"])
        assert sum(ranks.values()) == pytest.approx(1)

    def test_each_person_has_one_vote_however_much_they_ship(self):
        """fan sends 500 units to loud, quiet sends 1 unit to modest: same influence."""
        people = ["fan", "quiet", "loud", "modest"]
        ranks = score.pagerank({("fan", "loud"): 500, ("quiet", "modest"): 1}, people)
        assert ranks["loud"] == pytest.approx(ranks["modest"])

    def test_more_voters_beat_more_votes(self):
        people = ["a", "b", "c", "wide", "narrow", "fan"]
        edges = {("a", "wide"): 1, ("b", "wide"): 1, ("c", "wide"): 1, ("fan", "narrow"): 100}
        ranks = score.pagerank(edges, people)
        assert ranks["wide"] > ranks["narrow"]

    def test_a_vote_from_someone_trusted_counts_for_more(self):
        people = ["x", "y", "z", "star", "fan_of_star", "fan_of_nobody", "nobody"]
        edges = {("x", "star"): 1, ("y", "star"): 1, ("z", "star"): 1,
                 ("star", "fan_of_star"): 1, ("nobody", "fan_of_nobody"): 1}
        ranks = score.pagerank(edges, people)
        assert ranks["fan_of_star"] > ranks["fan_of_nobody"]

    def test_no_votes_means_everyone_is_equal(self):
        assert score.pagerank({}, ["a", "b"]) == {"a": 0.5, "b": 0.5}


class TestPercentile:
    def test_spreads_people_from_zero_to_one(self):
        assert score.percentile([10, 30, 20]).tolist() == [0, 1, 0.5]

    def test_ties_share_the_average(self):
        assert score.percentile([5, 5, 9]).tolist() == [0.25, 0.25, 1]


class TestAcceptability:
    def table(self):
        rng = np.random.default_rng(0)
        middle = rng.uniform(0.2, 0.8, (10, 3))
        return np.vstack([np.ones((1, 3)), middle, np.zeros((1, 3))])

    def test_best_on_every_measure_is_100_percent(self):
        assert score.acceptability(self.table())[0] == 1

    def test_worst_on_every_measure_is_0_percent(self):
        assert score.acceptability(self.table())[-1] == 0

    def test_it_is_a_share_of_weightings_not_a_grade(self):
        """Five places in every weighting, so everyone's numbers add up to 500%."""
        assert score.acceptability(self.table()).sum() == pytest.approx(score.TOP)

    def test_a_specialist_is_chosen_only_when_their_measure_is_weighted(self):
        rows = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] + [[0.45, 0.45, 0.45]] * 2 + [[0.4, 0.4, 0.4]] * 3
        share = score.acceptability(np.array(rows, dtype=float))
        assert 0 < share[0] < 1

    def test_same_input_gives_same_answer(self):
        table = self.table()
        assert score.acceptability(table).tolist() == score.acceptability(table).tolist()

    def test_works_with_exactly_five_people(self):
        assert score.acceptability(np.ones((5, 3))).tolist() == [1] * 5


class TestReach:
    def setup_method(self):
        self.commits = [commit(1, "Alice", 40), commit(2, "Bob", 30), commit(3, "Carol", 30)]

    def votes(self, model, count=3):
        return model.reach(everyone(model), [False] * count)

    def test_a_later_pr_splits_its_credit_by_lines_touched(self):
        commits = [commit(1, "Alice", 200), commit(2, "Bob", 199), commit(3, "Carol", 198)]
        model = model_of(commits, prs_for("alice", "bob", "carol"), built_on={2: {0: 3, 1: 1}})
        value = {origin: v for _, origin, v in self.votes(model)}
        assert value[0] / value[1] == pytest.approx(3)

    def test_nobody_gets_credit_from_themselves(self):
        model = model_of(self.commits, prs_for("alice", "alice", "carol"), built_on={1: {0: 6}})
        assert not self.votes(model)

    def test_bots_cannot_vote(self):
        model = model_of(self.commits, prs_for("alice", "dependabot[bot]", "carol"), built_on={1: {0: 6}})
        assert not self.votes(model)

    def test_autonomous_prs_cannot_vote(self):
        prs = prs_for("alice", "bob", "carol")
        prs[2]["autonomy"] = "autonomous"
        model = model_of(self.commits, prs, built_on={1: {0: 6}})
        assert not self.votes(model)

    def test_code_written_by_a_bot_earns_nothing(self):
        model = model_of(self.commits, prs_for("renovate[bot]", "bob", "carol"), built_on={1: {0: 6}})
        assert not self.votes(model)

    def test_younger_prs_get_more_credit_for_the_same_builders(self):
        """Age correction: credit is still arriving for the younger PR."""
        commits = [
            commit(1, "Old", 150), commit(2, "Next", 149),  # teaches the arrival curve
            commit(3, "Old", 140), commit(4, "Next", 120),
            commit(5, "Alice", 60), commit(6, "Bob", 10), commit(7, "Carol", 8),
        ]
        prs = prs_for("old", "next", "old", "next", "alice", "bob", "carol")
        in_window = [False] * 4 + [True] * 3
        model = model_of(commits, prs, built_on={1: {0: 1}, 3: {2: 1}, 5: {4: 1}, 6: {5: 1}})
        value = {origin: v for _, origin, v in model.reach(in_window, in_window)}
        assert value[5] > value[4]

    def test_no_older_history_means_no_age_correction(self):
        model = model_of(self.commits, prs_for("alice", "bob", "carol"), built_on={1: {0: 6}})
        keep = in_window = everyone(model)
        assert [v for _, _, v in model.reach(keep, in_window)] == [1]


def scored(work, autonomous=(), mechanical=(), reviews=()):
    """Score a small team. work is one (author, [who later built on it]) per PR."""
    # Two old PRs teach the age correction that credit arrives within a week.
    commits = [commit(900, "Old", 300), commit(901, "Next", 299)]
    prs = {}
    built_on = {1: {0: 1}}
    follow_ups = []
    for number, (author, builders) in enumerate(work, 1):
        commits.append(commit(number, author, 60))
        if number in mechanical:
            commits[-1]["title"] = f"chore: regenerate api client (#{number})"
        prs[number] = stored_pr(number, author, "autonomous" if number in autonomous else "directed")
        follow_ups += [(len(commits) - 1, builder) for builder in builders]
    for number, (origin, builder) in enumerate(follow_ups, 1000):
        built_on[len(commits)] = {origin: 1}
        commits.append(commit(number, builder, 50))
        prs[number] = stored_pr(number, builder)
    prs[1]["reviews"]["nodes"] += [
        {"author": {"login": who, "__typename": "User"}, "bodyText": "A real review."} for who in reviews
    ]
    return score.score_window(model_of(commits, prs, built_on=built_on), 90)


class TestScenarios:
    TEAM = ["a", "b", "c", "d", "e", "f"]

    def ring(self):
        """Each colleague is built on by one neighbour."""
        return [(name, [self.TEAM[(i + 1) % len(self.TEAM)]]) for i, name in enumerate(self.TEAM)]

    def star_work(self):
        return [("star", self.TEAM)] + self.ring()

    def logins(self, result):
        return [p["login"] for p in result["top"]]

    def test_being_built_on_by_everyone_wins(self):
        result = scored(self.star_work())
        assert self.logins(result)[0] == "star"
        assert result["top"][0]["builders"] == len(self.TEAM)

    def test_autonomous_work_counts_in_the_main_ranking(self):
        assert self.logins(scored(self.star_work(), autonomous={1}))[0] == "star"

    def test_the_comparison_really_removes_autonomous_work(self):
        star = scored(self.star_work(), autonomous={1})["top"][0]
        assert star["rankWithoutAutonomous"] == len(self.TEAM) + 1

    def test_mechanical_prs_earn_nothing(self):
        assert "star" not in self.logins(scored(self.star_work(), mechanical={1}))

    def test_notices_a_top_five_that_rests_on_single_prs(self):
        one_hit = [(name, ["x", "y", "z"]) for name in self.TEAM[:5]]
        steady = [(name, ["f"]) for name in ("x", "y", "z") for _ in range(2)]
        assert scored(one_hit + steady)["sameTop5WithoutBiggestPr"] is False

    def test_a_top_five_with_depth_survives_losing_each_biggest_pr(self):
        deep = [(name, ["x", "y", "z"]) for name in self.TEAM[:5] for _ in range(3)]
        quiet = [(name, []) for name in ("x", "y", "z")]
        assert scored(deep + quiet)["sameTop5WithoutBiggestPr"] is True

    def test_too_few_people_stops_with_a_clear_message(self):
        with pytest.raises(ValueError, match="90d window has 2 people"):
            scored([("a", ["b"])])

    def test_someone_who_only_reviews_counts_as_a_person(self):
        assert scored(self.ring(), reviews=["reviewer"])["people"] == len(self.TEAM) + 1

    def test_someone_who_only_reviews_can_be_published_without_prs(self):
        """Five authors and three reviewers is too few for a reviewer to miss the top 5."""
        result = scored([("a", ["b"]), ("b", [])], reviews=["r", "s", "t", "u"])
        reviewer = next(p for p in result["top"] if p["login"] in "rstu")
        assert (reviewer["prs"], reviewer["volumeRank"], reviewer["evidence"]) == (0, None, [])


class TestHeldUp:
    def ended(self, fixes_for_alice):
        """Alice and Bob hold the same code in one area, with different fix counts."""
        rows = []
        for origin, person_fixes in ((0, fixes_for_alice), (1, 0), (2, 2), (3, 2), (4, 2)):
            rows.append((origin, "products/a", "corrected", 1, person_fixes))
            rows.append((origin, "products/a", "alive", 10, 1000 - person_fixes))
        return [row for row in rows if row[4]]

    def model(self, fixes_for_alice):
        logins = ("alice", "bob", "carol", "dave", "erin", "frank")
        commits = [commit(i, login.title(), 50) for i, login in enumerate(logins, 1)]
        return model_of(commits, prs_for(*logins), ended=self.ended(fixes_for_alice))

    def test_a_window_with_real_differences_is_measured(self):
        assert score.score_window(self.model(300), 90)["heldMeasured"] is True

    def test_a_window_without_real_differences_is_flagged_as_too_early(self):
        assert score.score_window(self.model(2), 90)["heldMeasured"] is False

    def test_more_fixes_than_peers_scores_below_one(self):
        model = self.model(300)
        held, info = model.held_up(everyone(model))
        assert info["variance"] > 0
        assert held["alice"]["held"] < 1 < held["bob"]["held"]
        assert held["alice"]["observed"] == 300

    def test_no_real_difference_between_people_means_exactly_average(self):
        model = self.model(2)
        held, info = model.held_up(everyone(model))
        assert info["variance"] == 0
        assert {person["held"] for person in held.values()} == {1.0}

    def test_your_own_fixes_never_raise_your_own_expectation(self):
        """Leaving Alice out of the baseline: her 300 fixes can't excuse themselves."""
        heavy, _ = (m := self.model(300)).held_up(everyone(m))
        light, _ = (m := self.model(2)).held_up(everyone(m))
        assert heavy["alice"]["expected"] == pytest.approx(light["alice"]["expected"], rel=0.02)

    def test_expected_fixes_come_from_peers_alone(self):
        """Bob's code lost 10% of its lines in week 1, so Alice's 1,000 should expect 100."""
        commits = [commit(1, "Alice", 50), commit(2, "Bob", 50)]
        rows = [
            (0, "products/a", "alive", 10, 1000),
            (1, "products/a", "corrected", 1, 100), (1, "products/a", "alive", 10, 900),
        ]
        model = model_of(commits, prs_for("alice", "bob"), ended=rows)
        held, _ = model.held_up([True, False])
        assert held["alice"]["expected"] == 100

    def test_an_area_one_person_dominates_uses_the_repo_wide_rate(self):
        commits = [commit(1, "Alice", 50), commit(2, "Bob", 50), commit(3, "Carol", 50)]
        rows = [
            (0, "solo", "alive", 10, 50_000),      # Alice is nearly all of this area
            (1, "solo", "corrected", 1, 100), (1, "solo", "alive", 10, 900),  # one peer, badly fixed
            (2, "other", "corrected", 1, 10), (2, "other", "alive", 10, 50_000),
        ]
        model = model_of(commits, prs_for("alice", "bob", "carol"), ended=rows)
        held, _ = model.held_up([True, False, False])
        peers_only_rate = 100 / 1000 * 50_000
        assert held["alice"]["expected"] < peers_only_rate / 5


class TestBreadth:
    def test_h_index_needs_h_areas_with_h_prs_each(self):
        commits = [commit(i, "Alice", 30) for i in range(1, 7)]
        areas = {0: ["a"], 1: ["a"], 2: ["a"], 3: ["b"], 4: ["b"], 5: ["c"]}
        model = model_of(commits, prs_for(*["alice"] * 6), areas=areas)
        assert model.breadth(range(6)) == {"alice": 2}  # a:3 b:2 c:1


class TestAttention:
    def edges(self, reviews, author="alice"):
        return score.attention([stored_pr(1, author, reviews=reviews)])

    def test_a_written_review_counts(self):
        edges, given, written_prs = self.edges([("bob", "This loop drops the last page of results.")])
        assert edges == {("alice", "bob"): 1}
        assert given["bob"] == 1
        assert written_prs == 1

    @pytest.mark.parametrize("text", ["LGTM", "lgtm!", "", "  ", "Looks good", "ship it", ":+1:", "thanks"])
    def test_empty_praise_is_ignored(self, text):
        assert not self.edges([("bob", text)])[0]

    @pytest.mark.parametrize("text", [
        "Automated review: looks safe.",
        "Posted on behalf of @bob by his agent.",
        "  \U0001F916 I checked the migration.",
        "Reviewed by Claude for correctness.",
    ])
    def test_disclosed_agent_reviews_are_removed(self, text):
        edges, _, written_prs = self.edges([("bob", text)])
        assert not edges
        assert written_prs == 0

    def test_reviewing_yourself_does_not_count(self):
        assert not self.edges([("alice", "Noting why I did this.")])[0]

    def test_review_bots_do_not_count(self):
        assert not self.edges([("coderabbitai", "Consider renaming this.")])[0]

    def test_reviews_on_bot_prs_do_not_count(self):
        pr = stored_pr(1, "dependabot[bot]", reviews=[("bob", "Pin this to a safe version.")], bot=True)
        assert not score.attention([pr])[0]


class TestReliability:
    def test_spearman_averages_ties_so_order_cannot_matter(self):
        """The same four pairs, with the two tied ones listed the other way round."""
        ties = [1, 1, 2, 3]
        assert reliability.spearman(ties, [2, 1, 3, 4]) == pytest.approx(reliability.spearman(ties, [1, 2, 3, 4]))

    def test_too_few_people_reports_none_rather_than_nan(self):
        model = model_of([commit(1, "A", 50)], prs_for("a"))
        rng = np.random.default_rng(0)
        assert reliability.built_on_split_half(model, [], ["a"], rng) == (None, 0)
        assert reliability.held_up_stability(model, {"origin_observed": {}, "origin_expected": {}}, rng) == (None, None)


def test_is_bot_catches_accounts_that_look_human():
    for login in ("stamphog", "trunk-io", "posthog", "max-ai", "greptile-apps", "someone-posthog"):
        assert score.is_bot({"login": login, "__typename": "User"})
    assert score.is_bot({"login": "x", "__typename": "Bot"})
    assert score.is_bot(None)
    assert not score.is_bot({"login": "webjunkie", "__typename": "User"})


def test_commit_windows_follow_the_merge_date():
    commits = [commit(1, "A", 95), commit(2, "A", 80), commit(3, "A", 20), commit(4, "A", 3)]
    model = model_of(commits, prs_for(*"abcdef"))
    assert score.open_window(model, 90).in_window == [False, True, True, True]
    assert score.open_window(model, 90).keep == [False, True, True, True]


def test_prs_that_only_touch_excluded_files_still_count_in_the_window():
    """A docs PR has no commit in the line history, but it was merged and reviewed."""
    model = model_of([commit(1, "A", 10)], prs_for(*"abcdef"))
    window = score.open_window(model, 90)
    assert len(window.human_prs) == 6


def test_commits_without_a_fetched_pr_are_outside_every_window():
    model = model_of([commit(99, "A", 3)], prs_for(*"abcdef"))
    assert score.open_window(model, 7 * 13).in_window == [False]


def test_mechanical_titles_are_recognised():
    titles = ["chore: regenerate schema (#1)", "chore: update snapshots (#2)", "chore: codegen (#3)"]
    commits = [{**commit(i, "A", 5), "title": t} for i, t in enumerate(titles, 1)]
    commits.append(commit(4, "A", 5))
    assert model_of(commits, prs_for("a", "a", "a", "a")).mechanical == [True, True, True, False]


def test_model_maps_commit_names_to_github_logins():
    commits = [commit(1, "Alice Smith", 5), commit(None, "Alice Smith", 200)]
    model = model_of(commits, {1: stored_pr(1, "asmith")})
    assert model.owner == ["asmith", "asmith"]
    assert model.display["asmith"] == "Alice Smith"
    assert all(model.human)


def test_published_json_never_contains_nan(tmp_path, monkeypatch):
    line_history = tmp_path / "replay.json"
    line_history.write_text(json.dumps({"end_time": NOW, "commits": [], "built_on": [], "ended": [], "areas": []}))
    (tmp_path / "raw").mkdir()
    monkeypatch.setattr(score, "score_window", lambda model, days: {"days": days, "top": [], "x": float("nan")})
    out = tmp_path / "impact.json"
    with pytest.raises(ValueError):
        score.run(line_history, tmp_path / "raw", out)
    assert not out.exists()
