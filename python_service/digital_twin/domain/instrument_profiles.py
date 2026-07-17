from dataclasses import dataclass, field
from typing import Dict, List

from .portfolio import Position


DEFAULT_INSTRUMENT_PROFILES_TEXT = """# symbol|label|archetypes|positionIntent|sensitivities|policies
# policies: allowAddOnStrength=1, trimOnTrendBreak=1, avoidAveragingDown=1
MSTR|비트코인 프록시 성장주|BitcoinProxy,HighVolatilityGrowth|trading|btc:high,rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
STRC|비트코인 민감 우선주/배당|PreferredIncome,BitcoinSensitiveIncome|income|btc:medium,rate:high,fx:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
000660|HBM 반도체 성장주|SemiconductorHBM,CyclicalGrowth|core|cycle:high,ai:high,fx:medium|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
SKHY|SK하이닉스 ADR|SemiconductorHBM,CyclicalGrowth,CrossListedSecurity|trading|cycle:high,ai:high,fx:high,crossListing:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SKHYV|SK하이닉스 임시 ADR|SemiconductorHBM,CyclicalGrowth,CrossListedSecurity|trading|cycle:high,ai:high,fx:high,crossListing:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SKHX|SK하이닉스 2x 롱 ETF|DailyLeveragedProduct,SingleStockETF,CrossListedSecurity|trading|cycle:high,fx:high,leverage:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SKHZ|SK하이닉스 1x 숏 ETF|InverseProduct,SingleStockETF,CrossListedSecurity|trading|cycle:high,fx:high,inverse:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SKUU|SK하이닉스 2x 롱 ETF|DailyLeveragedProduct,SingleStockETF,CrossListedSecurity|trading|cycle:high,fx:high,leverage:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SKDD|SK하이닉스 -2x 숏 ETF|InverseProduct,DailyLeveragedProduct,SingleStockETF,CrossListedSecurity|trading|cycle:high,fx:high,inverse:high,leverage:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
005930|대형 반도체 우량주|MegaCapQuality,SemiconductorCyclical|core|cycle:medium,fx:medium|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
AAPL|대형 플랫폼 우량 성장주|MegaCapQuality,PlatformGrowth|core|rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
NVDA|AI 인프라 성장주|AIGrowth,SemiconductorHBM|growth|ai:high,rate:medium,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1,avoidAveragingDown=1
TSLA|고변동 성장주|HighVolatilityGrowth,CyclicalGrowth|trading|rate:high,cycle:high,fx:high|allowAddOnStrength=0,trimOnTrendBreak=1,avoidAveragingDown=1
SPY|S&P 500 시장 프록시|MarketProxyInstrument,BroadMarketProxy,RiskAppetiteProxy|market-signal|broadMarket:high,risk:high,usd:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
QQQ|나스닥 100 성장 프록시|MarketProxyInstrument,GrowthMarketProxy,RiskAppetiteProxy|market-signal|growth:high,rate:high,usd:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
IWM|러셀 2000 소형주 프록시|MarketProxyInstrument,SmallCapRiskProxy,RiskAppetiteProxy|market-signal|smallCap:high,risk:high,credit:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
IPO|미국 IPO 사이클 프록시|MarketProxyInstrument,IPOCycleProxy,RiskAppetiteProxy|market-signal|ipo:high,growth:medium,risk:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
IPOS|해외 IPO 사이클 프록시|MarketProxyInstrument,IPOCycleProxy,ForeignMarketProxy|market-signal|ipo:medium,foreignMarket:medium,risk:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
VIXY|VIX 단기선물 ETF 프록시|MarketProxyInstrument,VolatilityProxy,RiskOffProxy|market-signal|volatility:high,riskOff:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
TLT|미국 장기국채 금리 프록시|MarketProxyInstrument,RateSensitivityProxy,DurationProxy|market-signal|rate:high,duration:high,usd:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
IEF|미국 중기국채 금리 프록시|MarketProxyInstrument,RateSensitivityProxy,DurationProxy|market-signal|rate:medium,duration:medium,usd:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
HYG|하이일드 크레딧 프록시|MarketProxyInstrument,CreditStressProxy,RiskAppetiteProxy|market-signal|credit:high,risk:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
LQD|투자등급 크레딧 프록시|MarketProxyInstrument,CreditStressProxy,DurationProxy|market-signal|credit:medium,rate:medium,duration:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
SOXX|미국 반도체 사이클 프록시|MarketProxyInstrument,SectorCycleProxy,SemiconductorCyclical|market-signal|semiconductor:high,cycle:high,ai:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
SMH|글로벌 반도체 사이클 프록시|MarketProxyInstrument,SectorCycleProxy,SemiconductorCyclical|market-signal|semiconductor:high,cycle:high,ai:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
GLD|금 가격 방어자산 프록시|MarketProxyInstrument,CommodityProxy,RiskOffProxy|market-signal|gold:high,commodity:medium,riskOff:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
USO|원유 가격 프록시|MarketProxyInstrument,CommodityProxy,CyclicalGrowth|market-signal|oil:high,commodity:high,cycle:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
UUP|달러 인덱스 프록시|MarketProxyInstrument,CurrencyProxy,RiskOffProxy|market-signal|usd:high,fx:high,riskOff:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
COIN|크립토 유동성 주식 프록시|MarketProxyInstrument,CryptoLiquidityProxy,HighVolatilityGrowth|market-signal|crypto:high,btc:high,risk:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
069500|KODEX 200 한국 대형주 프록시|MarketProxyInstrument,KoreaMarketProxy,BroadMarketProxy|market-signal|korea:high,broadMarket:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
229200|KODEX 코스닥150 성장 프록시|MarketProxyInstrument,KoreaMarketProxy,GrowthMarketProxy|market-signal|korea:high,growth:high,risk:medium|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
091160|KODEX 반도체 한국 사이클 프록시|MarketProxyInstrument,KoreaMarketProxy,SectorCycleProxy,SemiconductorCyclical|market-signal|korea:medium,semiconductor:high,cycle:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
122630|KODEX 레버리지 한국 위험선호 프록시|MarketProxyInstrument,KoreaMarketProxy,RiskAppetiteProxy,DailyLeveragedProduct|market-signal|korea:high,risk:high,leverage:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
360750|TIGER 미국S&P500 해외주식 프록시|MarketProxyInstrument,KoreaMarketProxy,ForeignMarketProxy,BroadMarketProxy|market-signal|korea:medium,foreignMarket:high,usd:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
BTC|크립토 기준 자산|MarketProxyInstrument,CryptoAssetProfile,HighVolatilityGrowth|market-signal|btc:high,crypto:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
ETH|크립토 성장 자산|MarketProxyInstrument,CryptoAssetProfile,HighVolatilityGrowth|market-signal|crypto:high|allowAddOnStrength=0,trimOnTrendBreak=0,avoidAveragingDown=1
"""


