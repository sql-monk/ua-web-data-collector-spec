# Dependency-запит: WP-04 PR1 → WP-01C (`TranslationQualityFlag`)

| Поле | Значення |
|---|---|
| Від | WP-04 PR1 `wp/04-1-segmenter-tm` (gate 3, знахідка R-1) |
| Кому | WP-01C — owner `src/collector/contracts/**` (PR2 `wp/01c-2-payload-news-contracts`, п.4) |
| Дата | 2026-09-24 |
| Блокує | WP-04 PR2 (перехід із локального `QualityFlag` на контракт); PR1 не блокує |

## Що потрібно

Додати до закритого enum `TranslationQualityFlag` два значення (minor-розширення, як
передбачає картка WP-01C PR2 п.4):

1. `language_unsupported` — уже в польоті в WP-01C PR2 (рішення О-5); тут лише фіксуємо, що
   WP-04 використовує саме цю назву.
2. **`untranslated_content`** — нове. Версія зібрана, але частина видимого тексту статті
   свідомо не перекладена: незакритий захищений елемент (`code`/`pre`/`translate="no"`)
   тягнеться до кінця документа, тож, як і в DOM браузера, весь текст після нього — вміст
   цього елемента.

## Навіщо

Gate 3 (R-1) вимагає, щоб неперекладений обсяг ніколи не давав `complete` без прапорця.
Після виправлення segmenter-а захищена область закривається на end-tag будь-якого
відкритого предка, тож лишається тільки випадок «до кінця документа». Його неможливо
відрізнити від легітимного коду, а перекладати вміст `code` не можна (картка PR1, п.1).
Тому версія записується з прапорцем, а не мовчки і не як `translation_failed`.

Поточний стан у WP-04: `QualityFlag` у `collector/translation/planner.py` — локальний
`Literal`, позначений TODO на WP-01C PR2. Значення `preservation_failed`,
`low_language_confidence`, `provider_truncated`, `language_unsupported` збігаються з
контрактом, а `untranslated_content` чекає на цей запит.

## Альтернатива, якщо значення відхилено

Мапити цей випадок на `low_language_confidence` семантично неправильно, тому WP-04 PR2
натомість не збиратиме версію (`translation_failed` + retry plan «виправити cleaned HTML у
WP-05»). Рішення — за WP-01C/оркестратором.
