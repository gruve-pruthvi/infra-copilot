"""Unit tests for compose helpers — no Docker daemon needed."""
import docker_tools.server as server
from docker_tools.server import _compose_base, _run_compose, compose_ps


def test_compose_base_builds_command(tmp_path):
    cmd = _compose_base(str(tmp_path), None)
    assert cmd == ["docker", "compose", "--project-directory", str(tmp_path)]


def test_compose_base_with_file(tmp_path):
    f = tmp_path / "compose.yaml"
    f.write_text("services: {}\n")
    cmd = _compose_base(str(tmp_path), str(f))
    assert cmd[-2:] == ["-f", str(f)]


def test_compose_base_rejects_missing_dir():
    res = _compose_base("/nonexistent/path/xyz", None)
    assert res["error"] is True


def test_run_compose_missing_binary(monkeypatch):
    res = _run_compose(["definitely-not-a-binary-xyz", "compose"])
    assert res["error"] is True


def test_compose_ps_parses_json_array_output(tmp_path, monkeypatch):
    """Some docker compose versions emit one JSON array instead of NDJSON."""

    def fake_run_compose(cmd, timeout=570.0):
        return {"exit_code": 0, "output": '[{"Name": "web"}]', "stderr": ""}

    monkeypatch.setattr(server, "_run_compose", fake_run_compose)
    res = compose_ps(str(tmp_path))
    assert "error" not in res
    assert res["services"] == [{"Name": "web"}]
