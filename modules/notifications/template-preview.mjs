import { stockDisplayName } from "../instruments/catalog.mjs";
import { notificationTemplateLabel } from "./editor.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { alertRuleCatalog, notificationMessageTypeIcon } from "../shell/catalog.mjs";

function notificationTemplatePreviewContext(messageType) {
  var dataLabelPrefixes = [
    "미장 가격 변동",
    "크립토 변동",
    "비트코인 변동",
    "크립토 가격",
    "크립토 거래액",
    "매수 판단",
    "매도 판단",
    "확인 단계",
    "자료 상태",
    "적정가 대비",
    "24h 거래액",
    "현재가",
    "평단가",
    "수익률",
    "기준일",
    "발송시각",
    "연속 실패",
    "실패 단계",
    "재시도",
    "투자자",
    "기울기",
    "권장 액션",
    "거래량",
    "거래액",
    "가격",
    "수급",
    "추세",
    "출처",
    "이전",
    "현재",
    "변화",
    "상태",
    "손익",
    "평가",
    "보유",
    "신호"
  ];
  var dataLabelOrder = {
    "상태": 10,
    "연속 실패": 11,
    "실패 단계": 12,
    "재시도": 13,
    "손익": 20,
    "미장 가격 변동": 20,
    "현재가": 21,
    "평단가": 22,
    "수익률": 23,
    "매수 판단": 25,
    "매도 판단": 26,
    "수급": 30,
    "추세": 40,
    "권장 액션": 41,
    "기울기": 45,
    "투자자": 50,
    "신호": 60,
    "비트코인 변동": 70,
    "크립토 변동": 71,
    "크립토 가격": 72,
    "크립토 거래액": 73,
    "출처": 88,
    "기준일": 89,
    "발송시각": 90
  };
  var separateDataLabels = {
    "상태": true,
    "연속 실패": true,
    "실패 단계": true,
    "재시도": true,
    "손익": true,
    "미장 가격 변동": true,
    "현재가": true,
    "평단가": true,
    "수익률": true,
    "매수 판단": true,
    "매도 판단": true,
    "수급": true,
    "추세": true,
    "권장 액션": true,
    "기울기": true,
    "투자자": true,
    "신호": true,
    "비트코인 변동": true,
    "크립토 변동": true,
    "크립토 가격": true,
    "크립토 거래액": true,
    "출처": true,
    "기준일": true,
    "발송시각": true,
    "평가": true,
    "보유": true
  };
  function previewReferenceDate() {
    var now = new Date();
    var shifted = new Date(now.getTime() + 9 * 60 * 60000);
    function pad(value) {
      return String(value).padStart(2, "0");
    }
    return shifted.getUTCFullYear() + "-" + pad(shifted.getUTCMonth() + 1) + "-" + pad(shifted.getUTCDate()) + " " + pad(shifted.getUTCHours()) + ":" + pad(shifted.getUTCMinutes()) + " KST";
  }
  function previewSentTime() {
    return previewReferenceDate();
  }
  function plainBullet(text) {
    var cleaned = String(text || "").trim();
    return cleaned ? "• " + cleaned : "";
  }
  function htmlBullet(text) {
    var cleaned = String(text || "").trim();
    return cleaned ? "• " + escapeHtml(cleaned) : "";
  }
  function splitLabelValue(text) {
    var cleaned = String(text || "").trim();
    var marker = cleaned.indexOf(": ");
    if (marker > 0 && marker <= 18) {
      return {
        label: cleaned.slice(0, marker).trim(),
        value: cleaned.slice(marker + 2).trim()
      };
    }
    return { label: "", value: cleaned };
  }
  function criterionRow(text, rich) {
    var pair = splitLabelValue(text);
    if (pair.label && pair.value) {
      if (rich) return "• <b>" + escapeHtml(pair.label) + "</b>: <code>" + escapeHtml(pair.value) + "</code>";
      return "• " + pair.label + ": " + pair.value;
    }
    return rich ? htmlBullet(text) : plainBullet(text);
  }
  function criterionRows(items, rich) {
    return (items || []).map(function (item) { return criterionRow(item, rich); }).filter(Boolean).join("\n");
  }
  function splitDataLine(line) {
    var text = String(line || "").trim();
    for (var index = 0; index < dataLabelPrefixes.length; index += 1) {
      var label = dataLabelPrefixes[index];
      var colonPrefix = label + ": ";
      if (text.indexOf(colonPrefix) === 0) {
        var colonValue = text.slice(colonPrefix.length).trim();
        if (colonValue) return { label: label, value: colonValue };
      }
      var prefix = label + " ";
      if (text.indexOf(prefix) === 0) {
        var value = text.slice(prefix.length).trim();
        if (value) return { label: label, value: value };
      }
    }
    return { label: "", value: text };
  }
  function dataValue(rawItems, label) {
    for (var index = 0; index < (rawItems || []).length; index += 1) {
      var pair = splitDataLine(rawItems[index]);
      if (pair.label === label && pair.value) return pair.value;
    }
    return "";
  }
  function signedDirection(value) {
    var match = String(value || "").match(/([+-])\s*\d/);
    if (!match) return 0;
    return match[1] === "+" ? 1 : -1;
  }
  function dominantSignedDirection(value) {
    var text = String(value || "");
    var regex = /([+-])\s*(\d+(?:\.\d+)?)/g;
    var match;
    var dominant = 0;
    while ((match = regex.exec(text)) !== null) {
      var sign = match[1] === "-" ? -1 : 1;
      var numeric = parseFloat(match[2]);
      if (!Number.isNaN(numeric) && Math.abs(numeric) > Math.abs(dominant)) {
        dominant = sign * numeric;
      }
    }
    if (dominant > 0) return 1;
    if (dominant < 0) return -1;
    return 0;
  }
  function titleFromChange(value, positive, negative, neutral) {
    var direction = dominantSignedDirection(value);
    if (direction > 0) return positive;
    if (direction < 0) return negative;
    return neutral;
  }
  function firstDataText(rawItems, pattern) {
    var regex = new RegExp(pattern);
    for (var index = 0; index < (rawItems || []).length; index += 1) {
      var text = String(rawItems[index] || "").trim();
      if (regex.test(text)) return text;
    }
    return "";
  }
  function percentText(value) {
    var text = String(value || "").trim();
    var match = text.match(/[-+]?\d+(?:\.\d+)?%/);
    return match ? match[0] : text;
  }
  function notificationTitleIcon(type, rawItems, sample) {
    var status = dataValue(rawItems, "상태");
    var profit = dataValue(rawItems, "손익") || dataValue(rawItems, "수익률");
    var change = dataValue(rawItems, "변화");
    var signal = dataValue(rawItems, "신호");
    var titleText = String(sample && sample.title || "");
    if (type === "modelBuy" || type === "watchlistBuyCandidate") return "🟢";
    if (type === "investmentInsight") {
      var insightTypeIcon = dataValue(rawItems, "인사이트 유형");
      var actionIcon = dataValue(rawItems, "권장 액션");
      var insightBlobIcon = [status, profit, actionIcon, insightTypeIcon, titleText].join(" ");
      if (/손절|손실|축소/.test(insightBlobIcon)) return "🛡️";
      if (/분할|익절|수익|리밸런싱/.test(insightBlobIcon)) return "💰";
      if (/매수|기회/.test(insightBlobIcon)) return "🟢";
      if (insightBlobIcon.indexOf("외부") >= 0) return "🌐";
      return "🧭";
    }
    if (type === "modelSell") return "🔴";
    if (type === "holdingTiming") {
      var statusBlob = [status, profit, titleText].join(" ");
      if (/손절|손실/.test(statusBlob) || signedDirection(profit) < 0) return "🛡️";
      if (/분할|익절|수익/.test(statusBlob)) return "💰";
      return "⚖️";
    }
    if (type === "monitorPnlChange") {
      var pnlDirection = dominantSignedDirection(change);
      return pnlDirection > 0 ? "📈" : pnlDirection < 0 ? "📉" : "📊";
    }
    if (type === "monitorValueChange") return dominantSignedDirection(change) < 0 ? "💸" : "💵";
    if (type === "monitorTrendChange") {
      if (signal.indexOf("하향") >= 0 || signal.indexOf("이탈") >= 0) return "📉";
      if (signal.indexOf("상향") >= 0 || signal.indexOf("돌파") >= 0) return "📈";
      return "📊";
    }
    if (type === "monitorDecisionChange") {
      var current = dataValue(rawItems, "현재");
      if (/손절|손실/.test(current)) return "🛡️";
      if (/분할|익절|수익/.test(current)) return "💰";
      if (current.indexOf("리밸런싱") >= 0) return "⚖️";
      return "🔁";
    }
    return notificationMessageTypeIcon(type);
  }
  function notificationTitleHeadline(type, rawItems, sample, fallback) {
    var status = dataValue(rawItems, "상태");
    var profit = dataValue(rawItems, "손익") || dataValue(rawItems, "수익률");
    var change = dataValue(rawItems, "변화");
    var signal = dataValue(rawItems, "신호");
    var titleText = String(sample && sample.title || "");
    var symbol = String(sample && sample.symbol || "").toUpperCase();
    if (type === "investmentInsight") {
      var insightType = dataValue(rawItems, "인사이트 유형");
      var action = dataValue(rawItems, "권장 액션");
      var insightBlob = [status, profit, action, insightType, dataValue(rawItems, "핵심 결론"), titleText].join(" ");
      var profitText = percentText(profit);
      if (/손절|손실|축소/.test(insightBlob)) return (profitText && signedDirection(profit) < 0 ? "손실 " + profitText + ": " : "") + "손절·분할축소 점검";
      if (/분할|익절|수익|리밸런싱/.test(insightBlob)) return (profitText && signedDirection(profit) > 0 ? "수익 " + profitText + ": " : "") + "분할매도·리밸런싱 점검";
      if (/매수|기회/.test(insightBlob)) return "매수 후보: 진입 조건 점검";
      if (insightBlob.indexOf("외부") >= 0) return "외부 신호: 보유 영향 점검";
      return insightType ? insightType + ": 대응 기준 점검" : "투자 인사이트: 대응 기준 점검";
    }
    if (type === "modelBuy" || type === "watchlistBuyCandidate") return "매수 후보 감지";
    if (type === "modelSell") return "매도 기준 점검";
    if (type === "watchlistQuote") return "관심종목 시세 갱신";
    if (type === "watchlistQuotePending") return "관심종목 시세 미수집";
    if (type === "holdingTiming") {
      var statusBlob = [status, profit, titleText].join(" ");
      var profitText = percentText(profit);
      if (/손절|손실/.test(statusBlob) || signedDirection(profit) < 0) return (profitText ? "손실 " + profitText + ": " : "") + "손절·분할축소 권장";
      if (/분할|익절|수익/.test(statusBlob)) return (profitText ? "수익 " + profitText + ": " : "") + "분할매도 권장";
      if (statusBlob.indexOf("조건부") >= 0) return "조건부 보유: 추가매수 보류";
      return "보유 판단: 유지·대기";
    }
    if (type === "monitorHeartbeat") return "모니터링 상태 확인";
    if (type === "monitorConnection") {
      var connectionBlob = [status, (rawItems || []).slice(0, 2).join(" ")].join(" ").toLowerCase();
      if (/실패|오류|unauthorized|forbidden|timeout|error/.test(connectionBlob)) return "토스 연결 오류";
      return "토스 연결 상태 변경";
    }
    if (type === "monitorPositionChange") {
      var body = (rawItems || []).join(" ");
      if (body.indexOf("신규") >= 0) return "신규 보유 감지";
      if (/제외|청산|매도 완료/.test(body)) return "보유 제외 감지";
      return "보유 수량 변경";
    }
    if (type === "monitorPnlChange") return titleFromChange(change, "손익률 개선", "손익률 악화", "손익률 변화");
    if (type === "monitorValueChange") return titleFromChange(change, "평가액 증가", "평가액 감소", "평가액 변화");
    if (type === "monitorTrendChange") {
      if (signal.indexOf("하향") >= 0 || signal.indexOf("이탈") >= 0) return "이동평균 하향 신호";
      if (signal.indexOf("상향") >= 0 || signal.indexOf("돌파") >= 0) return "이동평균 상향 신호";
      return "이동평균·추세 신호";
    }
    if (type === "monitorCashChange") return titleFromChange(change, "현금 비중 증가", "현금 비중 감소", "현금 비중 변화");
    if (type === "monitorDecisionChange") {
      var current = dataValue(rawItems, "현재");
      if (/손절|손실/.test(current)) return "판단 변경: 손절·분할축소 권장";
      if (/분할|익절|수익/.test(current)) return "판단 변경: 분할매도 권장";
      if (current.indexOf("리밸런싱") >= 0) return "판단 변경: 리밸런싱 권장";
      if (current.indexOf("보유") >= 0) return "판단 변경: 보유 유지";
      return "판단 변경: 대응 액션 변경";
    }
    if (type === "externalEquityMove") return titleFromChange(dataValue(rawItems, "미장 가격 변동"), "미장 가격 급등", "미장 가격 급락", "미장 가격·거래량 급변");
    if (type === "externalCryptoMove") {
      var cryptoModel = sample && sample.cryptoMoveModel && typeof sample.cryptoMoveModel === "object" ? sample.cryptoMoveModel : {};
      var modelTitle = String(cryptoModel.titleLabel || sample && sample.cryptoMoveTitle || "").trim();
      if (modelTitle) return modelTitle;
      var cryptoLine = firstDataText(rawItems, "(비트코인|크립토).*?(24h|7d)");
      var asset = cryptoLine.indexOf("비트코인") >= 0 || symbol === "BTC" ? "비트코인" : "크립토";
      return titleFromChange(cryptoLine, asset + " 가격 급등", asset + " 가격 급락", asset + " 가격 급변");
    }
    if (type === "externalMacroShift") return "금리·거시 지표 변화";
    if (type === "externalDartDisclosure") return "국내 공시 감지";
    if (type === "externalDataConnection") return rawItems && rawItems[0] ? String(rawItems[0]).trim() + " 연결 점검" : "외부 API 연결 점검";
    return fallback || titleText || type;
  }
  function groupedDataRows(items) {
    var rows = [];
    for (var index = 0; index < items.length; index += 2) {
      rows.push("• " + items.slice(index, index + 2).join(", "));
    }
    return rows;
  }
  function orderedDataEntries(rawItems) {
    return rawItems.map(function (line, index) {
      var pair = splitDataLine(line);
      if (pair.label && pair.value) {
        return {
          kind: "pair",
          label: pair.label,
          value: pair.value,
          index: index,
          order: Object.prototype.hasOwnProperty.call(dataLabelOrder, pair.label) ? dataLabelOrder[pair.label] : 100 + index
        };
      }
      return {
        kind: "text",
        text: String(line || "").trim(),
        index: index,
        order: 100 + index
      };
    }).sort(function (a, b) {
      if (a.order !== b.order) return a.order - b.order;
      return a.index - b.index;
    });
  }
  function dataPairText(label, value, rich) {
    if (rich) return "<b>" + escapeHtml(label) + "</b>: <code>" + escapeHtml(value) + "</code>";
    return label + ": " + value;
  }
  function formattedDataRows(rawItems, rich) {
    var rows = [];
    var pairs = [];
    function flushPairs() {
      if (pairs.length) {
        rows = rows.concat(groupedDataRows(pairs));
        pairs = [];
      }
    }
    orderedDataEntries(rawItems).forEach(function (entry) {
      if (entry.kind === "pair") {
        var pairText = dataPairText(entry.label, entry.value, rich);
        if (separateDataLabels[entry.label]) {
          flushPairs();
          rows.push("• " + pairText);
        } else {
          pairs.push(pairText);
        }
      } else {
        flushPairs();
        rows.push(rich ? htmlBullet(entry.text) : plainBullet(entry.text));
      }
    });
    flushPairs();
    return rows.filter(Boolean).join("\n");
  }
  function plainDataRows(rawItems) {
    return formattedDataRows(rawItems, false);
  }
  function htmlDataRows(rawItems) {
    return formattedDataRows(rawItems, true);
  }
  function criterionLinesForSample(sample, type, triggerSummary, rawItems) {
    if (Array.isArray(sample.criteria) && sample.criteria.length) return sample.criteria.slice();
    var detected = "";
    if (type === "monitorPnlChange" || type === "monitorValueChange" || type === "monitorCashChange") {
      detected = rawItems.filter(function (line) { return /^변화\s/.test(line) || /^이전\s/.test(line) || /^현재\s/.test(line); }).join(", ");
    } else if (type === "monitorTrendChange") {
      detected = rawItems.filter(function (line) { return /^신호\s/.test(line) || /^추세[:\s]/.test(line); }).join(", ");
    } else if (type === "externalEquityMove") {
      detected = rawItems.filter(function (line) { return /^미장 가격 변동\s/.test(line) || /^현재가[:\s]/.test(line) || /^가격\s/.test(line); }).join(", ");
    } else if (rawItems.length) {
      detected = rawItems[0];
    }
    return ["설정: " + triggerSummary].concat(detected ? ["감지: " + detected] : []);
  }
  var type = messageType || "monitorHeartbeat";
  var samples = {
    default: {
      title: "삼성전자 관찰 알림",
      symbol: "005930",
      severity: "WATCH",
      lines: ["현재가 71,000원", "관찰 기준 유지", "다음 장에서 수급 재확인"],
      criteria: ["설정: 알림 조건이 실제 데이터에서 충족될 때", "감지: 현재가 71,000원"]
    },
    investmentInsight: {
      title: "삼성전자",
      symbol: "005930",
      severity: "WATCH",
      lines: ["인사이트 유형: 리스크 관리", "판단: 수익 보호를 위한 분할축소 검토", "현재가: 101,300원", "평단가: 90,200원", "수익률: +12.2%", "수급: 거래량 1,200,000(1.4x), 거래액 1216억 원", "추세: 20일선보다 6.5% 낮음, 60일선보다 30.7% 낮음", "권장 대응: 보유 수량 일부를 줄일 기준 점검", "핵심 결론: 보유 판단과 외부 신호가 위험 관리 쪽으로 기울었습니다.", "근거 신호: 보유 타이밍, 판단 변화, 거시 지표 변화", "다음 확인: 손절/분할축소 기준과 다음 조회 유지 여부를 확인하세요."],
      criteria: ["설정: TypeDB 관계 규칙에서 의미 있는 투자 변화가 확인될 때", "감지: 수익 보호와 가격 흐름 약화 조건이 함께 확인"]
    },
    modelBuy: {
      title: "삼성전자 매수 후보",
      symbol: "005930",
      severity: "WATCH",
      lines: ["판단: 소액 진입 조건 확인", "현재가: 71,000원", "평단가: 74,000원", "수익률: -4.1%", "적정가 대비 -12.4%", "가격 흐름과 거래량 조건 확인"],
      criteria: ["설정: TypeDB 진입 관계와 자료 상태가 모두 확인될 때", "감지: 가격 회복과 거래량 조건이 함께 확인"]
    },
    modelSell: {
      title: "엔비디아 분할매도 검토",
      symbol: "NVDA",
      severity: "ALERT",
      lines: ["판단: 수익 보호를 위한 분할축소 검토", "현재가: $180", "평단가: $142", "수익률: +26.8%", "수익 보호 기준 확인", "20일선 이탈 여부 확인"],
      criteria: ["설정: TypeDB 수익 보호 관계가 확인될 때", "감지: 수익 구간과 가격 흐름 약화가 함께 확인"]
    },
    watchlistBuyCandidate: {
      title: "Apple 관심종목 매수 후보",
      symbol: "AAPL",
      severity: "WATCH",
      lines: ["판단: 관심 유지·진입 조건 확인", "관심 종목", "현재 $185", "거래량과 이동평균 조건 확인"],
      criteria: ["설정: TypeDB 진입 관찰 관계가 확인될 때", "감지: 가격 흐름과 거래량 조건 확인"]
    },
    watchlistQuote: {
      title: "엔비디아",
      symbol: "NVDA",
      severity: "WATCH",
      lines: ["관심종목 시세 수집", "현재 $180", "20일선 $172(+4.7%)", "관심종목 알림 기준과 매수 후보를 확인하세요."],
      criteria: ["설정: 관심종목 가격 변화율 ±3% 이상", "감지: 현재가 $180 수집"]
    },
    watchlistQuotePending: {
      title: "카카오",
      symbol: "035720",
      severity: "INFO",
      lines: ["관심종목 시세 대기", "현재가를 아직 받지 못했습니다.", "토스 candles 응답, 종목 코드, 허용 IP를 확인하세요."],
      criteria: ["설정: 관심종목 현재가가 아직 수집되지 않았을 때", "감지: 현재가 없음"]
    },
    holdingTiming: {
      title: "SK하이닉스 매수·매도 타이밍",
      symbol: "000660",
      severity: "WATCH",
      lines: ["판단: 보유 유지·다음 조건 확인", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "추세: 현재 150,000원, 20일선 144,000원(+4.2%)", "수급: 거래량 31,000(1.7x), 거래액 48억 원", "투자자: 외국인 +22,000, 기관 -8,000", "권장 대응: 보유 유지, 추가매수는 가격 회복과 수급 확인 전 보류"],
      criteria: ["설정: TypeDB 보유 관계가 새 조건 또는 의미 있는 변화로 확인될 때", "감지: 보유 손익과 가격 흐름 변화 확인"]
    },
    monitorHeartbeat: {
      title: "실시간 모니터링",
      symbol: "",
      severity: "INFO",
      lines: ["모니터링 정상 작동", "상태 토스 계좌 동기화", "보유 5개", "평가 5,081만 원"],
      criteria: ["설정: 실시간 모니터링 워커 생존 확인 주기", "감지: 상태 토스 계좌 동기화, 보유 5개"]
    },
    monitorConnection: {
      title: "연결 상태",
      symbol: "",
      severity: "ALERT",
      lines: ["상태 연속 인증 실패", "연속 실패 3회", "실패 단계 accounts", "재시도 access token 재발급 1회", "토스 조회 실패 · Toss accounts 단계 실패 · HTTP 401 Unauthorized"],
      criteria: ["설정: 토스 연결 모드가 live가 아니며 3회 이상 연속 실패할 때만 보냅니다", "감지: 연속 실패 3회, stage=accounts, mode=demo"]
    },
    monitorPositionChange: {
      title: "SK하이닉스",
      symbol: "000660",
      severity: "WATCH",
      lines: ["보유 수량 변경", "이전 4주", "현재 5주", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.4%", "평가액 1,114만 원"],
      criteria: ["설정: 직전 스냅샷 대비 보유 수량이 달라졌을 때", "감지: 이전 4주, 현재 5주"]
    },
    monitorPnlChange: {
      title: "SK하이닉스",
      symbol: "000660",
      severity: "WATCH",
      lines: ["손익률 급변", "이전 -16.3%", "현재 -13.3%", "변화 +3.0%p", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.3%"],
      criteria: ["설정: 손익률 변화폭 ±2%p 이상", "감지: 변화 +3.0%p, 이전 -16.3%, 현재 -13.3%"]
    },
    monitorValueChange: {
      title: "SK하이닉스",
      symbol: "000660",
      severity: "WATCH",
      lines: ["평가액 급변", "이전 1,051만 원", "현재 1,114만 원", "변화 +6.0%", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.3%"],
      criteria: ["설정: 평가액 변화율 ±5% 이상", "감지: 변화 +6.0%, 이전 1,051만 원, 현재 1,114만 원"]
    },
    monitorTrendChange: {
      title: "SK하이닉스",
      symbol: "000660",
      severity: "ALERT",
      lines: ["이동평균 변화", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "신호 20일선 하향 이탈 · 60일선 상향 돌파", "추세: 20일선 144,000원보다 4.2% 높음, 60일선 137,500원보다 9.1% 높음", "수급: 거래량 31,000(1.7x), 거래액 48억 원", "투자자: 외국인 +22,000, 기관 -8,000"],
      criteria: ["설정: 20일/60일 이동평균 돌파, 크로스, 또는 현재가가 이동평균보다 8% 이상 높거나 낮을 때", "감지: 신호 20일선 하향 이탈 · 60일선 상향 돌파"]
    },
    monitorCashChange: {
      title: "현금비중",
      symbol: "",
      severity: "ALERT",
      lines: ["한국장", "이전 +12.0%", "현재 +1.0%", "변화 -11.0%p"],
      criteria: ["설정: 시장별 현금 비중 변화폭 ±10%p 이상", "감지: 변화 -11.0%p, 이전 +12.0%, 현재 +1.0%"]
    },
    monitorDecisionChange: {
      title: "SK하이닉스",
      symbol: "000660",
      severity: "WATCH",
      lines: ["판단 변화", "이전 확인 단계: 관찰", "현재 확인 단계: 조건 확인", "변화: 방향 변경", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "권장 액션: 보유 유지, 추가매수는 새 매수 신호가 뜰 때까지 보류", "Codex 답변: 판단 방향이 바뀌어 재검토 필요"],
      criteria: ["설정: 판단 행동 또는 확인 단계가 바뀔 때", "감지: 관찰 → 조건 확인, 방향 변경"]
    },
    externalEquityMove: {
      title: "미국 주식 변동",
      symbol: "AAPL",
      severity: "WATCH",
      lines: ["미장 가격 변동 +3.1%", "현재가: $180", "평단가: $155", "수익률: +16.1%", "거래량 58,000,000", "출처 Alpha Vantage"],
      criteria: ["설정: 미장 가격 변동률 ±3% 이상", "감지: 가격 변동 +3.1%, 현재가 $180"]
    },
    externalCryptoMove: {
      title: "크립토 변동",
      symbol: "BTC",
      severity: "WATCH",
      lines: ["비트코인 변동 24h +4.5% · 7d +11.2%", "크립토 가격 $61,227", "크립토 거래액 $42,000,000,000", "MSTR 등 비트코인 민감 종목 점검"],
      criteria: ["설정: 크립토 24h ±4% 또는 7d ±10% 이상", "감지: 7일 변동 +11.2%로 새 조건 성립 · 24시간 +4.5%"],
      cryptoMoveTitle: "비트코인 가격 급등",
      reviewLevel: "check",
      changeState: "new-condition",
      dataState: "sufficient",
      cryptoMoveDirection: "상승",
      cryptoMoveDominantPeriod: "7일",
      cryptoMoveDominantChange: 11.2,
      cryptoMoveReason: "7일 변동률 +11.2%가 기준 ±4%를 넘어서 비트코인 가격 급등으로 판단했습니다.",
      cryptoMoveModel: { titleLabel: "비트코인 가격 급등", reviewLevel: "check", changeState: "new-condition", dataState: "sufficient", dominantPeriodLabel: "7일", dominantChange: 11.2, reason: "7일 변동률 +11.2%가 기준 ±4%를 넘어서 비트코인 가격 급등으로 판단했습니다." }
    },
    externalMacroShift: {
      title: "매크로 지표 변화",
      symbol: "DGS10",
      severity: "WATCH",
      lines: ["FRED 금리/스프레드 변화", "DGS10 4.35% (+25bp)", "10Y-2Y 0.4% (+30bp)", "성장주 할인율 재점검"],
      criteria: ["설정: FRED 금리 또는 10Y-2Y 스프레드 변화 ±15bp 이상", "감지: DGS10 +25bp, 10Y-2Y +30bp"]
    },
    externalDartDisclosure: {
      title: "국내 공시 감지",
      symbol: "005930",
      severity: "INFO",
      lines: ["신규 공시 감지", "단일판매·공급계약", "현재가: 71,000원", "평단가: 74,000원", "수익률: -4.1%", "접수일 20260701", "출처 OpenDART"],
      criteria: ["설정: OpenDART 접수번호가 직전 조회와 다를 때", "감지: 단일판매·공급계약, 접수일 20260701"]
    },
    externalDataConnection: {
      title: "외부 데이터 연결 상태",
      symbol: "",
      severity: "INFO",
      lines: ["FRED", "응답 지연", "키/호출 제한/응답 형식 확인"],
      criteria: ["설정: 외부 데이터 API 응답 오류, 호출 제한, 또는 응답 형식 문제가 감지될 때", "감지: FRED - 응답 지연"]
    },
    modelReview: {
      title: "내 매매 모델 점검",
      symbol: "005930",
      severity: "INFO",
      body: "내 매매 모델 점검\n- 매수 후보 2개\n- 분할매도 검토 1개\n- 기준값 변경 전후를 비교하세요."
    },
    workHandoff: {
      title: "작업 완료 핸드오프",
      symbol: "",
      severity: "INFO",
      body: "작업 완료 핸드오프\n- 커밋 abc1234\n- npm test 통과\n- origin/main push 완료"
    },
    notification: {
      title: "일반 알림",
      symbol: "",
      severity: "INFO",
      body: "일반 알림\n- 테스트 메시지입니다.\n- 템플릿 변경 후 발송 포맷을 확인하세요."
    }
  };
  var sample = samples[type] || samples.default;
  var rawItems = Array.isArray(sample.lines) ? sample.lines.filter(function (line) { return String(line || "").trim(); }) : [];
  var hasReferenceDate = rawItems.some(function (line) { return splitDataLine(line).label === "기준일"; });
  var referenceDate = sample.referenceDate || previewReferenceDate();
  if (!hasReferenceDate && referenceDate) rawItems.push("기준일 " + referenceDate);
  var sentTime = sample.sentTime || previewSentTime();
  if (type === "holdingTiming" && !rawItems.some(function (line) { return splitDataLine(line).label === "발송시각"; }) && sentTime) {
    rawItems.push("발송시각 " + sentTime);
  }
  var rawLines = rawItems.join("\n");
  var lines = rawItems.map(function (line) { return "- " + line; }).join("\n");
  var bulletLines = rawItems.map(plainBullet).join("\n");
  var rule = alertRuleCatalog.filter(function (item) { return item.key === type; })[0] || {};
  var messageTypeLabel = rule.label || notificationTemplateLabel(type);
  var severityLabel = sample.severity === "ALERT" ? "주의" : sample.severity === "WATCH" ? "관찰" : "정보";
  var displaySymbol = sample.symbol ? stockDisplayName(sample.symbol, sample) : "";
  var symbolLine = displaySymbol ? "종목: " + displaySymbol : "";
  var typeLine = messageTypeLabel ? "유형: " + messageTypeLabel : "";
  var severityLine = severityLabel ? "상태: " + severityLabel : "";
  var triggerSummary = rule.description ? rule.description : "조건이 실제 데이터에서 충족될 때 보냅니다.";
  var triggerLine = triggerSummary ? "발생 조건: " + triggerSummary : "";
  var criterionLines = criterionLinesForSample(sample, type, triggerSummary, rawItems);
  var dataLines = lines;
  var statusHeadline = severityLabel ? "[" + severityLabel + "]" : "";
  var titleIcon = notificationTitleIcon(type, rawItems, sample);
  var titleHeadline = notificationTitleHeadline(type, rawItems, sample, messageTypeLabel || sample.title);
  var headline = [statusHeadline, titleIcon, titleHeadline].filter(Boolean).join(" ");
  var targetValue = sample.title || displaySymbol || "";
  if (displaySymbol && sample.symbol && targetValue.toUpperCase() === String(sample.symbol || "").toUpperCase()) {
    targetValue = displaySymbol;
  }
  if (displaySymbol && targetValue && targetValue.indexOf(displaySymbol) < 0) {
    targetValue += " / " + displaySymbol;
  }
  var targetLine = targetValue ? "대상: " + targetValue : "";
  var triggerBlockRows = criterionRows(criterionLines, false);
  var triggerBlock = triggerBlockRows ? "발송 기준\n" + triggerBlockRows : "";
  var dataRows = plainDataRows(rawItems);
  var dataBlock = dataRows ? "데이터\n" + dataRows : "";
  var divider = "";
  var readableMessage = [
    headline,
    targetValue,
    "",
    dataBlock,
    triggerBlock ? "" : "",
    triggerBlock
  ].filter(function (line, index, list) {
    if (line === "") return index > 0 && list[index - 1] !== "";
    return String(line || "").trim();
  }).join("\n").trim();
  var telegramDataLines = htmlDataRows(rawItems);
  var telegramMessage = [
    "<b>" + escapeHtml(headline) + "</b>",
    targetValue ? "<code>" + escapeHtml(targetValue) + "</code>" : "",
    "",
    telegramDataLines ? "<b>데이터</b>" : "",
    telegramDataLines,
    criterionLines.length ? "" : "",
    criterionLines.length ? "<b>발송 기준</b>" : "",
    criterionRows(criterionLines, true)
  ].filter(function (line, index, list) {
    if (line === "") return index > 0 && list[index - 1] !== "";
    return String(line || "").trim();
  }).join("\n").trim();
  var body = sample.body || readableMessage || [sample.title, lines].filter(Boolean).join("\n");
  return {
    title: sample.title || samples.default.title,
    lines: lines,
    rawLines: rawLines,
    referenceDate: referenceDate,
    eventGeneratedAt: referenceDate,
    sentAt: sentTime,
    sentTime: sentTime,
    sentLine: "발송시각 " + sentTime,
    dataLines: dataLines,
    bulletLines: bulletLines,
    body: body,
    readableMessage: readableMessage,
    telegramMessage: telegramMessage,
    telegramDataLines: telegramDataLines,
    messageType: type,
    messageTypeLabel: messageTypeLabel,
    symbol: displaySymbol || sample.symbol || "",
    rawSymbol: sample.symbol || "",
    symbolDisplayName: displaySymbol,
    headline: headline,
    statusHeadline: statusHeadline,
    titleIcon: titleIcon,
    titleHeadline: titleHeadline,
    targetLine: targetLine,
    triggerBlock: triggerBlock,
    criterionBlock: triggerBlock,
    criterionLines: criterionLines.join("\n"),
    dataBlock: dataBlock,
    divider: divider,
    symbolLine: symbolLine,
    severity: sample.severity || "INFO",
    severityLabel: severityLabel,
    severityLine: severityLine,
    rule: type,
    typeLine: typeLine,
    triggerSummary: triggerSummary,
    triggerLine: triggerLine,
    key: type + ":preview",
    target: targetValue || type,
    rawTarget: sample.symbol || type,
    accountLabel: "기본 계정",
    accountId: "default",
    cryptoMoveModel: sample.cryptoMoveModel || "",
    reviewLevel: sample.reviewLevel || "observe",
    changeState: sample.changeState || "unchanged",
    dataState: sample.dataState || "partial",
    cryptoMoveDirection: sample.cryptoMoveDirection || "",
    cryptoMoveDominantPeriod: sample.cryptoMoveDominantPeriod || "",
    cryptoMoveDominantChange: sample.cryptoMoveDominantChange || "",
    cryptoMoveTitle: sample.cryptoMoveTitle || "",
    cryptoMoveReason: sample.cryptoMoveReason || ""
  };
}

export { notificationTemplatePreviewContext };
