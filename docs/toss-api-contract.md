# Toss API Contract Phase 1

Last reviewed: 2026-06-25 KST

This document defines the first implementation contract for integrating Toss Securities Open API into Digiter Twin.

Current product direction: personal native app first. The user enters their own Toss Open API credentials, and the native app stores sensitive values in the device secure storage layer. Flutter Web still must not call Toss directly.

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
- No public endpoint for reading the user's Toss app watchlist was present in the official OpenAPI document reviewed here. Digiter Twin manages watchlist symbols as app-local user settings and can pass those symbols to its own BFF query, but it does not claim to sync Toss app watchlists.
- The `수급` screen is designed around the market data group: current price and price change from current-price/candle style responses, trade strength and buy/sell prints from trades, bid/ask imbalance from orderbook, and moving-average indicators calculated locally from Toss daily candles. Toss OpenAPI returns OHLCV candles, not `ma20`/`ma60` fields, so Digiter Twin owns moving-average calculation. Until live mappings are connected, Web mock mode uses app-local manual signal input with the same normalized field names.
- Fair value, buy score, and sell score are user-configurable formulas in the client. The app ships a recommended default formula set, but user-defined weights and threshold values are treated as local strategy settings, not Toss raw data.

## Product Decisions

- Flutter Web never stores `client_secret`, Toss access tokens, or account headers.
- Flutter Web never calls `https://openapi.tossinvest.com` directly.
- Personal native builds may call Toss directly after the user enters their own `client_id` and `client_secret`.
- Native builds store `client_id`, `client_secret`, and optional access tokens in iOS Keychain / Android Keystore through `flutter_secure_storage`.
- The app owns OAuth token issuance, token caching, Toss account selection, rate-limit handling, and error normalization for personal native mode.
- Public web or multi-user service mode must use the BFF contract below instead of native direct calls.
- Phase 2 starts read-only. Order endpoints remain locked behind an explicit feature flag.
- The app treats every decimal money/quantity value as a string at the API boundary to avoid precision loss.
- The app accepts unknown enum values from Toss and renders them as unsupported/unknown instead of crashing.
- Strategy formulas stay local by default. They can reference normalized market/account variables, but they must not trigger order creation by themselves.

## Personal Native Flow

1. User installs the app on a trusted personal iOS/Android device.
2. User enters Toss Open API `client_id` and `client_secret`.
3. The app stores sensitive values in device secure storage, not SharedPreferences.
4. The app calls `POST /oauth2/token` and receives an access token.
5. The app calls read-only Toss endpoints first, starting with `GET /api/v1/accounts`.
6. Account-scoped calls add `X-Tossinvest-Account: {accountSeq}`.
7. Order creation, modification, and cancellation stay disabled until a separate order-safety phase.

Personal native mode reduces shared-service risk but does not make secrets impossible to steal from a compromised device. Users must be able to delete local credentials and revoke/reissue Toss credentials.

## BFF Endpoint Contract

The BFF contract remains the target for Flutter Web, public previews, or any future multi-user service. All endpoints below are served by Digiter Twin, not Toss. Paths are intentionally app-oriented so the frontend does not depend on Toss raw schema names.

| Method | Path | Purpose | Toss mapping | Phase |
| --- | --- | --- | --- | --- |
| `GET` | `/api/toss/status` | Return connection/configuration status without secrets | Local config + token cache metadata | 2 |
| `POST` | `/api/toss/connect` | Save encrypted client credentials and optional default account | `POST /oauth2/token`, `GET /api/v1/accounts` | 2 |
| `POST` | `/api/toss/probe` | Verify credentials and account header | `POST /oauth2/token`, optional `GET /api/v1/accounts` | 2 |
| `GET` | `/api/toss/accounts` | List user accounts with masked display numbers | `GET /api/v1/accounts` | 2 |
| `GET` | `/api/toss/portfolio` | Return holdings plus overview totals | `GET /api/v1/holdings` | 2 |
| `GET` | `/api/toss/positions` | Return normalized position rows | `GET /api/v1/holdings` | 2 |
| `GET` | `/api/toss/market-signals?symbols=AAPL,005930` | Return normalized trade strength, volume, buy/sell pressure, and orderbook imbalance | market data: current prices, trades, orderbook, candles | 2 |
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

### `TossMarketSignal`

```json
{
  "symbol": "005930",
  "name": "삼성전자",
  "currency": "KRW",
  "currentPrice": "72000",
  "priceChangeRate": "2.1",
  "tradeStrength": "118",
  "volumeRatio": "1.8",
  "buyVolume": "620000",
  "sellVolume": "480000",
  "bidAskImbalance": "18",
  "ma20": "69000",
  "ma60": "66000",
  "ma20Distance": "4.3",
  "ma60Distance": "9.1",
  "source": "toss-market-data",
  "syncedAt": "2026-06-25T09:05:00+09:00"
}
```

`tradeStrength` is interpreted around `100` as the neutral line. `bidAskImbalance` is positive when bid-side quantity is stronger and negative when ask-side quantity is stronger. These fields feed the Web `수급` tab together with valuation assumptions; they do not create orders.

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
- Personal native direct calls are allowed only with user-provided credentials stored in device secure storage.
- BFF endpoint contract is defined before implementation.
- Read-only first behavior and order lock are documented.
- Internal normalized fixtures are committed.

## Phase 2 Entry Checklist

- Store personal native credentials in device secure storage.
- Keep Toss settings hidden in Flutter Web builds.
- Implement official OAuth token issuance for `/oauth2/token`.
- Probe `GET /api/v1/accounts` before any account-scoped endpoint.
- Add tests using the fixture set first, then add optional real-credential smoke tests guarded by env vars.
