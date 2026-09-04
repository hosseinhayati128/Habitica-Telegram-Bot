import socket

import pytest


TEST_HABITICA_CLIENT_ID = "12345678-90ab-416b-cdef-1234567890ab-hhabitica-tests"


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Fail every test that attempts an unmocked outbound connection."""

    monkeypatch.setenv("HABITICA_CLIENT_ID", TEST_HABITICA_CLIENT_ID)

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("unexpected real network connection")

    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    monkeypatch.setattr(socket.socket, "connect", unexpected_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected_connection)
