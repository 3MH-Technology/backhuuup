import re


def sanitize_bot_name(name: str) -> str:
    safe = re.sub(r'[^\w\-\. ]', '', name)
    return safe.strip() or "unnamed_bot"


def validate_bot_name(name: str) -> bool:
    return 2 <= len(name.strip()) <= 100
