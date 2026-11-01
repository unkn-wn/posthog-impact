"""How far can each window's measures be trusted? Run by hand after a refresh.

The results are quoted in the README and aren't part of the daily job.

  Split-half  Split each person's PRs in two at random. If a measure describes
              people rather than noise, the two halves rank people the same way.
  Chance      Shuffle who wrote which PR. Real differences must beat the shuffle.
"""
import collections
import json

import numpy as np

from config import DATA, LINE_HISTORY, RAW, WINDOWS
from score import SEED, Model, load_prs, open_window, pagerank_arrays, percentile, scenario

MIN_PRS = 20  # a person needs this many credited PRs to be compared
MIN_PEOPLE = 3  # a rank correlation needs at least this many people


def spearman(a, b):
    return float(np.corrcoef(percentile(a), percentile(b))[0, 1])


def built_on_split_half(model, votes, people, rng):
    """Returns the score and how many people it covers, or None if too few."""
    index = {p: i for i, p in enumerate(people)}
    votes = [v for v in votes if v[0] in index and model.owner[v[1]] in index]
    source = np.array([index[builder] for builder, _, _ in votes], dtype=int)
    target = np.array([index[model.owner[origin]] for _, origin, _ in votes], dtype=int)
    origins = np.array([origin for _, origin, _ in votes], dtype=int)
    value = np.array([value for _, _, value in votes], dtype=float)
    credited = collections.Counter(model.owner[o] for o in set(origins.tolist()))
    compared = np.array([credited[p] >= MIN_PRS for p in people])
    if compared.sum() < MIN_PEOPLE:
        return None, int(compared.sum())

    scores = []
    unique = np.unique(origins)
    for _ in range(20):
        side = np.zeros(origins.max() + 1, dtype=bool)
        side[unique] = rng.random(len(unique)) < 0.5
        mask = side[origins]
        left = pagerank_arrays(source[mask], target[mask], value[mask], len(people))
        right = pagerank_arrays(source[~mask], target[~mask], value[~mask], len(people))
        scores.append(spearman(left[compared], right[compared]))
    return round(float(np.mean(scores)), 2), int(compared.sum())


def held_up_stability(model, info, rng):
    """Returns the split-half score and the share of shuffles real data beats."""
    observed, expected = info["origin_observed"], info["origin_expected"]
    origins = [o for o in expected if expected[o] > 0]
    obs = np.array([observed[o] for o in origins], dtype=float)
    exp = np.array([expected[o] for o in origins], dtype=float)
    names, codes = np.unique([model.owner[o] for o in origins], return_inverse=True)
    busy = np.bincount(codes, minlength=len(names)) >= MIN_PRS
    if busy.sum() < MIN_PEOPLE:
        return None, None

    def log_ratios(owners, mask=slice(None)):
        o = np.bincount(owners[mask], obs[mask], len(names))[busy]
        e = np.bincount(owners[mask], exp[mask], len(names))[busy]
        return np.log((o + 1) / (e + 1))

    scores = []
    for _ in range(50):
        mask = rng.random(len(codes)) < 0.5
        scores.append(spearman(log_ratios(codes, mask), log_ratios(codes, ~mask)))
    real = log_ratios(codes).std()
    shuffled = [log_ratios(rng.permutation(codes)).std() for _ in range(500)]
    return round(float(np.mean(scores)), 2), round(float(np.mean(np.array(shuffled) < real)), 3)


def measure(model, days):
    window = open_window(model, days)
    full = scenario(model, window, window.keep)
    rng = np.random.default_rng(SEED)
    built_on, people = built_on_split_half(model, full.votes, window.people, rng)
    held_split, beats_chance = held_up_stability(model, full.held_info, rng)
    return {
        "builtOnSplitHalf": built_on,
        "builtOnPeople": people,
        "heldSplitHalf": held_split,
        "heldBeatsChance": beats_chance,
    }


if __name__ == "__main__":
    replay = json.loads((DATA / LINE_HISTORY).read_text(encoding="utf-8"))
    model = Model(replay, load_prs(DATA / RAW))
    for days in WINDOWS:
        print(f"{days}d: {measure(model, days)}")
