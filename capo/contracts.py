"""Provider-independent structured decisions and strict local validation."""


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
TASK = object_schema({"title": TEXT, "instructions": TEXT,
                      "worker": {"type": "string", "enum": ["codex", "grok"]}})
PLAN = object_schema({"summary": TEXT, "acceptance": TEXTS,
                      "tasks": {"type": "array", "items": TASK}})
CHANGE = object_schema({"path": TEXT, "content": TEXT,
                        "delete": {"type": "boolean"}})
IMPLEMENTATION = object_schema({"summary": TEXT,
                                "changes": {"type": "array", "items": CHANGE}})
REVIEW = object_schema({"approved": {"type": "boolean"}, "findings": TEXTS})
DECISION = object_schema({"accepted": {"type": "boolean"}, "reason": TEXT})


def validate(value, schema, path="result"):
    """Validate the deliberately small schema subset used by all contracts."""
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    if type(value) is not types[schema["type"]]:
        raise ValueError(f"{path}: expected {schema['type']}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unsupported value {value!r}")
    if isinstance(value, dict):
        required = set(schema.get("required", schema["properties"]))
        if not required <= set(value) or not set(value) <= set(schema["properties"]):
            raise ValueError(f"{path}: missing or unexpected fields")
        for key, child in value.items():
            validate(child, schema["properties"][key], f"{path}.{key}")
    if isinstance(value, list):
        for i, child in enumerate(value):
            validate(child, schema["items"], f"{path}[{i}]")
