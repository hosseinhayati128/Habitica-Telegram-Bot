import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Fail every test that attempts an unmocked outbound connection."""

    def unexpected_connection(*_args, **_kwargs):
        raise AssertionError("unexpected real network connection")

    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    monkeypatch.setattr(socket.socket, "connect", unexpected_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected_connection)
