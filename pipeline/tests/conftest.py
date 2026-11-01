import pytest

import fetch_prs
from helpers import FakeGitHub, Repo


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo").mkdir()
    return Repo(tmp_path / "repo")


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(fetch_prs, "API", fake.url)
    yield fake
    fake.close()


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(fetch_prs.time, "sleep", lambda _: None)
