from .market_data import number


def money(value: float, currency: str = "KRW") -> str:
    amount = number(value)
    code = str(currency or "KRW").upper()
    if amount <= 0:
        return "-"
    if code == "USD":
        if amount >= 1000:
            return "$" + format(round(amount), ",")
        return "$" + format(round(amount, 2), ",").rstrip("0").rstrip(".")
    if code != "KRW":
        if amount >= 1000:
            return format(round(amount), ",") + " " + code
        return format(round(amount, 2), ",").rstrip("0").rstrip(".") + " " + code
    if amount >= 100000000:
        return str(round(amount / 100000000)) + "억 원"
    if amount >= 10000:
        return format(round(amount / 10000), ",") + "만 원"
    return format(round(amount), ",") + "원"


def signed_pct(value: float, suffix: str = "%") -> str:
    rounded = round(float(value or 0), 1)
    return ("+" if rounded > 0 else "") + str(rounded) + suffix


def pct_delta(current: float, previous: float) -> float:
    base = float(previous or 0)
    if not base:
        return 0.0
    return ((float(current or 0) / base) - 1) * 100


def compact_number(value: float) -> str:
    amount = number(value)
    if not amount:
        return "-"
    rounded = round(amount, 1)
    if rounded == round(rounded):
        return format(round(rounded), ",")
    return format(rounded, ",")


def signed_number(value: float) -> str:
    amount = number(value)
    if not amount:
        return "-"
    prefix = "+" if amount > 0 else ""
    return prefix + compact_number(amount)
