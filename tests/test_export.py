import json

import pytest

from lazyline.export import _sanitize, from_dict, from_json, to_dict, to_json
from lazyline.models import (
    FunctionProfile,
    LineProfile,
    ProfileRun,
    RunMetadata,
)


def _make_run(*, with_memory=False, functions=None):
    if functions is None:
        mem = 512.0 if with_memory else None
        line_mem = 512.0 if with_memory else None
        functions = [
            FunctionProfile(
                module="mod",
                name="func",
                filename="/tmp/mod.py",
                start_line=1,
                total_time=0.5,
                call_count=10,
                lines=[
                    LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
                    LineProfile(
                        lineno=2,
                        hits=10,
                        time=0.5,
                        source="    return 1",
                        memory=line_mem,
                    ),
                ],
                memory=mem,
            )
        ]
    return ProfileRun(
        version=1,
        lazyline_version="0.0.4",
        metadata=RunMetadata(
            command=["python", "-c", "pass"],
            scope="mod",
            timestamp="2026-03-27T12:00:00+00:00",
            memory_tracking=with_memory,
            python_version="3.12.3",
            exit_code=0,
        ),
        functions=functions,
    )


# --- to_dict / from_dict roundtrip ---


def test_roundtrip_minimal():
    run = _make_run()
    data = to_dict(run)
    restored = from_dict(data)
    assert restored.version == run.version
    assert restored.lazyline_version == run.lazyline_version
    assert restored.metadata == run.metadata
    assert len(restored.functions) == 1
    fp = restored.functions[0]
    assert fp.module == "mod"
    assert fp.name == "func"
    assert fp.memory is None
    assert len(fp.lines) == 2
    assert fp.lines[0].source == "def func():"
    assert fp.lines[1].time == 0.5


def test_roundtrip_with_memory():
    run = _make_run(with_memory=True)
    data = to_dict(run)
    restored = from_dict(data)
    fp = restored.functions[0]
    assert fp.memory == 512.0
    assert fp.lines[0].memory is None
    assert fp.lines[1].memory == 512.0


def test_roundtrip_empty_functions():
    run = _make_run(functions=[])
    data = to_dict(run)
    restored = from_dict(data)
    assert restored.functions == []


def test_from_dict_missing_version():
    with pytest.raises(ValueError, match="missing 'version' field"):
        from_dict({"metadata": {}, "functions": []})


def test_from_dict_unsupported_version():
    data = to_dict(_make_run())
    data["version"] = 99
    with pytest.raises(ValueError, match="Unsupported schema version"):
        from_dict(data)


def test_from_dict_missing_metadata():
    data = {"version": 1, "functions": []}
    with pytest.raises(KeyError):
        from_dict(data)


def test_from_dict_optional_fields_default():
    data = to_dict(_make_run())
    # Remove optional fields from a line.
    del data["functions"][0]["lines"][0]["memory"]
    del data["functions"][0]["lines"][0]["source"]
    restored = from_dict(data)
    lp = restored.functions[0].lines[0]
    assert lp.memory is None
    assert lp.source == ""


def test_to_dict_none_memory_is_none():
    run = _make_run()
    data = to_dict(run)
    assert data["functions"][0]["memory"] is None
    # Verify it serializes to null in JSON.
    raw = json.dumps(data)
    assert '"memory": null' in raw


# --- to_json / from_json (filesystem) ---


def test_to_json_creates_file(tmp_path):
    path = tmp_path / "out.json"
    to_json(_make_run(), path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert "metadata" in data
    assert "functions" in data


def test_from_json_roundtrip(tmp_path):
    path = tmp_path / "out.json"
    run = _make_run(with_memory=True)
    to_json(run, path)
    restored = from_json(path)
    assert restored.metadata == run.metadata
    assert len(restored.functions) == len(run.functions)
    assert restored.functions[0].memory == run.functions[0].memory


def test_from_json_nonexistent():
    with pytest.raises(FileNotFoundError):
        from_json("/nonexistent/path.json")


def test_sanitize_strips_ansi():
    assert _sanitize("normal") == "normal"
    assert _sanitize("\033[2Jcleared") == "cleared"
    assert _sanitize("\033[1mbold\033[0m") == "bold"
    assert _sanitize("\033]0;title\x07text") == "text"


def test_from_dict_sanitizes_ansi():
    """ANSI escape sequences in string fields should be stripped on import."""
    data = to_dict(_make_run())
    data["functions"][0]["module"] = "\033[2Jevil"
    data["functions"][0]["name"] = "\033[1mbold\033[0m"
    data["functions"][0]["lines"][0]["source"] = "\033]0;pwned\x07code"
    run = from_dict(data)
    assert run.functions[0].module == "evil"
    assert run.functions[0].name == "bold"
    assert run.functions[0].lines[0].source == "code"


def test_from_dict_sanitizes_metadata_fields():
    """ANSI in metadata fields (scope, command, filename) should be stripped."""
    data = to_dict(_make_run())
    data["metadata"]["scope"] = "\033[2Jevil_scope"
    data["metadata"]["command"] = ["\033[1mbold_cmd\033[0m"]
    data["functions"][0]["filename"] = "\033[2J/venv/lib/mod.py"
    run = from_dict(data)
    assert run.metadata.scope == "evil_scope"
    assert run.metadata.command == ["bold_cmd"]
    assert run.functions[0].filename == "/venv/lib/mod.py"


def test_from_json_rejects_nan(tmp_path):
    """NaN values in JSON should be rejected."""
    path = tmp_path / "nan.json"
    data = to_dict(_make_run())
    # Write with allow_nan to create a file with NaN
    raw = json.dumps(data)
    raw = raw.replace('"total_time": 0.5', '"total_time": NaN')
    path.write_text(raw)
    with pytest.raises(ValueError, match="NaN"):
        from_json(path)


def test_from_json_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{")
    with pytest.raises(json.JSONDecodeError):
        from_json(path)


def test_from_json_invalid_schema(tmp_path):
    path = tmp_path / "bad_schema.json"
    path.write_text('{"version": 1}')
    with pytest.raises(KeyError):
        from_json(path)
