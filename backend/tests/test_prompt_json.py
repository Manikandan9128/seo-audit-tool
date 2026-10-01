import json

from app.services.prompt_json import compact_json


def test_compact_json_has_no_padding_and_round_trips():
    data = {"keyword": "payroll", "rows": [{"volume": 1300, "kd": 28}], "note": "café"}
    text = compact_json(data)
    assert "\n" not in text and ": " not in text and ", " not in text
    assert "café" in text  # not escaped to é, which costs more tokens
    assert json.loads(text) == data


def test_compact_json_keeps_default_hook_for_non_json_values():
    from datetime import date

    assert compact_json({"d": date(2026, 10, 1)}, default=str) == '{"d":"2026-10-01"}'


def test_prompts_built_from_data_carry_no_indentation(monkeypatch):
    from app.services import core_problem_service

    seen = {}

    def fake_attempts(prompt, max_tokens, errors):
        seen["prompt"] = prompt
        return iter(())

    monkeypatch.setattr(core_problem_service, "iter_text_attempts", fake_attempts)
    core_problem_service.generate_core_problem({"pages_checked": 20, "issues": [{"url": "/a", "issues": ["x"]}]})
    findings_part = seen["prompt"].split('"pages_checked"')[1][:80]
    assert "\n   " not in findings_part
