from k8s_tools.server import _parse_manifest


def test_parse_multi_doc():
    docs = _parse_manifest("""
apiVersion: v1
kind: ConfigMap
metadata:
  name: a
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: b
""")
    assert [d["kind"] for d in docs] == ["ConfigMap", "Deployment"]


def test_parse_rejects_doc_without_name():
    res = _parse_manifest("apiVersion: v1\nkind: ConfigMap\nmetadata: {}\n")
    assert res["error"] is True


def test_parse_rejects_invalid_yaml():
    res = _parse_manifest("{{{{not yaml")
    assert res["error"] is True


def test_parse_rejects_non_dict_metadata():
    """Regression: metadata must be a dict, not a scalar string."""
    res = _parse_manifest("apiVersion: v1\nkind: ConfigMap\nmetadata: just-a-string\n")
    assert res["error"] is True
    assert res["type"] == "InvalidManifest"


def test_parse_rejects_top_level_scalar():
    """Regression: top-level YAML scalar must return error dict, not raise."""
    res = _parse_manifest("just a string")
    assert res["error"] is True
    assert res["type"] == "InvalidManifest"


def test_parse_rejects_top_level_list():
    """Regression: top-level YAML list must return error dict, not raise."""
    res = _parse_manifest("[1, 2, 3]")
    assert res["error"] is True
    assert res["type"] == "InvalidManifest"
