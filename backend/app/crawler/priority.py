"""
Координация приоритетов между фоновой индексацией и живыми поисками.

Живые поиски имеют приоритет над фоновым краулером.
"""

import asyncio

_active_searches: int = 0


def search_started() -> None:
    global _active_searches
    _active_searches += 1


def search_ended() -> None:
    global _active_searches
    _active_searches = max(0, _active_searches - 1)


async def yield_to_live_search(max_wait: float = 40.0) -> None:
    """
    Фоновые задачи вызывают эту функцию перед краулингом.
    Если идёт живой поиск — ждём до max_wait секунд.
    """
    if _active_searches == 0:
        return
    waited = 0.0
    step = 0.5
    while _active_searches > 0 and waited < max_wait:
        await asyncio.sleep(step)
        waited += step
