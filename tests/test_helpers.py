import ast
import re
from pathlib import Path

SOURCE = Path("app.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

def load_helpers():
    names = {"srt_stamp", "split_subtitle_text", "make_srt", "validate_srt"}
    nodes = [node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app_helpers", "exec"), namespace)
    return namespace

def test_app_parses():
    ast.parse(SOURCE)

def test_srt_draft_is_valid_and_has_newlines():
    helpers = load_helpers()
    content = helpers["make_srt"]("မင်္ဂလာပါ။\nဒီမှာ ဇာတ်လမ်းပါ။", 20)
    assert "\n" in content
    assert "-->" in content
    validated = helpers["validate_srt"](content)
    assert validated.startswith("1\n")

def test_rejects_invalid_srt():
    helpers = load_helpers()
    try:
        helpers["validate_srt"]("not a subtitle")
    except ValueError:
        return
    raise AssertionError("Invalid SRT should be rejected")

def test_timestamp_format():
    helpers = load_helpers()
    assert helpers["srt_stamp"](1.25) == "00:00:01,250"
