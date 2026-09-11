"""Web workspace boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.settings import ROOT_DIR
from digital_twin.infrastructure.settings import read_json
from digital_twin.infrastructure.settings import write_private_json
from digital_twin.infrastructure.web.adapters.brain import investment_brain_question_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import new_id
from digital_twin.infrastructure.web.common import now
from digital_twin.infrastructure.web.common import operational_read_settings
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.model_registry.infrastructure.model_reviewer import codex_cli_arguments
from digital_twin.platform.domain.event_types import APP_MEMORY_RECORDED
from digital_twin.platform.domain.event_types import CHAT_MESSAGE_APPENDED
from pathlib import Path
from typing import Dict
from typing import List
import os
import re
import subprocess
import tempfile


LOCAL_APP_STORE_PATH = ROOT_DIR / "data" / "store.json"


MEMORY_CATEGORIES = ["identity", "preference", "finance", "travel", "asset", "schedule", "work", "other"]


DOMAIN_TYPES = ["stock", "trip", "asset", "schedule", "task", "note"]


def default_store() -> Dict[str, object]:
    stamped = now()
    return {
        "version": 1,
        "profile": {
            "ownerName": "Namsoon",
            "assistantName": "Twin",
            "preferredLanguage": "한국어",
            "answerStyle": "핵심부터 말하고, 필요한 근거와 실행 단계를 짧게 정리한다.",
            "tone": "담백하고 실무적인 말투. 과장하지 않는다.",
            "decisionStyle": "선택지를 비교하고 리스크와 다음 행동을 분리해서 판단한다.",
            "riskStyle": "투자와 자산 판단은 보수적으로 접근하고, 확신이 낮으면 추가 확인을 요구한다.",
            "financePolicy": "주식은 매수/매도 지시가 아니라 관찰 포인트, 리스크, 체크리스트 중심으로 돕는다.",
            "travelPolicy": "여행은 예산, 이동 동선, 피로도, 예약 마감일을 함께 본다.",
            "schedulePolicy": "일정은 오늘 처리할 것, 미룰 것, 위임할 것을 나눠서 관리한다.",
            "assetPolicy": "자산은 계좌번호나 인증 정보 없이 요약 단위로 기록하고, 목표와 현금흐름 중심으로 관리한다.",
            "boundaries": "법률, 세무, 투자 판단은 최종 결정을 대신하지 않는다. 민감한 정보는 저장하지 않는다.",
        },
        "memories": [
            {
                "id": "mem-default-1",
                "content": "사용자는 한국어로 명확하고 실용적인 답변을 선호한다.",
                "category": "preference",
                "status": "approved",
                "importance": 4,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "mem-default-2",
                "content": "비서는 주식, 여행 계획, 자산관리, 스케줄 관리를 우선 도메인으로 다룬다.",
                "category": "identity",
                "status": "approved",
                "importance": 5,
                "source": "초기 설정",
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "items": [
            {
                "id": "item-default-1",
                "type": "task",
                "title": "비서에게 나의 투자 기준 입력",
                "status": "open",
                "date": "",
                "notes": "예: 장기 투자, 단기 매매 회피, 현금 비중 선호, 관심 섹터",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
            {
                "id": "item-default-2",
                "type": "schedule",
                "title": "이번 주 일정 정리",
                "status": "planned",
                "date": "",
                "notes": "중요한 회의, 마감일, 개인 약속을 입력한다.",
                "fields": {},
                "createdAt": stamped,
                "updatedAt": stamped,
            },
        ],
        "messages": [
            {
                "id": "msg-default-1",
                "role": "assistant",
                "content": "무엇부터 정리할까요? 주식 관심 목록, 여행 계획, 자산 현황, 이번 주 일정 중 하나를 말해주면 바로 기록하고 다음 행동으로 나누겠습니다.",
                "createdAt": stamped,
            }
        ],
    }


def app_store(settings: Dict[str, object] = None):
    # Opening the web shell is a read path. Schema bootstrap and retention are
    # owned by the service manager, so the first browser request must not pay
    # their startup cost.
    return stores.app_store(settings or operational_read_settings())


def read_store() -> Dict[str, object]:
    fallback = default_store()
    try:
        parsed = app_store().load()
    except Exception as error:  # noqa: BLE001 - bootstrap must remain available when optional MySQL is offline.
        parsed = read_json(LOCAL_APP_STORE_PATH, {})
        if isinstance(parsed, dict):
            parsed.setdefault("metadata", {})
            parsed["metadata"]["operationalStoreWarning"] = str(error)[:240]
    if not parsed:
        parsed = fallback
        try:
            app_store().replace(parsed)
        except Exception:  # noqa: BLE001 - local fallback keeps the web console readable.
            write_private_json(LOCAL_APP_STORE_PATH, parsed)
    return {
        **fallback,
        **parsed,
        "profile": {**fallback["profile"], **dict(parsed.get("profile") or {})},
        "memories": parsed.get("memories") if isinstance(parsed.get("memories"), list) else [],
        "items": parsed.get("items") if isinstance(parsed.get("items"), list) else [],
        "messages": parsed.get("messages") if isinstance(parsed.get("messages"), list) else [],
    }


def save_store(mutator):
    store = read_store()
    mutator(store)
    try:
        app_store().replace(store)
    except Exception:  # noqa: BLE001 - local fallback keeps manual notes usable without MySQL.
        write_private_json(LOCAL_APP_STORE_PATH, store)
    return store


def snapshot_payload() -> Dict[str, object]:
    store = read_store()
    return {
        "profile": store["profile"],
        "memories": store["memories"],
        "items": store["items"],
        "messages": store["messages"],
    }


def category_for(value: str) -> str:
    text = str(value or "")
    if re.search(r"주식|투자|종목|포트폴리오|배당|매수|매도", text):
        return "finance"
    if re.search(r"자산|현금|계좌|예산|지출|저축|대출", text):
        return "asset"
    if re.search(r"여행|항공|호텔|숙소|동선|예약", text):
        return "travel"
    if re.search(r"일정|회의|약속|마감|캘린더|할 일", text):
        return "schedule"
    if re.search(r"좋아|싫어|선호|말투|스타일|방식", text):
        return "preference"
    if re.search(r"나는|내가|나의|목표|직업|역할", text):
        return "identity"
    return "other"


def normalize_amount(value):
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def normalize_item_fields(fields) -> Dict[str, str]:
    if not isinstance(fields, dict):
        return {}
    return {str(key): "" if value is None else str(value).strip() for key, value in fields.items()}


def patch_item(item: Dict[str, object], body: Dict[str, object]) -> Dict[str, object]:
    next_item = dict(item)
    if body.get("type") in DOMAIN_TYPES:
        next_item["type"] = body["type"]
    if "title" in body and configured(body.get("title")):
        next_item["title"] = configured(body.get("title"))
    if "status" in body:
        next_item["status"] = configured(body.get("status")) or "open"
    if "date" in body:
        next_item["date"] = configured(body.get("date"))
    if "amount" in body:
        next_item["amount"] = normalize_amount(body.get("amount"))
    if "currency" in body:
        next_item["currency"] = configured(body.get("currency"))
    if "ticker" in body:
        next_item["ticker"] = configured(body.get("ticker")).upper()
    if "location" in body:
        next_item["location"] = configured(body.get("location"))
    if "notes" in body:
        next_item["notes"] = configured(body.get("notes"))
    if "fields" in body:
        next_item["fields"] = {**dict(next_item.get("fields") or {}), **normalize_item_fields(body.get("fields"))}
    next_item["updatedAt"] = now()
    return next_item


def fallback_reply(message: str) -> str:
    text = configured(message)
    if re.search(r"주식|투자|종목|포트폴리오", text):
        return "투자 판단은 매수/매도 단정보다 가격 기준, 손절 기준, 보유 이유, 현금 비중을 나눠 확인하겠습니다."
    if re.search(r"일정|회의|약속|마감", text):
        return "일정은 오늘 처리할 일, 미룰 일, 의존성이 있는 일을 분리해서 정리하겠습니다."
    if re.search(r"여행|항공|호텔|숙소", text):
        return "여행 계획은 날짜, 예산, 이동 동선, 예약 마감일을 기준으로 정리하겠습니다."
    return "기록했습니다. 필요한 내용을 주식, 여행, 자산, 일정 중 어느 쪽으로 정리할지 알려주면 다음 행동으로 나누겠습니다."


def local_memory_candidates(message: str) -> List[Dict[str, object]]:
    text = configured(message)
    if len(text) < 12:
        return []
    signals = ["나는", "내가", "나의", "선호", "좋아", "싫어", "원해", "중요", "성향", "스타일", "방식", "투자", "여행", "일정", "자산", "목표"]
    if not any(signal in text for signal in signals):
        return []
    normalized = re.sub(r"^(나는|내가|나의)\s*", "", text).strip()
    return [{
        "content": ("사용자는 " + normalized)[:180],
        "category": category_for(text),
        "importance": 4 if re.search(r"선호|싫어|좋아|중요|원해|성향|방식|스타일", text) else 3,
    }]


def memory_fingerprint(content: str) -> str:
    return re.sub(r"[.,!?'\"]", "", re.sub(r"\s+", "", str(content or "").lower())).removeprefix("사용자는")


def persist_memory_candidates(candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
    saved = []
    if not candidates:
        return saved

    def mutate(store):
        for candidate in candidates[:3]:
            content = configured(candidate.get("content"))
            if len(content) < 5:
                continue
            category = candidate.get("category") if candidate.get("category") in MEMORY_CATEGORIES else category_for(content)
            next_fingerprint = memory_fingerprint(content)
            duplicate = any(
                memory.get("status") != "archived"
                and memory.get("category") == category
                and memory_fingerprint(memory.get("content")).find(next_fingerprint) >= 0
                for memory in store["memories"]
            )
            if duplicate:
                continue
            stamped = now()
            memory = {
                "id": new_id("mem"),
                "content": content,
                "category": category,
                "status": "approved",
                "importance": max(1, min(5, int(candidate.get("importance") or 3))),
                "source": "conversation",
                "createdAt": stamped,
                "updatedAt": stamped,
            }
            store["memories"].insert(0, memory)
            saved.append(memory)

    save_store(mutate)
    if saved:
        new_domain_event(
            APP_MEMORY_RECORDED,
            "conversation",
            {"count": len(saved), "memoryIds": [item.get("id") for item in saved], "source": "conversation"},
        )
    return saved


def append_message(role: str, content: str) -> Dict[str, object]:
    message = {}

    def mutate(store):
        message.update({"id": new_id("msg"), "role": role, "content": content, "createdAt": now()})
        store["messages"].append(message)
        store["messages"] = store["messages"][-80:]

    save_store(mutate)
    new_domain_event(
        CHAT_MESSAGE_APPENDED,
        message.get("id") or role,
        {"messageId": message.get("id"), "role": role},
    )
    return message


def run_local_codex(message: str) -> str:
    if os.environ.get("LOCAL_CODEX_ENABLED") == "0":
        return ""
    codex = os.environ.get("CODEX_BIN") or "codex"
    prompt = "\n".join([
        "너는 Orbit Alpha 웹앱의 로컬 Python 비서 백엔드다.",
        "한국어로 답하고, 투자 관련 답변은 확인할 데이터와 리스크 중심으로만 말한다.",
        "파일을 수정하지 말고 설명만 한다.",
        "",
        "사용자 질문:",
        message,
    ])
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as output:
        output_path = output.name
    try:
        result = subprocess.run(
            [
                codex,
                *codex_cli_arguments(),
                "-a",
                "never",
                "--sandbox",
                "read-only",
                "--cd",
                str(ROOT_DIR),
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-last-message",
                output_path,
                "-",
            ],
            input=prompt,
            text=True,
            cwd=str(ROOT_DIR),
            env={**os.environ, "NO_COLOR": "1"},
            timeout=int(os.environ.get("CODEX_TIMEOUT_MS") or "90000") / 1000,
            capture_output=True,
        )
        if result.returncode != 0:
            return ""
        return Path(output_path).read_text(encoding="utf-8").strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    finally:
        try:
            Path(output_path).unlink()
        except OSError:
            pass


def is_investment_brain_question(message: str, body: Dict[str, object] = None) -> bool:
    body = body if isinstance(body, dict) else {}
    if configured(body.get("mode") or body.get("engine")).lower() in {"investment", "ontology", "investment-brain"}:
        return True
    compact = str(message or "").lower()
    return any(term in compact for term in [
        "주식", "종목", "매수", "매도", "보유", "추가매수", "분할축소", "손절",
        "포트폴리오", "수익률", "투자", "리스크", "공시", "주가", "증권",
    ])


def chat_payload(body: Dict[str, object]) -> Dict[str, object]:
    message = configured(body.get("message"))
    if not message:
        raise ValueError("메시지를 입력하세요.")
    append_message("user", message)
    if is_investment_brain_question(message, body):
        result = investment_brain_question_payload(body)
        append_message("assistant", str(result.get("reply") or ""))
        return result
    reply = run_local_codex(message) or fallback_reply(message)
    candidates = persist_memory_candidates(local_memory_candidates(message))
    append_message("assistant", reply)
    return {"reply": reply, "memoryCandidates": candidates, "usedFallback": True, "engine": "python"}
