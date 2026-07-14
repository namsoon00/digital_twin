from dataclasses import dataclass, field
from typing import Dict, List

from .portfolio import Position


DEFAULT_INSTRUMENT_PROFILES_TEXT = """# symbol|label|archetypes|positionIntent|sensitivities|policies
# policies: allowAddOnStrength=1, trimOnTrendBreak=1, avoidAveragingDown=1
MSTR|비트코인 프록시 성장주|BitcoinProxy,HighVolatilityGrowth|trading|btc:high,rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
STRC|비트코인 민감 우선주/배당|PreferredIncome,BitcoinSensitiveIncome|income|btc:medium,rate:high,fx:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
000660|HBM 반도체 성장주|SemiconductorHBM,CyclicalGrowth|core|cycle:high,ai:high,fx:medium|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
005930|대형 반도체 우량주|MegaCapQuality,SemiconductorCyclical|core|cycle:medium,fx:medium|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
AAPL|대형 플랫폼 우량 성장주|MegaCapQuality,PlatformGrowth|core|rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
NVDA|AI 인프라 성장주|AIGrowth,SemiconductorHBM|growth|ai:high,rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
TSLA|고변동 성장주|HighVolatilityGrowth,CyclicalGrowth|trading|rate:high,cycle:high,fx:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
BTC|크립토 기준 자산|CryptoAssetProfile,HighVolatilityGrowth|market-signal|btc:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
ETH|크립토 성장 자산|CryptoAssetProfile,HighVolatilityGrowth|market-signal|crypto:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
"""


BTC_SENSITIVE_SYMBOLS = {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    label: str
    archetypes: List[str] = field(default_factory=list)
    position_intent: str = "core"
    sensitivities: Dict[str, str] = field(default_factory=dict)
    policies: Dict[str, object] = field(default_factory=dict)
    source: str = "inferred"

    @property
    def allow_add_on_strength(self) -> bool:
        return bool(self.policies.get("allowAddOnStrength"))

    @property
    def trim_on_trend_break(self) -> bool:
        return bool(self.policies.get("trimOnTrendBreak"))

    @property
    def avoid_averaging_down(self) -> bool:
        return bool(self.policies.get("avoidAveragingDown"))

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "label": self.label,
            "archetypes": list(self.archetypes),
            "positionIntent": self.position_intent,
            "sensitivities": dict(self.sensitivities),
            "policies": dict(self.policies),
            "allowAddOnStrength": self.allow_add_on_strength,
            "trimOnTrendBreak": self.trim_on_trend_break,
            "avoidAveragingDown": self.avoid_averaging_down,
            "source": self.source,
        }


def default_instrument_profiles_text() -> str:
    return DEFAULT_INSTRUMENT_PROFILES_TEXT


def unique_strings(values: List[object]) -> List[str]:
    seen = set()
    rows: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def parse_bool(value: object, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on", "사용", "허용"}


def parse_key_values(text: str, value_kind: str = "text") -> Dict[str, object]:
    rows: Dict[str, object] = {}
    for chunk in str(text or "").replace("\n", ",").split(","):
        item = chunk.strip()
        if not item or ":" not in item and "=" not in item:
            continue
        sep = "=" if "=" in item else ":"
        key, value = item.split(sep, 1)
        clean_key = key.strip()
        if not clean_key:
            continue
        rows[clean_key] = parse_bool(value) if value_kind == "bool" else value.strip()
    return rows


def parse_instrument_profiles_text(text: str) -> Dict[str, InstrumentProfile]:
    profiles: Dict[str, InstrumentProfile] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 2:
            continue
        symbol = parts[0].upper()
        if not symbol:
            continue
        label = parts[1] or symbol
        archetypes = unique_strings((parts[2] if len(parts) > 2 else "").replace("/", ",").split(","))
        intent = parts[3] if len(parts) > 3 and parts[3] else "core"
        sensitivities = {str(key): str(value) for key, value in parse_key_values(parts[4] if len(parts) > 4 else "").items()}
        policies = parse_key_values(parts[5] if len(parts) > 5 else "", value_kind="bool")
        policies = {
            "allowAddOnStrength": bool(policies.get("allowAddOnStrength", True)),
            "trimOnTrendBreak": bool(policies.get("trimOnTrendBreak", True)),
            "avoidAveragingDown": bool(policies.get("avoidAveragingDown", True)),
            **{key: value for key, value in policies.items() if key not in {"allowAddOnStrength", "trimOnTrendBreak", "avoidAveragingDown"}},
        }
        profiles[symbol] = InstrumentProfile(
            symbol=symbol,
            label=label,
            archetypes=archetypes,
            position_intent=intent,
            sensitivities=sensitivities,
            policies=policies,
            source="setting",
        )
    return profiles


def profile_settings(settings: Dict[str, object] = None) -> Dict[str, InstrumentProfile]:
    settings = settings if isinstance(settings, dict) else {}
    merged = parse_instrument_profiles_text(DEFAULT_INSTRUMENT_PROFILES_TEXT)
    custom = parse_instrument_profiles_text(str(settings.get("instrumentProfiles") or ""))
    merged.update(custom)
    return merged


def inferred_profile_for_position(position: Position) -> InstrumentProfile:
    symbol = str(position.symbol or "").upper().strip()
    market = str(position.market or "").upper().strip()
    currency = str(position.currency or "").upper().strip()
    sector = str(position.sector or "").strip()
    name = str(position.name or symbol or "종목")
    archetypes: List[str] = []
    sensitivities: Dict[str, str] = {}
    intent = "core"

    if market == "CRYPTO" or symbol in {"BTC", "ETH", "SOL"}:
        archetypes.extend(["CryptoAssetProfile", "HighVolatilityGrowth"])
        sensitivities["crypto"] = "high"
        intent = "market-signal"
    elif symbol in BTC_SENSITIVE_SYMBOLS or sector == "디지털자산":
        archetypes.extend(["BitcoinProxy", "HighVolatilityGrowth"])
        sensitivities["btc"] = "high"
        sensitivities["fx"] = "high" if currency and currency != "KRW" else "medium"
        intent = "trading"
    elif "우선" in name or "preferred" in name.lower():
        archetypes.append("PreferredIncome")
        sensitivities["rate"] = "high"
        intent = "income"
    elif sector == "반도체":
        archetypes.extend(["SemiconductorCyclical"])
        sensitivities["cycle"] = "medium"
        sensitivities["fx"] = "medium"
    elif sector == "AI/플랫폼":
        archetypes.extend(["PlatformGrowth"])
        sensitivities["rate"] = "medium"
    else:
        archetypes.append("EquityGeneral")

    if currency and currency != "KRW":
        sensitivities.setdefault("fx", "high")
    policies = {
        "allowAddOnStrength": "HighVolatilityGrowth" not in archetypes or symbol in {"MSTR", "NVDA"},
        "trimOnTrendBreak": True,
        "avoidAveragingDown": True,
    }
    return InstrumentProfile(
        symbol=symbol,
        label=name + " 기본 프로필",
        archetypes=unique_strings(archetypes),
        position_intent=intent,
        sensitivities=sensitivities,
        policies=policies,
        source="inferred",
    )


def instrument_profile_for_position(position: Position, settings: Dict[str, object] = None) -> InstrumentProfile:
    symbol = str(position.symbol or "").upper().strip()
    profiles = profile_settings(settings)
    return profiles.get(symbol) or inferred_profile_for_position(position)
