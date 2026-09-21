"""CLI contracts. Auth remains with each provider; no API client is used."""

import json
from pathlib import Path


from .process import WorkerError, run_process


def decode_json(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise WorkerError("Expected a JSON object")
    return value


class AuthenticationError(WorkerError):
    """Provider login needs attention; safe to identify without exposing output."""


class ServiceAuthenticationError(AuthenticationError):
    def __init__(self, service):
        super().__init__(service + ' connection needs renewal.')
        self.service = service


def reported_failure(envelope, provider, directory):
    """Normalize reported errors without copying provider output into user errors."""
    from datetime import datetime
    import time
    from .recovery import RateLimited
    raw = envelope.get('error')
    data = raw if isinstance(raw, dict) else envelope
    message = str(data.get('message', envelope.get('result', raw or ''))).lower()
    if any(word in message for word in ('failed to authenticate', 'oauth session expired', 'authentication required')):
        raise AuthenticationError(provider + ' login has expired. Reconnect on the computer running Capo.')
    if data.get('status') == 429 or envelope.get('stopReason') in ('rate_limit', 'quota_exceeded') or any(word in message for word in ('rate limit', 'usage limit', 'hit your limit', 'too many requests')):
        reset = time.time() + 3600
        try:
            if 'reset_at' in data:
                value = data['reset_at']
                reset = float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            elif 'retry_after' in data:
                reset = time.time() + max(0, float(data['retry_after']))
        except (TypeError, ValueError, OverflowError):
            pass
        raise RateLimited(reset)
    if any(word in message for word in ('overloaded', 'temporarily unavailable', 'service unavailable')):
        raise ConnectionError(provider + ' is temporarily unavailable.')
    raise WorkerError(f'{provider} reported failure; inspect {directory}')


def run_cli(provider, argv, cwd, directory, timeout, stdin=None):
    """Classify structured failures even when the CLI exits nonzero."""
    from .process import CleanupUncertain
    try:
        return run_process(argv, cwd, directory, timeout, stdin)
    except CleanupUncertain:
        raise
    except WorkerError:
        for name in ('stdout.txt', 'stderr.txt'):
            path = directory/name
            if not path.exists():
                continue
            with path.open() as stream:
                lines = stream.read(200000).splitlines()
            for line in reversed(lines):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or not (event.get('is_error') or event.get('type') in ('error','turn.failed') or event.get('error')):
                    continue
                try:
                    reported_failure(event, provider, directory)
                except AuthenticationError:
                    raise
                except WorkerError:
                    continue
        raise


class Providers:
    def __init__(self, timeout=900, config=None, capacity=None, deadline=None):
        from .transport import load_config, validate_config
        from .capacity import Capacity
        self.timeout = timeout
        self.deadline = deadline
        self.config = load_config() if config is None else validate_config(config)
        self.capacity = Capacity(providers_config=self.config) if capacity is None else capacity

    def call(self, provider, prompt, schema, cwd, directory, images=None):
        from .recovery import RateLimited
        if self.capacity:
            self.capacity.check(provider)
        import time
        original_timeout = self.timeout
        if self.deadline is not None:
            remaining = self.deadline - time.time()
            if remaining <= 0:
                raise TimeoutError('The execution window has closed')
            self.timeout = min(original_timeout, remaining)
        try:
            return self._call(provider, prompt, schema, cwd, directory, images)
        except RateLimited as exc:
            if self.capacity:
                self.capacity.limited(provider, exc.reset_at)
            raise
        finally:
            self.timeout = original_timeout

    def _call(self, provider, prompt, schema, cwd, directory, images=None):
        if images and provider != "claude":
            raise ValueError("Image input is currently supported by Claude only")
        from .communication import writing_style
        prompt = writing_style() + prompt
        directory.mkdir(parents=True, exist_ok=True)
        prompt_file = directory / "prompt.txt"
        prompt_file.write_text(prompt)
        schema_file = directory / "schema.json"
        schema_file.write_text(json.dumps(schema))
        if provider == "claude":
            argv = ["claude", "-p", "--output-format", "json", "--tools", "",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--setting-sources", "", "--permission-mode", "dontAsk",
                    "--json-schema", json.dumps(schema), "--no-session-persistence",
                    "--settings", json.dumps({"autoMemoryEnabled": False, "disableAllHooks": True})]
            input_text = prompt
            if images:
                import base64
                if len(images) > 3:
                    raise ValueError("At most three images per request")
                blocks = [{"type": "text", "text": prompt}]
                for picture in images:
                    if picture["media_type"] not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                        raise ValueError("Unsupported image type")
                    if len(base64.b64decode(picture["data"], validate=True)) > 4_000_000:
                        raise ValueError("Image is too large")
                    blocks.append({"type": "image", "source": {"type": "base64", **picture}})
                argv[argv.index("--output-format") + 1] = "stream-json"
                argv += ["--input-format", "stream-json", "--verbose"]
                input_text = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}) + "\n"
            output = run_cli("Claude", argv, cwd, directory, self.timeout, input_text)
            if images:
                results = [decode_json(line) for line in output.splitlines() if line.strip()]
                envelope = next((value for value in reversed(results) if value.get("type") == "result"), None)
                if envelope is None:
                    raise WorkerError("Claude image request returned no result")
            else:
                envelope = decode_json(output)
            if envelope.get("is_error") or envelope.get("subtype", "success") != "success":
                reported_failure(envelope, 'Claude', directory)
            result = envelope.get("structured_output")
            if result is None:
                result = decode_json(envelope.get("result", ""))
        elif provider == "codex":
            final = directory / "result.json"
            argv = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--json",
                    "--output-schema", str(schema_file), "--output-last-message", str(final), "-"]
            output = run_cli("Codex", argv, cwd, directory, self.timeout, prompt)
            for line in output.splitlines():
                event = json.loads(line)
                if event.get("type") in ("error", "turn.failed"):
                    reported_failure(event, 'Codex', directory)
            result = decode_json(final.read_text())
        elif provider == "grok":
            argv = ["grok", "--prompt-file", str(prompt_file), "--output-format", "json",
                    "--json-schema", json.dumps(schema), "--tools", "",
                    "--no-subagents", "--sandbox", "read-only", "--permission-mode", "dontAsk"]
            if self.config.get("grok", {}).get("transport") == "lima":
                from .transport import run_grok
                output = run_grok(self.config["grok"], prompt, schema, directory, self.timeout)
            else:
                output = run_cli("Grok", argv, cwd, directory, self.timeout)
            envelope = decode_json(output)
            if (envelope.get("is_error") or envelope.get("error")
                    or envelope.get("type") == "error"
                    or envelope.get("stopReason", "end_turn") != "end_turn"):
                reported_failure(envelope, 'Grok', directory)
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
