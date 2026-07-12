import socket
import threading

from monitoring_tools.server import check_connectivity


def test_reachable_port():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    t = threading.Thread(target=lambda: srv.accept(), daemon=True)
    t.start()
    res = check_connectivity(host="127.0.0.1", port=port, timeout_seconds=2.0)
    srv.close()
    assert res["reachable"] is True
    assert res["latency_ms"] >= 0


def test_unreachable_port():
    res = check_connectivity(host="127.0.0.1", port=1, timeout_seconds=0.5)
    assert res["reachable"] is False
    assert "reason" in res
