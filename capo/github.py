"""Explicit GitHub publication of an immutable, verified candidate."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .repository import changed_diff, git
from .runtime import exclusive


def repository_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        raise ValueError("Expected GitHub OWNER/REPO")
    if value.split("/")[1] in (".", ".."):
        raise ValueError("Invalid GitHub repository")
    return value


def remote_repository(url):
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?", url)
    if not match:
        raise ValueError("Source origin must identify a github.com repository")
    return repository_name(match[1])


def github_environment(auth="default"):
    environment = os.environ.copy()
    environment["GH_HOST"] = "github.com"
    environment["GH_PROMPT_DISABLED"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if auth == "keyring":
        environment.pop("GH_TOKEN", None)
        environment.pop("GITHUB_TOKEN", None)
    elif auth != "default":
        raise ValueError("Unknown GitHub authentication mode")
    return environment


class GitHub:
    def __init__(self, auth="default"):
        self.env = github_environment(auth)

    def gh(self, *args):
        process = subprocess.run(["gh", *args], env=self.env, capture_output=True,
                                 text=True, timeout=60)
        if process.returncode:
            # Do not echo credentials or arbitrary remote response text.
            raise RuntimeError(f"GitHub command failed (exit {process.returncode}); check gh auth status and repository access")
        return process.stdout.strip()

    def issue(self, repo, number):
        repository_name(repo)
        if number < 1:
            raise ValueError("Expected a positive issue number")
        return json.loads(self.gh("issue", "view", str(number), "--repo", repo,
                                  "--json", "title,body,url"))

    def remote_ref(self, workspace, repo, branch):
        output = git(workspace, "ls-remote", "--heads", f"https://github.com/{repo}.git",
                     f"refs/heads/{branch}", env=self.env)
        return output.split()[0] if output else None

    def push_new(self, workspace, payload):
        ref = f"refs/heads/{payload['branch']}"
        # An explicit empty lease permits creation only, never replacement.
        git(workspace, "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
            "push", f"--force-with-lease={ref}:", f"https://github.com/{payload['repository']}.git",
            f"{payload['commit']}:{ref}", env=self.env)

    def find_pr(self, payload):
        owner, repo = payload["repository"].split("/")
        results = json.loads(self.gh("pr", "list", "--repo", payload["repository"],
            "--state", "all", "--head", payload["branch"], "--base", payload["base_branch"],
            "--json", "number,url,headRefOid,title,body,isDraft,state,headRepositoryOwner,headRepository"))
        matches = [row for row in results if
                   (row.get("headRepositoryOwner") or {}).get("login", "").lower() == owner.lower()
                   and (row.get("headRepository") or {}).get("name", "").lower() == repo.lower()]
        if len(matches) > 1:
            raise ValueError("Multiple PRs found for this objective branch; reconcile manually")
        return matches[0] if matches else None

    def create_pr(self, payload, body_file):
        self.gh("pr", "create", "--repo", payload["repository"], "--base", payload["base_branch"],
                "--head", payload["branch"], "--title", payload["title"],
                "--body-file", str(body_file), "--draft")
        result = self.find_pr(payload)
        if result is None:
            raise RuntimeError("PR creation returned but reconciliation found no PR; retry publication")
        return result

    def status(self, repo, number):
        return json.loads(self.gh("pr", "view", str(number), "--repo", repo, "--json",
            "number,url,state,isDraft,headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup"))


def payload_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verified_workspace(objective):
    if objective["status"] != "completed" or not objective.get("accepted_tree"):
        raise ValueError("Publication requires a completed objective with a recorded verified tree")
    workspace = Path(objective["workspace"])
    if git(workspace, "rev-parse", "HEAD") != objective["base"]:
        raise ValueError("Workspace HEAD changed since verification")
    diff = changed_diff(workspace, objective["base"])
    if git(workspace, "write-tree") != objective["accepted_tree"]:
        raise ValueError("Workspace changed since verification; create a new objective to verify the changes")
    if not diff:
        raise ValueError("No changes to publish")
    return workspace, diff


def prepare(store, objective_id, repo, base_branch, title=None):
    repository_name(repo)
    with exclusive(store.home):
        objective = store.get(objective_id)
        workspace, diff = verified_workspace(objective)
        source = remote_repository(git(objective["repo"], "remote", "get-url", "origin"))
        if source.lower() != repo.lower():
            raise ValueError("Publication repository must match the source origin")
        git(workspace, "check-ref-format", f"refs/heads/{base_branch}")
        if objective.get("publication"):
            old = objective["publication"]["payload"]
            if (old["repository"].lower() != repo.lower() or old["base_branch"] != base_branch
                    or title is not None and old["title"] != title):
                raise ValueError("A different publication is already prepared for this objective")
            return objective["publication"]
        title = title if title is not None else objective["request"].splitlines()[0][:120]
        if not title.strip() or "\n" in title or "\r" in title or len(title) > 256:
            raise ValueError("PR title must be a single nonempty line, at most 256 characters")
        verification = objective["verification"]
        body = (f"{objective['plan']['summary']}\n\n"
                f"{verification['decision']['reason']}\n\nValidation:\n"
                + "\n".join(f"- Passed: `{json.dumps(check['command'])}`" for check in verification["checks"])
                + "\n\nReviewed by: " + ", ".join(row["provider"] for row in verification["reviews"])
                + f".\n\nTeam: {objective.get('team_name', 'SPARKITscience')}. Capo objective: `{objective_id}`.\n")
        if objective.get("source"):
            body += f"\nRelated issue: {objective['source']}\n"
        commit = git(workspace, "-c", "user.name=Capo", "-c", "user.email=capo@localhost",
                     "-c", "commit.gpgSign=false", "commit-tree", objective["accepted_tree"],
                     "-p", objective["base"], "-m", title)
        branch = f"capo/{objective_id}"
        git(workspace, "update-ref", f"refs/heads/{branch}", commit, "")
        payload = {"repository": repo, "base_branch": base_branch, "base_commit": objective["base"],
                   "branch": branch, "commit": commit, "tree": objective["accepted_tree"],
                   "title": title, "body": body, "draft": True}
        publication = {"status": "prepared", "digest": payload_digest(payload), "payload": payload}
        directory = store.home / "artifacts" / objective_id
        (directory / "pull-request.md").write_text(f"# {title}\n\n{body}")
        (directory / "publication.json").write_text(json.dumps(publication, indent=2))
        (directory / "changes.patch").write_text(diff)
        objective["publication"] = publication
        store.save(objective, "publication_prepared")
        return publication


def publish(store, objective_id, digest, gateway=None):
    gateway = gateway or GitHub()
    with exclusive(store.home):
        objective = store.get(objective_id)
        publication = objective.get("publication")
        if not publication:
            raise ValueError("Prepare the publication first")
        payload = publication["payload"]
        if digest != publication["digest"] or digest != payload_digest(payload):
            raise ValueError("Publication digest does not match the prepared content")
        workspace, _ = verified_workspace(objective)
        if git(workspace, "rev-parse", f"{payload['commit']}^{{tree}}") != objective["accepted_tree"]:
            raise ValueError("Prepared commit differs from the verified tree")
        if publication["status"] == "published":
            return publication
        existing = gateway.find_pr(payload)
        if existing is None:
            remote_base = gateway.remote_ref(workspace, payload["repository"], payload["base_branch"])
            if remote_base != payload["base_commit"]:
                raise ValueError("Remote base moved or is missing; verify a new objective against the current base")
            remote_head = gateway.remote_ref(workspace, payload["repository"], payload["branch"])
            if remote_head not in (None, payload["commit"]):
                raise ValueError("Remote objective branch contains different work; refusing to replace it")
            publication["status"] = "publishing"
            store.save(objective, "publication_started")
            if remote_head is None:
                gateway.push_new(workspace, payload)
            body_file = store.home / "artifacts" / objective_id / "pr-body.txt"
            body_file.write_text(payload["body"])
            existing = gateway.create_pr(payload, body_file)
        if (existing["headRefOid"] != payload["commit"] or existing["title"] != payload["title"]
                or existing["body"] != payload["body"]):
            raise ValueError("Existing PR differs from the prepared publication; reconcile manually")
        publication["status"] = "published"
        publication["pr"] = {key: existing[key] for key in ("number", "url", "state", "isDraft")}
        store.save(objective, "published")
        return publication


def sync(store, objective_id, gateway=None):
    gateway = gateway or GitHub()
    with exclusive(store.home):
        objective = store.get(objective_id)
        publication = objective.get("publication", {})
        if publication.get("status") != "published":
            raise ValueError("Publish the objective before syncing its PR")
        status = gateway.status(publication["payload"]["repository"], publication["pr"]["number"])
        status["matches_verified_commit"] = status["headRefOid"] == publication["payload"]["commit"]
        publication["remote_status"] = status
        store.save(objective, "github_status_updated")
        return status
