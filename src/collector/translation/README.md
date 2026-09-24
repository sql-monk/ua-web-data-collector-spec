# Translation core — PR1

Цей модуль реалізує чисте ядро перекладу новин: сегментацію cleaned HTML, визначення мови,
маскування фактів і термінів, translation-memory key та складання результату. У PR1 немає
мережі, БД, credentials або runtime handler-а; вони додаються в PR2 через порти.

## Потік даних

1. `plan_article_translation()` вибирає доступні поля за `content_access` і викликає
   `segment_html()`.
2. Мова визначається в порядку metadata → `lang` → Lingua. Короткий сегмент успадковує
   мову статті. `uk` лишається без перекладу; unsupported-мова дає quality flag.
3. `mask_segment()` замінює inline tags, числа, дати, валюту, URL, email і glossary-терміни
   placeholder-ами.
4. `translation_memory_key()` хешує canonical JSON п'яти компонентів: source language,
   target language, normalized masked segment hash, provider/model і glossary version.
5. `execute_plan()` бере TM hits, передає лише misses у translator-port, перевіряє
   preservation, записує валідні misses у TM та викликає `reassemble()`.

Source HTML і оригінальні значення не змінюються. Неповний або пошкоджений переклад не
маскується як успішний: outcome має `complete=false` і quality flags.

## Основні модулі

| Модуль | Відповідальність |
|---|---|
| `segmenter.py` | HTML → ordered segments; DOM-equivalent reassembly |
| `detection.py` | metadata/`lang`/classifier decision з confidence |
| `planner.py` | поля, action `translate/keep/unsupported`, `not_required` |
| `preservation.py` | masking, restoration, validation фактів і inline tags |
| `glossary/` | versioned per-language terms і do-not-translate entries |
| `memory.py` | deterministic TM key і storage port |
| `pipeline.py` | TM hit/miss orchestration та `TranslationOutcome` |

## Інваріанти розширення

- Новий provider не змінює segmenter/TM: його `name` і `model_version` входять у ключ.
- Зміна glossary content створює нову версію й не перезаписує старі TM entries.
- Нове джерельне language code треба додати до конфігурації extra languages і до golden
  corpus PR3; мовчазно мапити його на «схожу» підтримувану мову заборонено.
- Новий placeholder має відновлюватися побайтово/семантично й отримати mutation-test.
- Pure-модулі не імпортують HTTP/DB/provider SDK. I/O реалізується адаптерами портів PR2.

## Перевірка

```text
uv run pytest -q -rs tests/unit/translation
uv run mypy src/collector/translation
uv run ruff check src/collector/translation tests/unit/translation
```

Offline QA corpus і human-QA rubric з'являться у PR3 та будуть описані в
`docs/translation-qa.md`.
