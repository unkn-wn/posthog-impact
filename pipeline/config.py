"""Values more than one pipeline step must agree on."""
from pathlib import Path

REPO = "PostHog/posthog"
DAY = 86400
WEEK = 7 * DAY
WINDOWS = (90, 30, 7)  # days, in the order the page shows them
TOP = 5

DATA = Path("pipeline/data")
RAW = "raw"  # folder of daily PR files, inside DATA
LINE_HISTORY = "replay.json"  # inside DATA
PUBLISHED = Path("src/data/impact.json")
