import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capo.cli import add_objective
from capo.github import (GitHub, github_environment, payload_digest, prepare, publish,
                             remote_repository, repository_name, sync)
from capo.repository import git
from capo.runtime import Runtime
from capo.store import Store
from test_capo import FakeProviders


class FakeGitHub:
    def __init__(self, base):
        self.base = base
        self.head = None
        self.pr = None
        self.pushes = 0
        self.creates = 0
        self.timeout_after_create = False

    def remote_ref(self, workspace, repo, branch):
        return self.base if branch == "main" else self.head

    def find_pr(self, payload):
        return self.pr

    def push_new(self, workspace, payload):
        self.pushes += 1
        self.head = payload["commit"]

    def create_pr(self, payload, body_file):
        self.creates += 1
        assert body_file.read_text() == payload["body"]
        self.pr = {"number": 1, "url": "https://github.com/owner/project/pull/1",
                   "headRefOid": self.head, "title": payload["title"], "body": payload["body"],
                   "isDraft": True, "state": "OPEN"}
        if self.timeout_after_create:
            raise RuntimeError("Connection lost after GitHub created the PR")
        return self.pr

    def status(self, repo, number):
        return {"number": number, "headRefOid": self.head, "state": "OPEN", "statusCheckRollup": []}


class PublicationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "remote", "add", "origin", "https://github.com/owner/project.git")
        (self.repo / "maths.py").write_text("def add(a,b):\n    return a-b\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "fixture")
        self.store = Store(self.root / "state")
        args = argparse.Namespace(repo=self.repo, check=[f'{sys.executable} -c "from maths import add; assert add(2,3)==5"'])
        objective = add_objective(self.store, args, "Fix addition")
        self.objective = Runtime(self.store, FakeProviders()).run(objective["id"])
        self.gateway = FakeGitHub(self.objective["base"])

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def prepare(self):
        return prepare(self.store, self.objective["id"], "owner/project", "main")

    def test_prepare_preserves_verified_tree_and_source(self):
        objective = self.store.get(self.objective["id"])
        objective["verification"]["decision"]["reason"] = "Passed checks at " + str(self.root)
        self.store.save(objective, "private_verification_fixture")
        result = self.prepare()
        self.assertEqual(result["digest"], payload_digest(result["payload"]))
        self.assertNotIn(sys.executable, result["payload"]["body"])
        self.assertNotIn(str(self.root), result["payload"]["body"])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        workspace = Path(self.objective["workspace"])
        self.assertEqual(git(workspace, "rev-parse", result["payload"]["commit"] + "^{tree}"),
                         self.objective["accepted_tree"])
        self.assertEqual(self.prepare(), result)

    def test_prepare_reconciles_crash_before_ledger_save(self):
        original = self.store.save
        def fail_once(data, kind):
            if kind == "publication_prepared":
                raise RuntimeError("simulated crash")
            return original(data, kind)
        with patch.object(self.store, "save", side_effect=fail_once):
            with self.assertRaises(RuntimeError):
                self.prepare()
        draft = self.prepare()
        self.assertEqual(draft["status"], "prepared")
        self.assertEqual(self.prepare(), draft)

    def test_followup_invalidates_prepared_approval(self):
        draft = self.prepare()
        updated = self.store.add_followup(self.objective["id"], "event-one", "Also cover negative inputs")
        self.assertEqual(updated["status"], "queued")
        self.assertNotIn("accepted_tree", updated)
        with self.assertRaises(ValueError):
            publish(self.store, updated["id"], draft["digest"], self.gateway)
        self.assertEqual(self.gateway.pushes, 0)

    def test_published_objective_rejects_followup(self):
        draft = self.prepare()
        publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        with self.assertRaisesRegex(ValueError, "new objective"):
            self.store.add_followup(self.objective["id"], "event-one", "Change it")

    def test_different_target_repository_refused(self):
        with self.assertRaisesRegex(ValueError, "match the source"):
            prepare(self.store, self.objective["id"], "other/project", "main")

    def test_changes_after_verification_refused(self):
        (Path(self.objective["workspace"]) / "maths.py").write_text("not verified")
        with self.assertRaisesRegex(ValueError, "changed since verification"):
            self.prepare()

    def test_publish_is_idempotent(self):
        draft = self.prepare()
        for _ in range(2):
            result = publish(self.store, self.objective["id"], draft["digest"], self.gateway)
            self.assertEqual(result["status"], "published")
        self.assertEqual((self.gateway.pushes, self.gateway.creates), (1, 1))

    def test_retry_after_uncertain_pr_creation_reconciles(self):
        draft = self.prepare()
        self.gateway.timeout_after_create = True
        with self.assertRaises(RuntimeError):
            publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        result = publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        self.assertEqual(result["status"], "published")
        self.assertEqual((self.gateway.pushes, self.gateway.creates), (1, 1))

    def test_wrong_digest_causes_no_external_writes(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "digest"):
            publish(self.store, self.objective["id"], "wrong", self.gateway)
        self.assertEqual((self.gateway.pushes, self.gateway.creates), (0, 0))

    def test_remote_base_movement_prevents_publish(self):
        draft = self.prepare()
        self.gateway.base = "f" * 40
        with self.assertRaisesRegex(ValueError, "base moved"):
            publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        self.assertEqual(self.gateway.pushes, 0)

    def test_remote_branch_collision_is_not_overwritten(self):
        draft = self.prepare()
        self.gateway.head = "f" * 40
        with self.assertRaisesRegex(ValueError, "different work"):
            publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        self.assertEqual(self.gateway.pushes, 0)

    def test_remote_branch_from_prior_push_is_reused(self):
        draft = self.prepare()
        self.gateway.head = draft["payload"]["commit"]
        publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        self.assertEqual((self.gateway.pushes, self.gateway.creates), (0, 1))

    def test_sync_marks_remote_commit_changes(self):
        draft = self.prepare()
        publish(self.store, self.objective["id"], draft["digest"], self.gateway)
        self.assertTrue(sync(self.store, self.objective["id"], self.gateway)["matches_verified_commit"])
        self.gateway.head = "f" * 40
        self.assertFalse(sync(self.store, self.objective["id"], self.gateway)["matches_verified_commit"])

    def test_existing_pr_with_different_content_is_not_adopted(self):
        draft = self.prepare()
        self.gateway.head = draft["payload"]["commit"]
        self.gateway.pr = {"headRefOid": draft["payload"]["commit"], "title": "Unexpected title", "body": ""}
        with self.assertRaisesRegex(ValueError, "differs"):
            publish(self.store, self.objective["id"], draft["digest"], self.gateway)


class GitHubContractCase(unittest.TestCase):
    def test_remote_url_normalization(self):
        for url in ("https://github.com/owner/project.git", "git@github.com:owner/project.git",
                    "ssh://git@github.com/owner/project", "https://github.com/owner/project"):
            self.assertEqual(remote_repository(url), "owner/project")
        with self.assertRaises(ValueError):
            remote_repository("https://attacker.invalid/owner/project")

    def test_invalid_repo_names(self):
        for name in ("../project", "owner/..", "-owner/project", "owner/repo/extra", "owner/repo?x=1"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                repository_name(name)

    def test_keyring_mode_is_explicit(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake", "GH_TOKEN": "fake"}):
            self.assertIn("GH_TOKEN", github_environment())
            self.assertNotIn("GH_TOKEN", github_environment("keyring"))
            self.assertNotIn("GITHUB_TOKEN", github_environment("keyring"))

    def test_push_uses_absent_branch_lease(self):
        payload = {"branch": "capo/123", "repository": "owner/project", "commit": "a" * 40}
        with patch("capo.github.git") as command:
            GitHub().push_new(Path("/tmp"), payload)
            args = command.call_args.args
            self.assertIn("--force-with-lease=refs/heads/capo/123:", args)
            self.assertIn("a" * 40 + ":refs/heads/capo/123", args)

    def test_real_git_create_only_push_cannot_replace_existing_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, remote = root / "repo", root / "remote.git"
            repo.mkdir()
            remote.mkdir()
            git(repo, "init", "-b", "main")
            git(remote, "init", "--bare")
            git(repo, "config", "user.name", "Test")
            git(repo, "config", "user.email", "test@example.invalid")
            (repo / "file").write_text("first")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "first")
            first = git(repo, "rev-parse", "HEAD")
            payload = {"branch": "capo/test", "repository": "owner/project", "commit": first}
            def local_git(workspace, *args, **kwargs):
                return git(workspace, *(str(remote) if a == "https://github.com/owner/project.git" else a
                                        for a in args), **kwargs)
            with patch("capo.github.git", side_effect=local_git):
                GitHub().push_new(repo, payload)
                (repo / "file").write_text("second")
                git(repo, "add", ".")
                git(repo, "commit", "-m", "second")
                payload["commit"] = git(repo, "rev-parse", "HEAD")
                with self.assertRaises(ValueError):
                    GitHub().push_new(repo, payload)
            self.assertEqual(git(remote, "rev-parse", "refs/heads/capo/test"), first)


if __name__ == "__main__":
    unittest.main()
