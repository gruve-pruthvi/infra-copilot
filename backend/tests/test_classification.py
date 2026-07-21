from app.config import settings
from app.tools.classification import Tier, classify, timeout_for


def test_unknown_tool_fails_closed():
    assert classify("docker", "definitely_not_a_tool") is Tier.DESTRUCTIVE


def test_timeout_default_matches_settings():
    assert timeout_for("docker", "list_containers") == settings.tool_timeout_seconds


def test_timeout_override_for_pull_image():
    assert timeout_for("docker", "pull_image") == 600.0


def test_compose_tools_classified():
    assert classify("docker", "compose_ps") is Tier.READ
    assert classify("docker", "compose_logs") is Tier.READ
    assert classify("docker", "compose_up") is Tier.MUTATING
    assert classify("docker", "compose_down") is Tier.DESTRUCTIVE


def test_exec_in_container_is_destructive():
    assert classify("docker", "exec_in_container") is Tier.DESTRUCTIVE


def test_check_connectivity_is_read():
    assert classify("monitoring", "check_connectivity") is Tier.READ
