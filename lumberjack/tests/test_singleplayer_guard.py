"""The singleplayer guard: only a client connected to this PC's singleplayer server may be driven."""
from lumberjack import singleplayer_guard as G

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:50101        127.0.0.1:43595        ESTABLISHED     4242
  TCP    127.0.0.1:47001        127.0.0.1:50200        ESTABLISHED     4242
  TCP    192.168.1.5:50300      52.10.1.2:443          ESTABLISHED     999
  TCP    [::1]:50400            [::1]:43595            ESTABLISHED     4242
"""


def test_a_local_singleplayer_client_passes():
    conns = G.parse_netstat(NETSTAT, 4242)
    assert ("127.0.0.1", 43595) in conns and ("::1", 43595) in conns
    assert G.problem(conns) is None                 # another program's internet use doesn't matter


def test_a_client_on_an_online_server_is_refused():
    online = NETSTAT + "  TCP    192.168.1.5:50500      104.21.3.4:43594       ESTABLISHED     4242\n"
    why = G.problem(G.parse_netstat(online, 4242))
    assert why and "104.21.3.4:43594" in why and "never on an online server" in why


def test_a_client_not_on_the_local_server_is_refused():
    assert "isn't connected" in G.problem([("127.0.0.1", 47001)])
    assert G.problem([]) is not None


def test_it_fails_closed_when_it_cannot_check(monkeypatch):
    monkeypatch.setattr(G.sys, "platform", "linux")
    monkeypatch.setattr(G, "_ok_until", 0.0)
    try:
        G.require()
    except G.NotSingleplayer:
        return
    raise AssertionError("an unverifiable game must not be driven")
