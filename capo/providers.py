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


class Providers:
    def __init__(self, timeout=900, config=None):
        from .transport import load_config, validate_config
        self.timeout = timeout
        self.config = load_config() if config is None else validate_config(config)

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
