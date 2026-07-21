from app.services import progress


def test_emit_routes_to_registered_session():
    got = []
    progress.register_session("s1", got.append)
    progress.set_inflight("docker", "s1", "call_1")
    progress.emit_progress("docker", "layer abc: downloading")
    assert got == [{"type": "tool_progress", "tool_call_id": "call_1", "text": "layer abc: downloading"}]
    progress.clear_inflight("docker")
    progress.emit_progress("docker", "ignored")
    assert len(got) == 1
    progress.unregister_session("s1")


def test_emit_without_registration_is_noop():
    progress.emit_progress("docker", "nobody listening")  # must not raise
