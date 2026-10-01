"""JSON for AI prompts. A model reads compact JSON exactly as well as indented
JSON, and the indentation, line breaks and spaces after ":" and "," are input
tokens you pay for on every call (and again on every retry). Use this for data
placed INTO a prompt - never for anything shown to a person or stored."""

import json


def compact_json(obj, **kwargs) -> str:
    kwargs.setdefault("separators", (",", ":"))
    kwargs.setdefault("ensure_ascii", False)
    return json.dumps(obj, **kwargs)
