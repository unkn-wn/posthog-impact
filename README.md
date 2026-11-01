# PostHog Impact

One page answering: who moved PostHog forward recently? It defaults to the last
90 days, with 30 and 7 day views.

Most PostHog PRs now involve an agent and few get a written human review, so PR,
line and review counts mostly measure tooling. This ranks what happened to the
code after it merged. No LLM reads any code, and every step is deterministic.

## How the data stays fresh

`.github/workflows/refresh.yml` runs daily, on non-Markdown pushes to `main`, and
on demand:

1. Run the tests. A failure stops the job.
2. Restore the PR data from the `raw.tar.gz` asset on the `data` release.
3. Run `pipeline/run.py`, which clones PostHog, fetches the newest days of PRs,
   replays line history, scores each window, and checks the result.
4. Build the static page, save the PR data, and deploy to GitHub Pages.

If any step fails, nothing deploys and the last good page stays up. GitHub turns
off scheduled workflows after 60 days without commits, so push a commit or
re-enable the workflow if this needs to run longer.

The page reads `src/data/impact.json`. The copy in git is only for local
development: the daily job builds its own and doesn't commit it.

## Running it locally

Needs Python 3.13, Node 22, git, and a logged-in `gh`.

```sh
pip install -r pipeline/requirements.txt
pytest                                        # under a minute, no network

gh release download data --pattern raw.tar.gz --clobber --repo unkn-wn/posthog-impact
mkdir -p pipeline/data && tar xzf raw.tar.gz -C pipeline/data
GH_TOKEN=$(gh auth token) python pipeline/run.py

npm install && npm run dev
```

`python pipeline/score.py` rescores existing data in seconds, and
`python pipeline/reliability.py` prints the numbers in the table below.

## Setting up a new copy

1. Run `pipeline/run.py` locally without restoring any data. This fetches all 91
   days, which takes over 20 minutes and more API quota than Actions' built-in
   token allows in an hour.
2. Save the data, which the workflow requires to exist:

   ```sh
   tar czf raw.tar.gz -C pipeline/data raw
   gh release create data raw.tar.gz --title "PR data" --notes "Rolling PR data for the daily job."
   ```

3. In the repo's Pages settings, set the source to GitHub Actions.

## How the score works

| Measure | Question | Method |
| --- | --- | --- |
| Built on | Did other people rely on this code? | People who later edited next to it. Each person has one vote, then PageRank |
| Held up | Did it need fixing? | Lines replaced by a later fix versus peers on code of the same age |
| Areas | How wide is real ownership? | h-index across product areas, counting only built-on PRs |
| Reviews | Who do people turn to? | PageRank over written human reviews, minus disclosed agent reviews |

Built on and Held up multiply into one score. The three scores become percentiles,
then 100,000 random weightings decide how often each person reaches the top 5.

**What the percentage means.** It is the share of weightings that put someone in
the top 5, not a grade. 100% means top 5 however the measures are weighted, and
0% means no weighting gets them there. Five people are picked each time, so the
numbers add up to 500% across everyone.

Handled explicitly: moved lines keep their author, generated code, tests and docs
are excluded, idle code does not count as durable, and an area baseline only
applies where peers hold at least as much of the code as the person being scored.

## How far to trust each window

Use 90 days for decisions. The 30 and 7 day views are early reads, because little
has happened to recent code yet. When the data finds no real difference between
people on Held up, the page says "Too early to tell" rather than showing everyone
as average.

Measured Nov 1, 2026:

| Window | Built on split-half | Held up separates people |
| --- | --- | --- |
| 90 days | 0.73 | Yes |
| 30 days | 0.56 | No |
| 7 days | Too few people | No |

Split-half: rank people on two random halves of their work. 1 means the halves
agree perfectly and 0 means they're unrelated.

## Known limits

- Fix detection reads PR titles, so it will include fixes unrelated to the lines replaced.
- Rewritten moves can escape exact-line matching.
- The Autonomy field is self-declared, so it only powers a comparison and never lowers a score.
- Undisclosed agent reviews still count as human. Each PR's reviews are capped at the first 30 reviews, and 2 comments in each of the first 12 threads.
- Markdown is excluded, which also leaves out agent skills and instructions that may have real leverage.
- Incidents, mentoring, planning, and other repos are invisible.
