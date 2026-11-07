"""Per-endpoint HTTP connections pinned to policy-validated addresses."""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from typing import Any

from .security import EndpointConfiguration


def pinned_urlopen(endpoint: EndpointConfiguration):
    """Keep the URL hostname for Host/TLS, using only approved numeric peers."""

    class HTTPConnection(http.client.HTTPConnection):
        def connect(self):
            failure = None
            for address in endpoint.addresses:
                try:
                    self.sock = socket.create_connection((address, self.port), self.timeout, self.source_address)
                    return
                except OSError as exc:
                    failure = exc
            raise OSError("no validated endpoint address could be reached") from failure

    class HTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            HTTPConnection.connect(self)
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)

    class HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(HTTPConnection, req)

    class HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(HTTPSConnection, req, context=self._context)

    class RejectRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed", headers, fp)

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), HTTPHandler(), HTTPSHandler(), RejectRedirects()
    )

    def open_request(request, timeout=600):
        from urllib.parse import urlsplit

        if urlsplit(request.full_url).netloc != urlsplit(endpoint.base_url).netloc:
            raise ValueError("request authority differs from the validated endpoint")
        return opener.open(request, timeout=timeout)

    return open_request


def pinned_openai_client(endpoint: EndpointConfiguration) -> Any:
    """Inject a private transport through upstream GEPA's existing LM kwargs."""
    import httpx
    from openai import OpenAI

    open_request = pinned_urlopen(endpoint)

    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            req = urllib.request.Request(
                str(request.url), data=request.read(), headers=dict(request.headers), method=request.method
            )
            timeout = request.extensions.get("timeout", {}).get("read") or 600
            try:
                response = open_request(req, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return httpx.Response(response.code, headers=list(response.headers.items()), content=response.read())

    return OpenAI(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        http_client=httpx.Client(transport=Transport(), follow_redirects=False, trust_env=False),
    )
