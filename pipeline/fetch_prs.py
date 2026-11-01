"""Keep one JSON file of merged PRs per UTC day.

GitHub search stops at 1,000 results, so PRs are fetched one day at a time. Old
days never change, so a refresh only fetches the newest days, which keeps daily
runs well inside the API limits. A day is saved only when its count matches.
"""
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from config import REPO, WINDOWS
from score import is_bot

API = "https://api.github.com/graphql"
KEEP_DAYS = max(WINDOWS) + 1  # the newest day is partial
REFRESH_DAYS = 3  # today is partial, and reviews can land just after a merge
WORKERS = 4
RETRIES = 10

QUERY = """query($q:String!,$c:String){search(query:$q,type:ISSUE,first:20,after:$c){
issueCount pageInfo{hasNextPage endCursor}
nodes{... on PullRequest{
number mergedAt body
author{login __typename}
reviews(first:30){nodes{author{login __typename} bodyText}}
reviewThreads(first:12){nodes{comments(first:2){nodes{author{login __typename} bodyText}}}}
}}}}"""


def declared_autonomy(body):
    """Read the self-declared autonomy from PostHog's PR template.

    "Human-driven (agent-assisted)" is the template's option for directed agent
    work. Fully human PRs are told to remove the section.
    """
    match = re.search(r"\*\*Autonomy:\*\*\s*([^\n]*)", body or "")
    value = (match.group(1) if match else "").lower()
    unfilled = " or " in value and "fully" in value and "human" in value
    if unfilled:
        return "unknown"
    if "fully autonomous" in value:
        return "autonomous"
    if "human-driven" in value:
        return "directed"
    return "unknown"


def search(query, cursor, token):
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": QUERY, "variables": {"q": query, "c": cursor}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    failure = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            if "errors" not in payload:
                return payload["data"]["search"]
            failure = payload["errors"]
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise RuntimeError("GitHub rejected GH_TOKEN") from error
            failure = error
        except (OSError, ValueError) as error:
            failure = error
        time.sleep(5 + attempt * 3)
    raise RuntimeError(f"gave up on {query}: {failure}")


def without_bots(connection):
    connection["nodes"] = [node for node in connection["nodes"] if not is_bot(node["author"])]


def slimmed(pr):
    """Most stored text would otherwise be bot reviews, which scoring never reads."""
    pr["autonomy"] = declared_autonomy(pr.pop("body"))
    without_bots(pr["reviews"])
    for thread in pr["reviewThreads"]["nodes"]:
        without_bots(thread["comments"])
    return pr


def fetch_day(day, path, token):
    query = f"repo:{REPO} is:pr is:merged merged:{day}"
    prs, cursor = [], None
    while True:
        page = search(query, cursor, token)
        prs += [slimmed(pr) for pr in page["nodes"]]
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    if len(prs) != page["issueCount"]:
        raise RuntimeError(f"{day}: got {len(prs)} of {page['issueCount']}")
    path.write_text(json.dumps(prs), encoding="utf-8")


def sync(today, out_dir, token):
    days = [(today - timedelta(n)).isoformat() for n in range(KEEP_DAYS)]
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.glob("*.json"):
        if path.stem not in days:
            path.unlink()
    due = [d for i, d in enumerate(days) if i < REFRESH_DAYS or not (out_dir / f"{d}.json").exists()]
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda day: fetch_day(day, out_dir / f"{day}.json", token), due))
    print("fetched", len(due), "days", flush=True)
