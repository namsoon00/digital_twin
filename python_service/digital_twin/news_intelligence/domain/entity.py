from typing import Dict, Iterable, List


COMPANY_ALIASES: Dict[str, List[str]] = {
    "005930": ["삼성전자", "Samsung Electronics", "Samsung"],
    "000660": ["SK하이닉스", "SK Hynix", "Hynix"],
    "035420": ["NAVER", "네이버"],
    "005380": ["현대차", "현대자동차", "Hyundai Motor"],
    "035720": ["카카오", "Kakao"],
    "066570": ["LG전자", "LG Electronics"],
    "000020": ["동화약품"],
    "AAPL": ["Apple", "Apple Inc.", "애플"],
    "NVDA": ["NVIDIA", "Nvidia", "엔비디아"],
    "TSLA": ["Tesla", "테슬라"],
    "PLTR": ["Palantir"],
    "MSTR": ["MicroStrategy", "Strategy"],
    "STRC": ["Strategy"],
}


def unique_aliases(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def target_aliases(symbol: object, name: object = "", extra: Iterable[object] = None) -> List[str]:
    normalized = str(symbol or "").upper().strip()
    return unique_aliases([name, normalized, *COMPANY_ALIASES.get(normalized, []), *(extra or [])])


def other_company_aliases(symbol: object) -> List[str]:
    normalized = str(symbol or "").upper().strip()
    aliases: List[str] = []
    for candidate, values in COMPANY_ALIASES.items():
        if candidate != normalized:
            aliases.extend(values)
    return unique_aliases(aliases)
