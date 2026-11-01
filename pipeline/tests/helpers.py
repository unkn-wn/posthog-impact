"""Real git repositories and a local stand-in for GitHub's GraphQL API."""
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE_SIZE = 2


def numbered_lines(name, count):
    """Long enough to count as real code for move detection."""
    return [f"{name} statement number {i:03d} = compute()" for i in range(count)]


def days_ago(days):
    return datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=days)


class Repo:
    def __init__(self, path):
        self.path = path
        self.git("init", "--quiet", "--initial-branch=master")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "uploadpack.allowFilter", "true")

    def git(self, *args, **env):
        subprocess.run(["git", "-C", str(self.path), *args], check=True, env={**os.environ, **env})

    def write(self, name, lines):
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def read(self, name):
        return (self.path / name).read_text(encoding="utf-8").splitlines()

    def edit(self, name, line, text):
        """Replace one line, numbered from 1."""
        lines = self.read(name)
        lines[line - 1] = text
        self.write(name, lines)

    def commit(self, age_days, author, title):
        stamp = days_ago(age_days).isoformat()
        self.git("add", "-A")
        self.git(
            "commit", "--quiet", "--allow-empty", "-m", title,
            GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="dev@example.com", GIT_AUTHOR_DATE=stamp,
            GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="dev@example.com", GIT_COMMITTER_DATE=stamp,
        )


class FakeGitHub:
    """Serves PRs per merge day, a few at a time, like the real search API."""

    def __init__(self):
        self.prs_by_day = {}
        self.queries = []
        self.attempts = 0
        self.fail_next = 0
        self.fail_status = 502
        self.overcount = {}  # day -> extra PRs to claim in issueCount
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                fake.attempts += 1
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fake.fail_next:
                    fake.fail_next -= 1
                    self.send_error(fake.fail_status)
                    return
                body = json.dumps(fake.answer(request["variables"])).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/graphql"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, variables):
        self.queries.append(variables)
        day = re.search(r"merged:(\S+)", variables["q"]).group(1)
        prs = self.prs_by_day.get(day, [])
        start = int(variables["c"] or 0)
        end = start + PAGE_SIZE
        return {"data": {"search": {
            "issueCount": len(prs) + self.overcount.get(day, 0),
            "pageInfo": {"hasNextPage": end < len(prs), "endCursor": str(end)},
            "nodes": prs[start:end],
        }}}

    def days_queried(self):
        return {re.search(r"merged:(\S+)", q["q"]).group(1) for q in self.queries}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def make_pr(number, login, merged_at=None, autonomy="Human-driven", reviews=(), bot=False):
    """A PR as GitHub returns it. reviews is a list of (login, text)."""
    return {
        "number": number,
        "mergedAt": (merged_at or days_ago(50)).isoformat(),
        "author": {"login": login, "__typename": "Bot" if bot else "User"},
        "body": f"## Agent context\n**Autonomy:** {autonomy}\n",
        "reviews": {"nodes": [
            {"author": {"login": who, "__typename": "User"}, "bodyText": text} for who, text in reviews
        ]},
        "reviewThreads": {"nodes": []},
    }
