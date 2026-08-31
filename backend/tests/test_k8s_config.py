"""ConfigMap / Secret tool behaviour.

The cluster-touching paths degrade gracefully (covered by the unavailable
tests); the security-critical part — that Secret *values* never leave the
tool boundary — is tested directly against the pure projection helpers.
"""

from __future__ import annotations

import base64

import pytest

from k8s_tools import server as k8s
from app.tools.classification import Tier, classify


# --- classification contract -------------------------------------------------

def test_configmap_and_secret_tools_are_classified():
    assert classify("k8s", "list_configmaps") is Tier.READ
    assert classify("k8s", "get_configmap") is Tier.READ
    assert classify("k8s", "apply_configmap") is Tier.MUTATING
    assert classify("k8s", "delete_configmap") is Tier.DESTRUCTIVE
    # Secrets are destructive on every write path.
    assert classify("k8s", "list_secrets") is Tier.READ
    assert classify("k8s", "apply_secret") is Tier.DESTRUCTIVE
    assert classify("k8s", "delete_secret") is Tier.DESTRUCTIVE


# --- the security property: secret values never escape -----------------------

def test_secret_projection_returns_key_names_only():
    """_secret_summary must expose keys + sizes, never values."""
    raw = {"password": base64.b64encode(b"hunter2").decode(),
           "token": base64.b64encode(b"abc123").decode()}
    out = k8s._secret_summary("db-creds", "default", raw, secret_type="Opaque")

    assert out["name"] == "db-creds"
    assert sorted(out["keys"]) == ["password", "token"]
    blob = repr(out)
    assert "hunter2" not in blob
    assert "abc123" not in blob
    # not even the base64 form may leak
    assert raw["password"] not in blob
    assert raw["token"] not in blob


def test_secret_projection_handles_empty_and_none_data():
    assert k8s._secret_summary("s", "default", None, secret_type="Opaque")["keys"] == []
    assert k8s._secret_summary("s", "default", {}, secret_type="Opaque")["keys"] == []


def test_configmap_projection_keeps_values():
    """ConfigMaps are not secret — values are useful and allowed through."""
    out = k8s._configmap_summary("cfg", "default", {"LOG_LEVEL": "debug"})
    assert out["data"]["LOG_LEVEL"] == "debug"


# --- graceful degradation ----------------------------------------------------

@pytest.mark.parametrize(
    "call",
    [
        lambda: k8s.list_configmaps(namespace="default"),
        lambda: k8s.get_configmap("cfg", namespace="default"),
        lambda: k8s.apply_configmap("cfg", {"A": "b"}, namespace="default", dry_run=True),
        lambda: k8s.delete_configmap("cfg", namespace="default", dry_run=True),
        lambda: k8s.list_secrets(namespace="default"),
        lambda: k8s.apply_secret("s", {"password": "x"}, namespace="default", dry_run=True),
        lambda: k8s.delete_secret("s", namespace="default", dry_run=True),
    ],
)
def test_tools_never_raise(monkeypatch, call):
    """With no reachable cluster every tool returns a dict, never an exception."""
    monkeypatch.setattr(k8s, "_load", lambda: {"available": False, "reason": "no kubeconfig"})
    res = call()
    assert isinstance(res, dict)
    assert res["available"] is False


def test_apply_secret_rejects_non_string_values(monkeypatch):
    """string_data must be flat str->str; anything else is a caller error, not a 500."""
    monkeypatch.setattr(k8s, "_load", lambda: (object(), object()))
    res = k8s.apply_secret("s", {"password": {"nested": 1}}, namespace="default")  # type: ignore[arg-type]
    assert res["error"] is True
    assert res["type"] == "InvalidSecretData"
