import re
from files.system_setup.system_logger import Logger

SPAM_PATTERN = re.compile(
    r"([A-Z0-9]| ){20,}|([A-Z0-9a-z]){15,}|(.)\3{5,}|(.*? )\4{2,}"
)

def is_spam_message(text: str) -> bool:
    result = SPAM_PATTERN.search(text or "") is not None
    if result:
        Logger.warn('Spam filter flagged "', (text or "").strip(), '"')
    return result