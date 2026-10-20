"""Crypto observation scope and source-bound research instructions."""
from digital_twin.modules.reasoning.contracts import CRYPTO_EVIDENCE_PROFILE

ASSETS = {"bitcoin": ("BTC", "비트코인"), "ethereum": ("ETH", "이더리움")}


def configured_crypto_assets(settings):
    if str(settings.get("externalCoinGeckoEnabled", "1")).lower() in {"0", "false", "off", "no"}:
        return []
    ids = str(settings.get("externalCryptoIds") or "bitcoin,ethereum").split(",")
    return [ASSETS[key] for key in dict.fromkeys(value.strip().lower() for value in ids) if key in ASSETS]


def instructions(packet):
    if packet.get("profile") != CRYPTO_EVIDENCE_PROFILE:
        return ""
    return """크립토 관찰·연구 계약 v1:
관찰 대상은 crypto-asset이며 주식·기업 실적 자료와 구분합니다. 원본 CoinGecko 가격은 USD, volume24h는 코인 개수나 주식 수량이 아닌 달러 거래대금입니다.
change24h와 change7d는 서로 다른 기간의 변동률입니다. 기간별 수치를 보존하고 같은 지표의 과거 값과만 비교하세요.
크립토 시장 참고 대상으로 등록된 사실은 실제 보유·평균 매수가·계정 비중의 증거가 아닙니다. 포트폴리오 영향은 확인된 노출에 한해 설명하세요.
가격·거래대금만으로 자금 유입, 온체인 활동, 펀딩·미결제약정·청산 또는 인과관계를 추정하지 마세요. 이 자료가 없으면 미확인 사항으로 남기세요.
가능한 설명·경쟁 설명·반증 조건을 구분하고 이전 판단·연구 질문·검증 결과를 다시 읽으세요. 조건 성립은 수익률 예측 성공이 아닙니다.
businessResearch.theses와 reviews는 빈 배열입니다. crypto 연구 틀의 지식 제안은 별도 연구 실행에서 기존 독립 검토를 거칩니다. 시장 관찰의 knowledgeResearch.claims=[] 정책은 유지합니다.
후속 조건은 crypto-asset의 같은 factId·같은 field를 left/right의 current 근거로 지정합니다. 지원 field는 currentPrice,change24h,change7d,volume24h,marketCap입니다.
프로그램은 right를 지금의 원본 기준값으로 고정하고 이후 새 원천 관측의 left와 비교합니다. 서로 다른 지표·기간을 섞거나 임의 숫자 기준을 만들지 마세요.
조건 설명은 기준 시점 대비 어떤 변화가 설명을 지지·약화·재검토하게 하는지 적으세요. 누락·오래된 시세·관찰 공백은 실패 예측으로 집계하지 않습니다.
새 자료가 필요하면 질문에 공개 대상·기간·답변 완료 기준을 명시하세요. 지원하지 않는 온체인·파생 자료를 이미 수집했다고 주장하지 마세요.
"""


def restrict_schema(schema, packet):
    if packet.get("profile") == CRYPTO_EVIDENCE_PROFILE:
        for key in ("theses", "reviews"):
            schema["properties"]["businessResearch"]["properties"][key]["maxItems"] = 0
    return schema
