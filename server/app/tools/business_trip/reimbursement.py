"""Deterministic receipt deduplication and reimbursement report rendering."""

from decimal import Decimal

from app.tools.business_trip.models import BusinessTripInput, TripReceipt


def build_reimbursement_report(requested: BusinessTripInput) -> dict[str, object]:
    unique: list[TripReceipt] = []
    duplicate_entries: list[dict[str, str]] = []
    seen: dict[str, TripReceipt] = {}
    for receipt in requested.receipts:
        key, reason = _deduplication_key(receipt)
        original = seen.get(key)
        if original is not None:
            duplicate_entries.append({
                "receiptId": receipt.id,
                "duplicateOf": original.id,
                "reason": reason,
            })
            continue
        seen[key] = receipt
        unique.append(receipt)

    totals: dict[str, Decimal] = {}
    for receipt in unique:
        totals[receipt.currency] = totals.get(receipt.currency, Decimal("0")) + receipt.amount

    items: list[dict[str, object]] = []
    missing_identifiers: list[str] = []
    outside_trip_dates: list[str] = []
    for receipt in unique:
        value = receipt.model_dump(mode="json", by_alias=True)
        value["amount"] = _money(receipt.amount)
        value["withinTripDates"] = (
            requested.start_date <= receipt.transaction_date <= requested.end_date
        )
        items.append(value)
        if receipt.invoice_number is None and receipt.order_number is None:
            missing_identifiers.append(receipt.id)
        if not value["withinTripDates"]:
            outside_trip_dates.append(receipt.id)

    return {
        "items": items,
        "receiptCount": len(unique),
        "duplicateCount": len(duplicate_entries),
        "duplicates": duplicate_entries,
        "totalsByCurrency": [
            {"currency": currency, "amount": _money(amount)}
            for currency, amount in sorted(totals.items())
        ],
        "missingIdentifierReceiptIds": missing_identifiers,
        "outsideTripDateReceiptIds": outside_trip_dates,
    }


def render_reimbursement_markdown(
    report: dict[str, object], requested: BusinessTripInput
) -> str:
    lines = [f"# {requested.destination}出差报销清单", ""]
    items = report.get("items") if isinstance(report.get("items"), list) else []
    if not items:
        lines.extend(["本次没有用户选择并确认的票据。", ""])
    else:
        lines.extend([
            "| 日期 | 类别 | 商户 | 金额 | 凭证号 | 来源 |",
            "|---|---|---|---:|---|---|",
        ])
        for item in items:
            if not isinstance(item, dict):
                continue
            number = item.get("invoiceNumber") or item.get("orderNumber") or "待补充"
            lines.append(
                "| {date} | {category} | {merchant} | {currency} {amount} | "
                "{number} | {source} |".format(
                    date=item.get("transactionDate", ""),
                    category=_category_text(str(item.get("category") or "other")),
                    merchant=_table_text(item.get("merchant")),
                    currency=item.get("currency", ""),
                    amount=item.get("amount", ""),
                    number=_table_text(number),
                    source=_table_text(item.get("sourceLabel")),
                )
            )
        lines.append("")

    lines.extend(["## 金额汇总", ""])
    totals = report.get("totalsByCurrency")
    if isinstance(totals, list) and totals:
        for total in totals:
            if isinstance(total, dict):
                lines.append(f"- {total.get('currency', '')} {total.get('amount', '')}")
    else:
        lines.append("- 暂无可汇总金额")

    duplicates = report.get("duplicates")
    if isinstance(duplicates, list) and duplicates:
        lines.extend(["", "## 已排除的重复票据", ""])
        for duplicate in duplicates:
            if isinstance(duplicate, dict):
                lines.append(
                    f"- {duplicate.get('receiptId')} 与 {duplicate.get('duplicateOf')} 重复"
                )

    missing = report.get("missingIdentifierReceiptIds")
    outside = report.get("outsideTripDateReceiptIds")
    if (isinstance(missing, list) and missing) or (isinstance(outside, list) and outside):
        lines.extend(["", "## 需要核对", ""])
        if isinstance(missing, list):
            lines.extend(f"- {item} 缺少发票号或订单号" for item in missing)
        if isinstance(outside, list):
            lines.extend(f"- {item} 的日期不在本次出差范围内" for item in outside)
    return "\n".join(lines).strip()


def _deduplication_key(receipt: TripReceipt) -> tuple[str, str]:
    if receipt.invoice_number:
        return "invoice:" + _normalized(receipt.invoice_number), "发票号相同"
    if receipt.order_number:
        return "order:" + _normalized(receipt.order_number), "订单号相同"
    fallback = "|".join([
        _normalized(receipt.merchant),
        receipt.transaction_date.isoformat(),
        receipt.currency,
        _money(receipt.amount),
    ])
    return "fallback:" + fallback, "商户、日期、币种和金额相同"


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), ".2f")


def _table_text(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _category_text(value: str) -> str:
    return {
        "flight": "机票",
        "hotel": "酒店",
        "taxi": "出租车",
        "transport": "交通",
        "meal": "餐饮",
        "other": "其他",
    }.get(value, "其他")
