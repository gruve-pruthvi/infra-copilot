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
