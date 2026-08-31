from collections.abc import MutableSequence
from typing import TypeVar

T = TypeVar("T")
VALID_POLICIES = {"latest", "latest-buffered", "all"}

def normalize_policy(policy: str | None) -> str:
    value = str(policy or "").strip().lower()
    return value if value in VALID_POLICIES else "latest-buffered"

def select_next(
    standard: MutableSequence[T],
    priority: MutableSequence[T],
    *,
    policy: str,
    keep_last: int = 4,
) -> T | None:
    if priority:
        return priority.pop(0)
    if not standard:
        return None

    normalized = normalize_policy(policy)
    if normalized == "all":
        return standard.pop(0)
    if normalized == "latest":
        item = standard[-1]
        standard.clear()
        return item

    item = standard.pop()
    keep_last = max(0, int(keep_last))
    while len(standard) > keep_last:
        standard.pop(0)
    return item
