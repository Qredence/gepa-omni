from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from unittest.mock import patch

import pytest

_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _public_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback:
            return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)
    except ValueError:
        if host == "localhost":
            return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]


@pytest.fixture(autouse=True)
def _mock_public_endpoint_dns() -> Iterator[None]:
    with patch("gepa_omni.security.socket.getaddrinfo", side_effect=_public_getaddrinfo):
        yield
