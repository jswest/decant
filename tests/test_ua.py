import re

from decant.ua import build_user_agent


def test_user_agent_format():
    ua = build_user_agent("alice@example.com")
    assert re.fullmatch(
        r"Decant/\d+\.\d+\.\d+ \(\+mailto:alice@example\.com\)", ua
    )
