"""Plain-Python specification of drop_below, for differential testing (validation rule 4),
written from the question alone: the count kept and the kept readings in order.
"""


def drop_below(readings: list[int], limit: int) -> tuple[int, list[int]]:
    kept = [r for r in readings if r >= limit]
    return len(kept), kept
