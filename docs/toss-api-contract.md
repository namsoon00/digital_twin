# Toss API Contract Phase 1

Last reviewed: 2026-06-25 KST

This document defines the first implementation contract for integrating Toss Securities Open API into Digiter Twin. The app must not call Toss directly from Flutter Web. The browser calls only this project's backend-for-frontend (BFF), and the BFF calls Toss with server-side credentials.

## Official Sources

- Toss Securities Open API guide: https://developers.tossinvest.com/docs
- LLM-readable source index: https://developers.tossinvest.com/llms.txt
- Human overview markdown: https://openapi.tossinvest.com/openapi-docs/overview.md
- Canonical OpenAPI document: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json

The official OpenAPI document was available as OpenAPI `3.1.0`, API title `토스증권 Open API`, version `1.1.5`.

## Confirmed Toss Surface

- Base server: `https://openapi.tossinvest.com`
- Auth: OAuth 2.0 Client Credentials Grant via `POST /oauth2/token`
- Auth header: `Authorization: Bearer {access_token}`
- Account-scoped header: `X-Tossinvest-Account: {accountSeq}`
- Protocol: REST API
- Coverage:
  - Auth: OAuth token issuance
  - Market data: orderbook, current prices, trades, price limits, candles
  - Stock info: stock master data and stock warnings
  - Market info: KRW/USD exchange rate, KR/US market calendars
  - Account and asset: accounts, holdings
  - Order: create, modify, cancel, list, detail, buying power, sellable quantity, commissions

## Product Decisions

- Flutter Web never stores `client_secret`, Toss access tokens, or account headers.
- Flutter Web never calls `https://openapi.tossinvest.com` directly.
- The BFF owns OAuth token issuance, token caching, Toss account selection, rate-limit handling, and error normalization.
- Phase 2 starts read-only. Order endpoints remain locked behind an explicit server-side feature flag.
- The app treats every decimal money/quantity value as a string at the API boundary to avoid precision loss.
- The app accepts unknown enum values from Toss and renders them as unsupported/unknown instead of crashing.

## BFF Endpoint Contract

All endpoints below are served by Digiter Twin, not Toss. Paths are intentionally app-oriented so the frontend does not depend on Toss raw schema names.

| Method | Path | Purpose | Toss mapping | Phase |
| --- | --- | --- | --- | --- |
| `GET` | `/api/toss/status` | Return connection/configuration status without secrets | Local config + token cache metadata | 2 |
| `POST` | `/api/toss/connect` | Save encrypted client credentials and optional default account | `POST /oauth2/token`, `GET /api/v1/accounts` | 2 |
| `POST` | `/api/toss/probe` | Verify credentials and account header | `POST /oauth2/token`, optional `GET /api/v1/accounts` | 2 |
| `GET` | `/api/toss/accounts` | List user accounts with masked display numbers | `GET /api/v1/accounts` | 2 |
| `GET` | `/api/toss/portfolio` | Return holdings plus overview totals | `GET /api/v1/holdings` | 2 |
| `GET` | `/api/toss/positions` | Return normalized position rows | `GET /api/v1/holdings` | 2 |
| `GET` | `/api/toss/buying-power?currency=KRW` | Return cash buying power | `GET /api/v1/buying-power` | 3 |
| `GET` | `/api/toss/sellable-quantity?symbol=AAPL` | Return sellable quantity for a symbol | `GET /api/v1/sellable-quantity` | 3 |
| `GET` | `/api/toss/commissions` | Return account commission rates | `GET /api/v1/commissions` | 3 |
| `GET` | `/api/toss/orders?status=OPEN` | Return open or closed order list | `GET /api/v1/orders` | 4 |
| `POST` | `/api/toss/orders/preview` | Validate a proposed order locally and with read-only Toss checks | buying power, sellable quantity, commissions, market calendar | 4 |
| `POST` | `/api/toss/orders` | Create an order only when server flag and user confirmation are enabled | `POST /api/v1/orders` | Later |
| `POST` | `/api/toss/orders/{orderId}/modify` | Modify an order only when server flag and user confirmation are enabled | `POST /api/v1/orders/{orderId}/modify` | Later |
| `POST` | `/api/toss/orders/{orderId}/cancel` | Cancel an order only when server flag and user confirmation are enabled | `POST /api/v1/orders/{orderId}/cancel` | Later |

