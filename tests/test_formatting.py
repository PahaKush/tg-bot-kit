import pytest

from tgkit.utils.formatting import get_progress_bar, mask_endpoint


@pytest.mark.parametrize(
    ("endpoint", "masked"),
    [
        ("203.0.113.45:51820", "203.0.*.*:51820"),
        ("203.0.113.45", "203.0.*.*"),
        ("[2001:db8::1]:51820", "[2001:…]:51820"),
        ("[::ffff:192.0.2.1]:8080", "192.0.*.*:8080"),
        ("vpn.example.com:443", "vpn.…:443"),
        (None, "Не подключен"),
    ],
)
def test_mask_endpoint(endpoint, masked):
    assert mask_endpoint(endpoint) == masked


@pytest.mark.parametrize("percent", [-20, 0, 55, 100, 150])
def test_progress_bar_keeps_its_width(percent):
    assert len(get_progress_bar(percent, width=10)) == 12
