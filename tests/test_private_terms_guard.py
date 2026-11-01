"""Proves the private-terms guard (scripts/check-private-terms.sh + .githooks/) in throwaway repos.

Each test builds a fresh git repo in tmp_path with a fake term list ("Zorbler"), so the real
.private-terms is never read: the guard resolves its list through the throwaway repo's toplevel.
Git runs with an isolated config (fake identity, no global or system config, no GIT_* variables),
so the tests behave the same on any machine and never touch the real repo.

TIDEWATCH_GUARD_SCRIPT points the tests at another copy of the guard. It is used to prove that the
tests can fail by running them against deliberately broken copies, without ever editing the
real guard.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = Path(
    os.environ.get("TIDEWATCH_GUARD_SCRIPT", REPO_ROOT / "scripts" / "check-private-terms.sh")
)
HOOKS = REPO_ROOT / ".githooks"
TERM = "Zorbler"  # fake term; it is not on the real list


class Repo:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "work"
        self.remote = tmp_path / "remote.git"
        home = tmp_path / "home"
        home.mkdir()
        gitconfig = tmp_path / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = Test\n\temail = test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n"
            "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
            "[diff]\n\trenames = true\n"
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(home), GIT_CONFIG_GLOBAL=str(gitconfig), GIT_CONFIG_NOSYSTEM="1")

        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], env=self.env, check=True)
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.root / "scripts").mkdir()
        shutil.copy(GUARD, self.root / "scripts" / "check-private-terms.sh")
        (self.root / "scripts" / "check-private-terms.sh").chmod(0o755)
        shutil.copytree(HOOKS, self.root / ".githooks")
        self.write(".gitignore", ".private-terms\n")
        self.write(".private-terms", f"{TERM}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "main")
        # Hooks are off until a test calls enable_hooks(), like a fresh clone before
        # `git config core.hooksPath .githooks`. Push tests use that window to create
        # bad commits without any hook bypass.

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            errors="replace",
            check=check,
        )

    def write(self, name: str, content: str | bytes) -> None:
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)

    def enable_hooks(self) -> None:
        self.git("config", "core.hooksPath", ".githooks")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def commit_file(self, name: str, content: str | bytes, message: str = "change"):
        self.write(name, content)
        self.git("add", name)
        return self.git("commit", "-q", "-m", message, check=False)

    def push(self, ref: str):
        return self.git("push", "-q", "origin", ref, check=False)

    def remote_has(self, ref: str) -> bool:
        out = subprocess.run(
            ["git", "ls-remote", str(self.remote), ref],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return out.strip() != ""


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def assert_blocked(result: subprocess.CompletedProcess, reason: str) -> None:
    """Blocked by the guard itself (not by a setup error), for the stated reason, term not shown."""
    assert result.returncode != 0
    assert "private-terms: blocked." in result.stderr
    assert reason in result.stderr
    assert TERM.lower() not in (result.stdout + result.stderr).lower()


def assert_fail_closed(result: subprocess.CompletedProcess) -> None:
    assert result.returncode != 0
    assert "is missing or empty" in result.stderr
    assert "Blocked." in result.stderr


# --- commit: blocked -------------------------------------------------------------------------


def test_commit_blocked_term_in_content(repo):
    repo.enable_hooks()
    before = repo.head()
    assert_blocked(repo.commit_file("notes.txt", f"hello {TERM}\n"), "A staged change")
    assert repo.head() == before


def test_commit_blocked_term_in_file_name(repo):
    # A pure rename: no content lines change, so only the file-name check can see the term.
    repo.write("clean.txt", "nothing to see\n")
    repo.git("add", "clean.txt")
    repo.git("commit", "-q", "-m", "add clean file")
    repo.enable_hooks()
    before = repo.head()
    repo.git("mv", "clean.txt", f"{TERM.lower()}-notes.txt")
    result = repo.git("commit", "-q", "-m", "rename", check=False)
    assert_blocked(result, "A staged file name")
    assert repo.head() == before


def test_commit_blocked_term_in_branch_name(repo):
    repo.enable_hooks()
    repo.git("switch", "-q", "-c", f"feature/{TERM.lower()}-fix")
    before = repo.head()
    assert_blocked(repo.commit_file("notes.txt", "clean\n"), "The branch name")
    assert repo.head() == before


def test_commit_blocked_term_in_message(repo):
    repo.enable_hooks()
    before = repo.head()
    result = repo.commit_file("notes.txt", "clean\n", message=f"notes about {TERM}")
    assert_blocked(result, "The commit message")
    assert repo.head() == before


def test_commit_blocked_when_list_missing(repo):
    repo.enable_hooks()
    (repo.root / ".private-terms").unlink()
    assert_fail_closed(repo.commit_file("notes.txt", "clean\n"))


def test_commit_blocked_when_list_has_only_comments(repo):
    repo.enable_hooks()
    repo.write(".private-terms", "# only a comment\n\n   \n")
    assert_fail_closed(repo.commit_file("notes.txt", "clean\n"))


def test_commit_blocked_term_line_with_trailing_spaces(repo):
    repo.enable_hooks()
    repo.write(".private-terms", f"{TERM}   \n")
    assert_blocked(repo.commit_file("notes.txt", f"hello {TERM}\n"), "A staged change")


def test_commit_blocked_term_line_with_crlf(repo):
    repo.enable_hooks()
    repo.write(".private-terms", f"{TERM}\r\n".encode())
    assert_blocked(repo.commit_file("notes.txt", f"hello {TERM}\n"), "A staged change")


def test_commit_blocked_term_in_binary_file(repo):
    repo.enable_hooks()
    data = b"\x89PNG\x00\x01\x02" + TERM.encode() + b"\x00\xff"
    assert_blocked(repo.commit_file("image.bin", data), "A staged change")


def test_commit_blocked_term_case_insensitive(repo):
    repo.enable_hooks()
    assert_blocked(repo.commit_file("notes.txt", f"hello {TERM.upper()}\n"), "A staged change")


# --- push: blocked (bad commits are made before the hooks are enabled) ------------------------


def test_push_blocked_term_in_commit_content(repo):
    repo.git("switch", "-q", "-c", "feature-content")
    repo.commit_file("notes.txt", f"hello {TERM}\n")
    repo.enable_hooks()
    assert_blocked(repo.push("feature-content"), "Commit ")
    assert not repo.remote_has("refs/heads/feature-content")


def test_push_blocked_term_in_branch_name(repo):
    branch = f"{TERM.lower()}-fix"
    repo.git("switch", "-q", "-c", branch)
    repo.commit_file("notes.txt", "clean\n")
    repo.enable_hooks()
    assert_blocked(repo.push(branch), "A branch or tag name being pushed")
    assert not repo.remote_has(f"refs/heads/{branch}")


def test_push_blocked_term_in_annotated_tag_message(repo):
    repo.git("tag", "-a", "v1", "-m", f"release for {TERM}")
    repo.enable_hooks()
    assert_blocked(repo.push("refs/tags/v1"), "An annotated tag")
    assert not repo.remote_has("refs/tags/v1")


def test_push_blocked_term_in_binary_file(repo):
    repo.git("switch", "-q", "-c", "feature-binary")
    repo.commit_file("image.bin", b"\x89PNG\x00\x01" + TERM.encode() + b"\x00\xff")
    repo.enable_hooks()
    assert_blocked(repo.push("feature-binary"), "Commit ")
    assert not repo.remote_has("refs/heads/feature-binary")


def test_push_blocked_term_only_in_commit_message(repo):
    repo.git("switch", "-q", "-c", "feature-message")
    repo.commit_file("notes.txt", "clean\n", message=f"fix for {TERM}")
    repo.enable_hooks()
    assert_blocked(repo.push("feature-message"), "Commit ")
    assert not repo.remote_has("refs/heads/feature-message")


# --- allowed ---------------------------------------------------------------------------------


def test_commit_allowed_no_over_blocking(repo):
    repo.enable_hooks()
    before = repo.head()
    near_miss = TERM[:-1]  # "Zorble": a prefix of the term is not the term
    result = repo.commit_file("notes.txt", f"hello {near_miss}\n")
    assert result.returncode == 0, result.stderr
    assert repo.head() != before


def test_commit_allowed_clean(repo):
    repo.enable_hooks()
    before = repo.head()
    result = repo.commit_file("notes.txt", "clean\n", message="add notes")
    assert result.returncode == 0, result.stderr
    assert repo.head() != before


def test_push_allowed_clean_branch(repo):
    repo.enable_hooks()
    repo.git("switch", "-q", "-c", "feature-ok")
    assert repo.commit_file("notes.txt", "clean\n").returncode == 0
    result = repo.push("feature-ok")
    assert result.returncode == 0, result.stderr
    assert repo.remote_has("refs/heads/feature-ok")


def test_push_allowed_clean_tag(repo):
    repo.enable_hooks()
    repo.git("tag", "-a", "v1", "-m", "first release")
    result = repo.push("refs/tags/v1")
    assert result.returncode == 0, result.stderr
    assert repo.remote_has("refs/tags/v1")


# --- find ------------------------------------------------------------------------------------


def test_find_reports_line_numbers_without_term(repo):
    repo.write("notes.txt", f"first\nhello {TERM}\nlast\n")
    result = subprocess.run(
        ["scripts/check-private-terms.sh", "find", "notes.txt"],
        cwd=repo.root,
        env=repo.env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "notes.txt: line 2"
    assert TERM.lower() not in (result.stdout + result.stderr).lower()
