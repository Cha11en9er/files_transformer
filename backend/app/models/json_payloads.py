"""Documented JSONB payload shapes. Not ORM tables — used by Pydantic later."""

from __future__ import annotations

from typing import Any, TypedDict


class HeaderFields(TypedDict, total=False):
    buyer: str
    seller: str
    contract_no: str
    incoterms: str
    date: str
    invoice_no: str
    consignee: str
    shipper: str
    subkits: list[str]


class CommercialData(TypedDict, total=False):
    qty: float | None
    price: float | None
    amount: float | None
    unit: str | None
    currency: str | None
    invoice_subkit: str | None
    color: str | None


class PackingData(TypedDict, total=False):
    rolls: float | None
    boxes: float | None
    net_weight: float | None
    gross_weight: float | None
    volume: float | None
    meters: float | None
    area: float | None
    is_aggregated: bool
    distributed_from_article: str | None


class CustomsData(TypedDict, total=False):
    hs_code: str | None
    tnved_code: str | None
    description_ru: str | None
    description_en: str | None
    country: str | None
    manufacturer: str | None
    brand: str | None


class SourceTraces(TypedDict, total=False):
    invoice: dict[str, Any]
    packing_list: dict[str, Any]
    specification: dict[str, Any]
    catalog: dict[str, Any]
    permit: dict[str, Any]