BTC_SENSITIVE_SYMBOLS = {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}

MARKET_PROXY_FACTOR_LABELS = {
    "ai": "AI 인프라",
    "broadMarket": "광범위 시장",
    "btc": "비트코인",
    "commodity": "원자재",
    "credit": "크레딧 스프레드",
    "crypto": "크립토 유동성",
    "cycle": "경기 사이클",
    "duration": "채권 듀레이션",
    "foreignMarket": "해외 시장",
    "fx": "환율",
    "gold": "금 가격",
    "growth": "성장주",
    "ipo": "IPO 사이클",
    "korea": "한국 시장",
    "leverage": "레버리지 수급",
    "oil": "원유 가격",
    "rate": "금리",
    "risk": "위험자산 심리",
    "riskOff": "위험회피",
    "semiconductor": "반도체 사이클",
    "smallCap": "소형주 심리",
    "usd": "달러",
    "volatility": "변동성",
}

MARKET_PROXY_ARCHETYPE_LABELS = {
    "BroadMarketProxy": "광범위 시장",
    "CreditStressProxy": "크레딧 스트레스",
    "CryptoLiquidityProxy": "크립토 유동성",
    "CurrencyProxy": "통화/달러",
    "DurationProxy": "채권 듀레이션",
    "ForeignMarketProxy": "해외 시장",
    "GrowthMarketProxy": "성장주 시장",
    "IPOCycleProxy": "IPO 사이클",
    "KoreaMarketProxy": "한국 시장",
    "RateSensitivityProxy": "금리 민감도",
    "RiskAppetiteProxy": "위험자산 심리",
    "RiskOffProxy": "위험회피",
    "SectorCycleProxy": "섹터 사이클",
    "SmallCapRiskProxy": "소형주 위험선호",
    "VolatilityProxy": "변동성",
}


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


def is_market_proxy_profile(profile: InstrumentProfile) -> bool:
    archetypes = set(profile.archetypes or [])
    return profile.position_intent == "market-signal" or "MarketProxyInstrument" in archetypes


def market_signal_profiles(settings: Dict[str, object] = None) -> Dict[str, InstrumentProfile]:
    return {
        symbol: profile
        for symbol, profile in profile_settings(settings).items()
        if is_market_proxy_profile(profile)
    }


def market_signal_symbols(settings: Dict[str, object] = None) -> List[str]:
    return sorted(market_signal_profiles(settings).keys())


def market_proxy_theme_label(key: str) -> str:
    return MARKET_PROXY_FACTOR_LABELS.get(str(key or ""), str(key or "시장 프록시"))


def market_proxy_themes_for_profile(profile: InstrumentProfile) -> List[Dict[str, str]]:
    themes: List[Dict[str, str]] = []
    seen = set()
    for factor, level in sorted((profile.sensitivities or {}).items()):
        key = str(factor or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        themes.append({
            "key": key,
            "label": market_proxy_theme_label(key),
            "source": "factor-sensitivity",
            "level": str(level or ""),
        })
    for archetype in profile.archetypes or []:
        label = MARKET_PROXY_ARCHETYPE_LABELS.get(archetype)
        if not label:
            continue
        key = archetype
        if key in seen:
            continue
        seen.add(key)
        themes.append({
            "key": key,
            "label": label,
            "source": "investment-archetype",
            "level": "",
        })
    return themes


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
