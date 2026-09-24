# ADR-0009: сегментний translation memory

- **Статус:** accepted
- **Дата:** 2026-09-24
- **Owner:** WP-04
- **Рішення:** Q-009

## Контекст

Новинна стаття змінюється версіями, але часто редагується лише один абзац. Повторний переклад
усього body збільшує витрати, затримку і ризик непотрібної зміни вже перевірених фрагментів.
Водночас ключ лише від source-тексту небезпечний: результат залежить від мовної пари,
provider/model і glossary.

## Рішення

Translation memory працює на рівні сегмента. Ключ — SHA-256 canonical JSON рівно п'яти
компонентів:

1. source language;
2. target language;
3. SHA-256 нормалізованого masked segment;
4. provider + model version як одна складена компонента;
5. glossary version.

Нормалізація — NFC, згортання пробілів і дозволених control characters. Inline tags, числа,
URL та glossary-терміни маскуються до хешування, але кожен TM hit повторно проходить
preservation validation після підстановки оригінальних значень. Записи immutable і
first-write-wins. Нова article version надсилає provider-у лише misses, а повна version
збирається з hits + нових перекладів.

## Наслідки

- Незмінні сегменти не оплачуються повторно й лишаються відтворюваними.
- Зміна model або glossary природно інвалідує відповідні hits без delete/update.
- Різні числа/URL можуть ділити masked translation; safety забезпечує відновлення саме
  source placeholders і повторна validation.
- Зміна алгоритму normalization/masking потребує явного versioning або міграційного рішення,
  інакше старі та нові ключі співіснуватимуть як cache misses.
- PostgreSQL adapter і retention політика TM належать WP-04 PR2/WP-01A PR3b.

## Відхилені альтернативи

- **TM на рівні article:** простіше, але одна правка спричиняє повний повторний переклад.
- **Ключ без provider/model/glossary:** змішує семантично різні результати й робить replay
  невідтворюваним.
- **In-place update TM:** втрачає походження попереднього результату і створює гонки.
