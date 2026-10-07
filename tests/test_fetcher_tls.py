"""HTTPS with the connection pinned to a validated IP, against a real local TLS server and a real certificate.
The mocks in test_fetcher.py cannot show that certificate checking still uses the *hostname*: this does."""
import http.server
import shutil
import ssl
import subprocess
import threading

import pytest

from product_extractor.errors import FetchFailed
from product_extractor.fetcher import Fetcher

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl CLI not installed")


@pytest.fixture
def tls_site(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2",
                    "-subj", "/CN=shop.example.test", "-addext", "subjectAltName=DNS:shop.example.test"], check=True, capture_output=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                self.send_response(404); self.end_headers(); return
            body = f"<h1>host={self.headers['Host']}</h1>".encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client_ctx = ssl.create_default_context(cafile=str(cert))       # trusts only this certificate
    yield server.server_address[1], client_ctx
    server.shutdown()


def fetcher(settings, ctx, mapping):
    async def resolve(host, port):
        return mapping.get(host, ["93.184.216.34"])
    return Fetcher(settings.model_copy(update={"allow_private_networks": True}), resolver=resolve, verify=ctx)


async def test_https_connects_to_the_pinned_ip_but_verifies_and_sends_the_real_hostname(settings, tls_site):
    port, ctx = tls_site
    f = fetcher(settings, ctx, {"shop.example.test": ["127.0.0.1"]})
    res = await f.fetch(f"https://shop.example.test:{port}/p/1")
    assert res.status == 200
    assert res.html == f"<h1>host=shop.example.test:{port}</h1>"      # Host header carries the name, not 127.0.0.1


async def test_a_certificate_for_another_hostname_is_rejected_even_though_the_ip_is_the_same(settings, tls_site):
    port, ctx = tls_site
    f = fetcher(settings, ctx, {"evil.example.test": ["127.0.0.1"]})   # same server, but its cert is not valid for this name
    with pytest.raises(FetchFailed, match="ConnectError"):
        await f.fetch(f"https://evil.example.test:{port}/p/1")


async def test_an_untrusted_certificate_is_rejected_by_default(settings, tls_site):
    port, _ = tls_site
    f = Fetcher(settings.model_copy(update={"allow_private_networks": True}),
                resolver=lambda h, p: _async(["127.0.0.1"]))           # default verification: the self-signed cert is not trusted
    with pytest.raises(FetchFailed):
        await f.fetch(f"https://shop.example.test:{port}/p/1")


async def _async(value):
    return value
