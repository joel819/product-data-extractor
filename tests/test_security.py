import ipaddress

import pytest

from product_extractor.errors import BlockedAddress, InvalidUrl
from product_extractor.security import is_public_ip, validate_url

REJECTED_SYNTAX = [
    "", "   ", "file:///etc/passwd", "ftp://example.com/file", "gopher://example.com/", "javascript:alert(1)",
    "data:text/html,<h1>x</h1>", "http://user:secret@example.com/", "http:///no-host", "http://exa mple.com/",
    "http://example.com/\nHost: evil", "not a url", "//example.com/path", "http://example.com:notaport/",
    "http://example.com:99999/", "http://" + "a" * 3000 + ".com/",
]


@pytest.mark.parametrize("url", REJECTED_SYNTAX)
async def test_bad_schemes_and_malformed_urls_are_rejected(url, public_resolver):
    with pytest.raises(InvalidUrl):
        await validate_url(url, resolver=public_resolver)


BLOCKED_LITERALS = [
    "http://127.0.0.1/", "http://127.0.0.1:8000/admin", "http://[::1]/", "http://0.0.0.0/",
    "http://10.0.0.5/", "http://172.16.0.1/", "http://172.31.255.255/", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://224.0.0.1/",
    "http://[fe80::1]/", "http://[fc00::1]/", "http://[fd12:3456::1]/", "http://[::]/",
    "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/", "http://[::ffff:10.0.0.1]/",
    "http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/", "http://[64:ff9b:1::1]/",
    "https://198.18.0.1/", "http://192.0.2.1/",
]


@pytest.mark.parametrize("url", BLOCKED_LITERALS)
async def test_private_and_reserved_addresses_are_blocked(url, public_resolver):
    with pytest.raises(BlockedAddress):
        await validate_url(url, resolver=public_resolver)


@pytest.mark.parametrize("url", ["http://localhost/", "http://localhost:8080/", "http://LOCALHOST./", "http://127.1/",
                                 "http://2130706433/", "http://0x7f.0.0.1/", "http://0177.0.0.1/"])
async def test_names_and_odd_number_forms_that_resolve_to_loopback_are_blocked(url):
    """These go through the real system resolver (they resolve offline). Whatever the platform does with the odd
    numeric forms, the URL must be refused, never fetched."""
    with pytest.raises((BlockedAddress, InvalidUrl)):
        await validate_url(url)


async def test_localhost_by_name_is_blocked_by_address_not_by_spelling():
    with pytest.raises(BlockedAddress):
        await validate_url("http://localhost/")


async def test_a_public_looking_name_that_resolves_to_a_private_address_is_blocked(public_resolver):
    public_resolver.mapping["innocent.example.test"] = ["10.0.0.7"]
    with pytest.raises(BlockedAddress) as exc:
        await validate_url("https://innocent.example.test/p/1", resolver=public_resolver)
    assert "10.0.0.7" in exc.value.message


async def test_every_resolved_address_must_be_public(public_resolver):
    """A name with one public and one private record is refused: either could be used to connect."""
    public_resolver.mapping["mixed.example.test"] = ["93.184.216.34", "127.0.0.1"]
    with pytest.raises(BlockedAddress):
        await validate_url("https://mixed.example.test/", resolver=public_resolver)


async def test_dns_failure_is_an_invalid_url_not_a_crash():
    async def failing(host, port):
        raise InvalidUrl("Could not resolve host")
    with pytest.raises(InvalidUrl):
        await validate_url("https://nope.example.test/", resolver=failing)


async def test_public_urls_pass_and_report_where_to_connect(public_resolver):
    public_resolver.mapping["v6.example.test"] = ["2606:4700:4700::1111"]
    t = await validate_url("https://Shop.Example.test:8443/p/1?x=1", resolver=public_resolver)
    assert (t.scheme, t.host, t.port, t.ip, t.host_header) == ("https", "shop.example.test", 8443, "93.184.216.34",
                                                               "shop.example.test:8443")
    plain = await validate_url("http://shop.example.test/", resolver=public_resolver)
    assert plain.port == 80 and plain.host_header == "shop.example.test"
    v6 = await validate_url("https://v6.example.test/", resolver=public_resolver)
    assert v6.ip == "2606:4700:4700::1111"


async def test_allow_private_is_an_explicit_opt_in(public_resolver):
    t = await validate_url("http://127.0.0.1:9000/p", allow_private=True, resolver=public_resolver)
    assert t.ip == "127.0.0.1" and t.port == 9000


@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("93.184.216.34", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("169.254.1.1", False), ("100.100.0.1", False),
    ("::1", False), ("fe80::1", False), ("::ffff:8.8.8.8", True), ("::ffff:192.168.0.1", False),
])
def test_is_public_ip(ip, public):
    assert is_public_ip(ipaddress.ip_address(ip)) is public
