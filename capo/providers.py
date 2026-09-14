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


class Providers:
    def __init__(self, timeout=900, config=None):
        from .transport import load_config, validate_config
        self.timeout = timeout
        self.config = load_config() if config is None else validate_config(config)

    def call(self, provider, prompt, schema, cwd, directory, images=None):
        if images and provider != "claude":
            raise ValueError("Image input is currently supported by Claude only")
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
            output = run_process(argv, cwd, directory, self.timeout, input_text)
            if images:
                results = [decode_json(line) for line in output.splitlines() if line.strip()]
                envelope = next((value for value in reversed(results) if value.get("type") == "result"), None)
                if envelope is None:
                    raise WorkerError("Claude image request returned no result")
            else:
                envelope = decode_json(output)
            if envelope.get("is_error") or envelope.get("subtype", "success") != "success":
                failure = str(envelope.get("result", "")).lower()
                if "failed to authenticate" in failure or "oauth session expired" in failure:
                    raise AuthenticationError("Claude login has expired. Reconnect Claude on the computer running Capo.")
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
            if self.config.get("grok", {}).get("transport") == "lima":
                from .transport import run_grok
                output = run_grok(self.config["grok"], prompt, schema, directory, self.timeout)
            else:
                output = run_process(argv, cwd, directory, self.timeout)
            envelope = decode_json(output)
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
