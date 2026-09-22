"""§5.1/§9.3 п.8/§12.2: Money без float, E.164 + IDNA нормалізація контактів, MeasuredValue."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from collector.contracts.enums import ContactKind
from collector.contracts.values import (
    ContactValue,
    MeasuredValue,
    Money,
    normalize_contact,
    normalize_email,
    normalize_phone,
)


def test_money_int_minor_and_iso4217_format() -> None:
    money = Money(amount_minor=259900, currency="UAH")
    assert money.model_dump() == {"amount_minor": 259900, "currency": "UAH"}
    for bad_currency in ("uah", "UA", "UAHH", "978"):
        with pytest.raises(ValidationError):
            Money(amount_minor=1, currency=bad_currency)


def test_money_rejects_float_and_decimal_string() -> None:
    with pytest.raises(ValidationError, match="float"):
        Money(amount_minor=2599.0, currency="UAH")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Money(amount_minor="2599.5", currency="UAH")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Money.model_validate({"amount_minor": 1.5, "currency": "EUR"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("050 123 45 67", "+380501234567"),
        ("+38 (067) 123-45-67", "+380671234567"),
        ("0671234567", "+380671234567"),
        ("+48 22 123 45 67", "+48221234567"),
        ("не телефон", None),
        ("123", None),
    ],
)
def test_phone_to_e164_default_region_ua(raw: str, expected: str | None) -> None:
    assert normalize_phone(raw) == expected
    contact = ContactValue.from_raw(ContactKind.PHONE, raw)
    assert contact.raw == raw  # raw ніколи не втрачається
    assert contact.normalized == expected


def test_phone_default_region_override() -> None:
    assert normalize_phone("22 123 45 67", default_region="PL") == "+48221234567"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ivan.Petrenko@Example.COM", "ivan.petrenko@example.com"),
        ("  user@Пошта.укр ", "user@xn--80a1acn3a.xn--j1amh"),
        ("no-at-sign", None),
        ("two words@example.com", None),
        ("@example.com", None),
    ],
)
def test_email_lowercase_idna(raw: str, expected: str | None) -> None:
    assert normalize_email(raw) == expected
    contact = ContactValue.from_raw(ContactKind.EMAIL, raw)
    assert (contact.raw, contact.normalized) == (raw, expected)


def test_contact_value_validates_normalized_shape() -> None:
    with pytest.raises(ValidationError, match="E.164"):
        ContactValue(kind=ContactKind.PHONE, raw="x", normalized="0501234567")
    with pytest.raises(ValidationError, match="lowercase"):
        ContactValue(kind=ContactKind.EMAIL, raw="x", normalized="A@b.c")
    assert normalize_contact(ContactKind.URL, "  https://t.me/x ") == "https://t.me/x"
    assert normalize_contact(ContactKind.OTHER, "   ") is None
    with pytest.raises(ValidationError):
        ContactValue(kind=ContactKind.PHONE, raw="")


def test_measured_value_keeps_raw() -> None:
    value = MeasuredValue(value=Decimal("1.5"), unit="kW", raw_value="2 к.с.", raw_unit="к.с.")
    assert value.raw_value == "2 к.с."
    assert MeasuredValue.model_validate_json(value.model_dump_json()) == value
