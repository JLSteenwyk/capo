"""CLI contracts. Auth remains with each provider; no API client is used."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path


class WorkerError(RuntimeError):
    pass


def run_process(argv, cwd, directory, timeout, stdin=None):
    """Persist output to files, bound duration, and terminate child process groups."""
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Prefer existing CLI subscription authentication over ambient API keys.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "XAI_API_KEY",
                "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        environment.pop(key, None)
    started = time.time()
    with (directory / "stdout.txt").open("w") as out, (directory / "stderr.txt").open("w") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                                   stdout=out, stderr=err, text=True, start_new_session=True)
        (directory / "process.json").write_text(json.dumps({
            "pid": process.pid, "started": started, "command": argv[0]}))
        try:
            deadline = time.monotonic() + timeout
            pending_input = stdin
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
                    raise WorkerError(f"Attempt exceeded its 8 MB log limit; inspect {directory}")
                try:
                    process.communicate(pending_input, timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            # The parent may exit on TERM while a child ignores it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise
    # Background descendants are outside the bounded attempt contract.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    (directory / "exit.json").write_text(json.dumps({
        "returncode": process.returncode, "seconds": time.time() - started}))
    if sum((directory / name).stat().st_size for name in ("stdout.txt", "stderr.txt")) > 8_000_000:
        raise WorkerError(f"Attempt exceeded its 8 MB log limit; inspect {directory}")
    if (directory / "stdout.txt").stat().st_size > 4_000_000:
        raise WorkerError(f"Output exceeds 4 MB; inspect {directory}")
    output = (directory / "stdout.txt").read_text(errors="replace")
    if process.returncode:
        raise WorkerError(f"{Path(argv[0]).name} exited {process.returncode}; inspect {directory}")
    return output


def decode_json(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise WorkerError("Expected a JSON object")
    return value


class Providers:
    def __init__(self, timeout=900):
        self.timeout = timeout

    def call(self, provider, prompt, schema, cwd, directory):
        directory.mkdir(parents=True, exist_ok=True)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(prompt)
        schema_file = directory / "schema.json"
        schema_file.write_text(json.dumps(schema))
        if provider == "claude":
            argv = ["claude", "-p", "--output-format", "json", "--tools", "",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--setting-sources", "", "--permission-mode", "dontAsk",
                    "--json-schema", json.dumps(schema)]
            envelope = decode_json(run_process(argv, cwd, directory, self.timeout, prompt))
            if envelope.get("is_error") or envelope.get("subtype", "success") != "success":
                raise WorkerError(f"Claude reported failure; inspect {directory}")
            result = envelope.get("structured_output")
            if result is None:
                result = decode_json(envelope.get("result", ""))
        elif provider == "codex":
            final = directory / "result.json"
            argv = ["codex", "exec", "--sandbox", "read-only", "--json",
                    "--output-schema", str(schema_file), "--output-last-message", str(final), "-"]
            output = run_process(argv, cwd, directory, self.timeout, prompt)
            for line in output.splitlines():
                event = json.loads(line)
                if event.get("type") in ("error", "turn.failed"):
                    raise WorkerError(f"Codex reported failure; inspect {directory}")
            result = decode_json(final.read_text())
        elif provider == "grok":
            argv = ["grok", "--prompt-file", str(prompt_file), "--output-format", "json",
                    "--json-schema", json.dumps(schema), "--tools", "",
                    "--no-subagents", "--sandbox", "read-only", "--permission-mode", "dontAsk"]
            envelope = decode_json(run_process(argv, cwd, directory, self.timeout))
            if (envelope.get("is_error") or envelope.get("error")
                    or envelope.get("type") == "error"
                    or envelope.get("stopReason", "end_turn") != "end_turn"):
                raise WorkerError(f"Grok reported failure; inspect {directory}")
            result = envelope.get("structuredOutput")
            if result is None:
                result = envelope.get("structured_output")
            if result is None:
                result = envelope.get("result", envelope.get("text", envelope))
            if isinstance(result, str):
                result = decode_json(result)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        if not isinstance(result, dict):
            raise WorkerError(f"Invalid structured result from {provider}")
        (directory / "response.json").write_text(json.dumps(result, indent=2))
        return result
