"""List every path that ever held a blob the partial clone left out (over 1 MB).

These are lockfiles, generated schemas and binaries. The replay must skip them,
because git cannot diff a file whose contents were never downloaded.
"""
import collections
import subprocess


def large_paths(repo):
    log = subprocess.run(
        ["git", "-C", repo, "log", "--raw", "--no-abbrev", "--no-renames", "--format=", "-z"],
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode("utf-8", "replace")
    paths = collections.defaultdict(set)
    fields = iter(log.split("\0"))
    for field in fields:  # alternating ":<modes> <shas> <status>" and path
        if field.lstrip("\n").startswith(":"):
            path = next(fields)
            for sha in field.split()[2:4]:
                if set(sha) != {"0"}:
                    paths[sha].add(path)

    result = subprocess.run(
        ["git", "--no-lazy-fetch", "-C", repo, "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input="\n".join(paths).encode() + b"\n",
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode()

    missing = set()
    for line in result.splitlines():
        fields = line.split()
        if fields[-1] == "missing":
            missing |= paths[fields[0]]
    return sorted(missing)
