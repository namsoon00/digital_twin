# Python Service Architecture

The Python service is the migration target for account-scale monitoring, scheduling, and strategy model development.

## Structure

- `python_service/digital_twin/config.py`: local env, settings, multi-account registry
- `python_service/digital_twin/providers.py`: Toss portfolio adapter and demo fallback
- `python_service/digital_twin/analytics.py`: portfolio summary, decision scoring, safe strategy formulas
- `python_service/digital_twin/monitor.py`: realtime anomaly detection and per-message cadence
- `python_service/digital_twin/scheduler.py`: long-running realtime scheduler
- `python_service/digital_twin/notifiers.py`: console and Telegram delivery
- `python_service/digital_twin/cli.py`: account and monitor CLI

## Local Commands

```bash
npm run python:accounts -- list
npm run python:accounts -- add --id main --label "Main" --client-id "$TOSS_CLIENT_ID" --client-secret "$TOSS_CLIENT_SECRET" --account-seq "$TOSS_ACCOUNT_SEQ" --watchlist "NVDA,005930"
npm run python:monitor:once -- --dry-run --force
npm run python:monitor:status
npm run python:monitor:watch
npm run python:service:start
npm run python:service:status
npm run python:service:restart
npm run python:service:stop
```

The registry is stored in `data/accounts.json` with `0600` permissions and is ignored by git.

If `data/accounts.json` does not exist, the service falls back to the existing single-account settings from `data/settings.json` or `.env.local`.

`python:monitor:watch` runs in the foreground. The `python:service:*` commands run the same scheduler in the background with `data/python-monitor.pid` and `data/python-monitor.log`.

## Monitoring Model

Each account produces an independent snapshot:

- connection status
- holdings
- portfolio cash/sector exposure
- per-position decision
- previous snapshot comparison

The monitor emits account-scoped events for:

- heartbeat
- connection changes
- new/removed holdings
- quantity changes
- P/L rate moves
- market value moves
- market cash ratio moves
- decision score changes
- holding timing checks

Cadence is stored per account, rule, and symbol in `data/python-monitor-state.json`.

## Model Development

`analytics.py` includes a safe formula evaluator. It supports arithmetic, numeric variables, and these functions only:

- `min`
- `max`
- `abs`
- `round`

This lets user formulas evolve without allowing arbitrary Python execution.

The current service intentionally uses only Python standard library modules so the existing CI can run without installing packages. The next migration step can add `pandas`/`numpy` behind an optional model lab once the service boundary is stable.