## Internal Normalized Models

These are Digiter Twin BFF models. They are not claimed to be Toss raw response schemas.

### `TossConnectionStatus`

```json
{
  "connected": true,
  "mode": "read_only",
  "baseUrl": "https://openapi.tossinvest.com",
  "accountSeq": "1",
  "accountDisplay": "****8901",
  "tokenExpiresAt": "2026-06-25T12:00:00+09:00",
  "ordersEnabled": false,
  "lastProbeAt": "2026-06-25T09:05:00+09:00"
}
```

### `TossAccount`

```json
{
  "id": "1",
  "displayNumber": "****8901",
  "type": "BROKERAGE",
  "isDefault": true
}
```

### `TossPosition`

```json
{
  "accountId": "1",
  "symbol": "005930",
  "name": "삼성전자",
  "marketCountry": "KR",
  "currency": "KRW",
  "quantity": "12",
  "lastPrice": "72000",
  "averagePurchasePrice": "65000",
  "marketValue": "864000",
  "profitLoss": "84000",
  "profitLossRate": "10.7692",
  "dailyProfitLoss": "-12000",
  "syncedAt": "2026-06-25T09:05:00+09:00"
}
```

### `TossPortfolio`

```json
{
  "accountId": "1",
  "totalPurchaseAmount": {
    "KRW": "780000",
    "USD": "420.00"
  },
  "marketValue": {
    "KRW": "864000",
    "USD": "486.20"
  },
  "profitLoss": {
    "KRW": "84000",
    "USD": "66.20"
  },
  "positions": []
}
```

### `TossOrderPreview`

```json
{
  "previewId": "preview_demo_001",
  "accountId": "1",
  "symbol": "AAPL",
  "side": "BUY",
  "orderType": "LIMIT",
  "quantity": "1",
  "price": "185.50",
  "currency": "USD",
  "estimatedOrderAmount": "185.50",
  "estimatedCommission": "0.28",
  "warnings": [],
  "ordersEnabled": false
}
```

### `TossBffError`

```json
{
  "error": {
    "requestId": "local-demo-request",
    "code": "toss-auth-required",
    "message": "토스증권 연결 인증이 필요합니다.",
    "retryable": false,
    "source": "digital-twin-bff"
  }
}
```

## Rate-Limit Handling

Toss rate limits are grouped by API family. Current public overview values:

| Group | Limit |
| --- | --- |
| `AUTH` | 5 TPS |
| `ACCOUNT` | 1 TPS |
| `ASSET` | 5 TPS |
| `STOCK` | 5 TPS |
| `MARKET_INFO` | 3 TPS |
| `MARKET_DATA` | 10 TPS |
| `MARKET_DATA_CHART` | 5 TPS |
| `ORDER` | 6 TPS, 3 TPS during 09:00-09:10 KST |
| `ORDER_HISTORY` | 5 TPS |
| `ORDER_INFO` | 6 TPS, 3 TPS during 09:00-09:10 KST |

BFF implementation requirements:

- Respect `Retry-After` on 429.
- Surface `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` in internal diagnostics, not raw UI copy.
- Cache token issuance and do not call `POST /oauth2/token` per user action.
- Apply per-group throttling before requests reach Toss.

## Fixture Set

Mock responses live under `docs/fixtures/toss/`:

- `status.json`
- `accounts.json`
- `portfolio.json`
- `buying-power.json`
- `order-preview.json`
- `error.json`

These fixtures are safe demo data and should be used for Phase 2 UI/BFF tests before real Toss credentials are connected.

## Phase 1 Acceptance Criteria

- Official source-of-truth URLs are captured.
- Auth, account header, base URL, endpoint groups, and rate limits are documented.
- Browser direct Toss calls are rejected as an architecture decision.
- BFF endpoint contract is defined before implementation.
- Read-only first behavior and order lock are documented.
- Internal normalized fixtures are committed.

## Phase 2 Entry Checklist

- Decide local secret storage mechanism for BFF credentials.
- Add server env/config keys without committing secrets.
- Implement BFF token cache and `/api/toss/probe`.
- Replace hidden Flutter Toss direct settings with a BFF connection status UI.
- Add tests using the fixture set first, then add optional real-credential smoke tests guarded by env vars.
