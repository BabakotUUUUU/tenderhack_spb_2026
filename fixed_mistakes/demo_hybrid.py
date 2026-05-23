"""
Демонстрация гибридного spell-checker (PriceHunt + BART).

Запуск:
    python demo_hybrid.py
"""

import sys
import asyncio

sys.path.insert(0, ".")

from src.hybrid_spell_checker import HybridSpellChecker
from data.typos_dataset import get_test_dataset


def main():
    print("Инициализация HybridSpellChecker (PriceHunt + BART fallback)...")
    checker = HybridSpellChecker(
        use_gpu=False,
        config_path="config_hybrid.yaml",
        model_mode="fast",
        auto_update=False,
    )
    print(f"Checker готов: {checker}\n")

    # --- PriceHunt fast-path демо ---
    pricehunt_queries = [
        "ноутбукк",
        "нотбук",
        "205\\55R16",
        "веб камера",
        "беспроводная мышь",
        "ghbdtn",  # привет (не из known roots, не исправится)
    ]
    print("=" * 60)
    print("1. PriceHunt fast-path (< 5 мс)")
    print("=" * 60)
    for q in pricehunt_queries:
        result = checker.correct(q)
        print(f"  '{q}' -> {result}")

    # --- BART algorithmic fallback ---
    bart_queries = [
        "тлефон",
        "компютер",
        "наушнеки",
        "тел3фон",
        "зарядка для телфона",
    ]
    print("\n" + "=" * 60)
    print("2. BART algorithmic fallback (n-gram + phonetic + weighted lev)")
    print("=" * 60)
    for q in bart_queries:
        result = checker.correct(q)
        print(f"  '{q}' -> {result}")

    # --- Synonym expansion ---
    print("\n" + "=" * 60)
    print("3. Synonym expansion (PriceHunt SYNONYM_MAP)")
    print("=" * 60)
    for q in ["ноутбук", "летние шины", "футболка"]:
        variants = checker.expand_synonyms(q)
        print(f"  '{q}' -> variants: {variants}")

    # --- process_query (полный pipeline) ---
    print("\n" + "=" * 60)
    print("4. process_query (correct + synonyms + category)")
    print("=" * 60)
    for q in ["нотбук", "летняя резина", "кросовки"]:
        processed = checker.process_query(q)
        print(f"  '{q}' ->")
        for k, v in processed.items():
            print(f"    {k}: {v}")

    # --- Batch ---
    print("\n" + "=" * 60)
    print("5. Batch / async")
    print("=" * 60)
    batch = checker.correct_batch(["тлефон", "компютер", "мыш"])
    print(f"  sync batch: {batch}")

    async def async_demo():
        batch_async = await checker.correct_batch_async(["наушнеки", "ноутбук"], k=2)
        print(f"  async batch: {batch_async}")

    asyncio.run(async_demo())

    # --- Evaluation ---
    print("\n" + "=" * 60)
    print("6. Evaluation")
    print("=" * 60)
    dataset = get_test_dataset()
    report = checker.evaluate(dataset)
    print(report)

    # --- Metrics ---
    print("\n" + "=" * 60)
    print("7. Metrics")
    print("=" * 60)
    print(checker.metrics.report())

    checker.export_dictionary("data/hybrid_dict.txt")
    print("\nГотово!")


if __name__ == "__main__":
    main()
