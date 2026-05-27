from decant import __version__


def build_user_agent(contact_email: str) -> str:
    return f"Decant/{__version__} (+mailto:{contact_email})"
