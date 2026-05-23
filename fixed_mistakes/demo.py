"""
Демонстрационный скрипт для улучшенного spell-checker.

Запуск:
    python demo.py
"""

import sys
import asyncio

sys.path.insert(0, ".")

from src.spell_checker import RussianSpellChecker
from data.typos_dataset import get_test_dataset


def main():
    print("Инициализация RussianSpellChecker (CPU + fallback)...")
    checker = RussianSpellChecker(
        use_gpu=False,
        fallback_to_dict=True,
        config_path="config.yaml",
        model_mode="fast",
        auto_update=False,
    )
    print(f"Checker готов: {checker}\n")

    # --- 1. Базовые тесты ---
    test_queries = [
        "тлефон",
        "компютер",
        "мыш",
        "наушнеки",
        "ноутбук",
        "тел3фон",
        "зарядка для телфона",
        "монитор 24 дюйма",
    ]

    print("=" * 60)
    print("1. Базовая коррекция (correct)")
    print("=" * 60)
    for q in test_queries:
        results = checker.correct(q)
        print(f"  Запрос: '{q}' -> {results}")

    # --- 2. Топ-k ---
    print("\n" + "=" * 60)
    print("2. Топ-3 варианта (correct_topk)")
    print("=" * 60)
    for q in ["тлефон", "мыш", "наушнеки"]:
        results = checker.correct_topk(q, k=3)
        print(f"  Запрос: '{q}' -> {results}")

    # --- 3. Пакетная обработка ---
    print("\n" + "=" * 60)
    print("3. Пакетная обработка (correct_batch)")
    print("=" * 60)
    batch_results = checker.correct_batch(test_queries)
    for q, r in zip(test_queries, batch_results):
        print(f"  '{q}' -> {r}")

    # --- 4. Асинхронная пакетная обработка ---
    async def async_demo():
        print("\n" + "=" * 60)
        print("4. Асинхронная пакетная обработка (correct_batch_async)")
        print("=" * 60)
        results = await checker.correct_batch_async(test_queries[:5], k=2)
        for q, r in zip(test_queries[:5], results):
            print(f"  '{q}' -> {r}")

    asyncio.run(async_demo())

    # --- 5. Оценка ---
    print("\n" + "=" * 60)
    print("5. Оценка качества на тестовом наборе")
    print("=" * 60)
    dataset = get_test_dataset()
    report = checker.evaluate(dataset)
    print(report)

    # --- 6. Метрики ---
    print("\n" + "=" * 60)
    print("6. Метрики качества")
    print("=" * 60)
    print(checker.metrics.report())

    # --- 7. Экспорт / импорт словаря ---
    print("\n" + "=" * 60)
    print("7. Экспорт словаря")
    print("=" * 60)
    export_path = "data/exported_dict.txt"
    checker.export_dictionary(export_path)
    print(f"Словарь экспортирован в {export_path}")

    print("\nГотово!")


if __name__ == "__main__":
    main()
