"""Refresh the published numbers. Needs git, and GH_TOKEN with read access to the repo."""
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import check
import fetch_prs
import find_large_paths
import replay
import score
from config import DATA, LINE_HISTORY, PUBLISHED, RAW, REPO, WINDOWS

CLONE_URL = f"https://github.com/{REPO}.git"
HISTORY_DAYS = max(WINDOWS) + 100  # older code gives scoring its baselines, see score.AGE_HORIZON
CLONE_MARGIN_DAYS = 5  # the oldest cloned commit looks like it adds the whole repo, so skip it


def main(clone_url=CLONE_URL, data=DATA, out=PUBLISHED):
    token = os.environ["GH_TOKEN"]
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(HISTORY_DAYS)
    clone_since = since - timedelta(CLONE_MARGIN_DAYS)
    raw = data / RAW
    line_history = data / LINE_HISTORY

    with TemporaryDirectory(ignore_cleanup_errors=True) as work:
        clone = str(Path(work) / "repo")
        print("cloning", clone_url, flush=True)
        subprocess.run([
            "git", "clone", "--quiet", "--single-branch", "--no-checkout",
            f"--shallow-since={clone_since}", "--filter=blob:limit=1m", clone_url, clone,
        ], check=True)
        fetch_prs.sync(today, raw, token)
        replay.run(clone, since.isoformat(), find_large_paths.large_paths(clone), line_history)

    score.run(line_history, raw, out)
    check.check(out)


if __name__ == "__main__":
    main()
