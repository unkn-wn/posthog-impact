"""Refuse to publish numbers that look wrong, so the last good page stays up."""
import json
import time

from config import DAY

MAX_AGE_DAYS = 3
MIN_COVERAGE = 95  # percent of merged commits matched to their PR


def problems_in(data):
    problems = []
    age = (time.time() - data["snapshot"]) / DAY
    if age > MAX_AGE_DAYS:
        problems.append(f"newest commit is {age:.1f} days old, limit is {MAX_AGE_DAYS}")
    for window in data["windows"]:
        if window["coverage"] < MIN_COVERAGE:
            problems.append(f"{window['days']}d: {window['coverage']}% of commits matched a PR, need {MIN_COVERAGE}%")
    return problems


def check(path):
    problems = problems_in(json.loads(path.read_text(encoding="utf-8")))
    if problems:
        raise ValueError("; ".join(problems))
