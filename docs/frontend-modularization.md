# Frontend Module Architecture

## Maintained Source and Deployment Artifact

`public/modules/bootstrap.mjs` is the five-line entry point. It initializes the
feature state slices, then starts the existing vanilla-JavaScript application.
The maintained source is 177 native ES modules, not `public/app.js`.

`public/app.js` remains a large, readable, generated classic-script bundle
(approximately 2.1 MB). `scripts/build-frontend.cjs` uses pinned esbuild 0.25.10
with static imports, without minification or tree shaking. This preserves the
existing deployment and script-loading contract. This migration does not claim
smaller downloads, lazy loading, or removal of old feature renderers.

The modules execute independently in the browser test's native-ESM mode. There
is no concatenated source runtime, `eval`, namespace import, aggregate mutable
`state` export, context bag, or global function service locator. Existing vendor
and companion scripts still expose their documented `window` APIs. Named
imports identify each dependency; helpers used only within one file are private.

## Ownership

| Owner | Responsibility |
| --- | --- |
| `state/` | Seventeen feature state slices, initialization, and local snapshot storage. No aggregate state object is exported. |
| `requests/` | Private JSON transport, path-sensitive bounded response cache, deduplication, latest-request lanes, silent activity scopes, mutation adapter. |
| `navigation/` | URL parsing/writing, deep-link compatibility, tab/detail navigation, scroll memory, view/layout lifetime, infinite-list observer adapter. |
| `render/` | Render scheduling, in-place DOM reconciliation, preservation of active controls and scroll state. |
| `shell/` | Startup, root element, layout, network indicators, install/service-worker registration, generic/delegated action dispatch. |
| `accounts/` | Account identity, selection, watchlists, account controls and detail views. |
| `market/`, `research/`, `instruments/` | Market/read-model projections, research evidence, symbol search, instrument detail/valuation/timeline and chart ownership. |
| `decisions/` | Case list/detail/history/reasoning, decision presentation, strategy views and case-tab navigation. |
| `notifications/` | Inbox paging and filters, job detail/AI review, notification settings and templates. |
| `ontology/` | Catalog, inference, audit, macro/strategy projection and graph-engine lifetime. |
| `portfolio/` | Account-scoped portfolio read models, interpretation, projections and workspace/detail views. |
| `calendar/`, `experiments/`, `hypotheses/`, `proposals/` | Their respective data loaders, controls, selectors and views. |
| `overview/`, `operations/`, `settings/` | Dashboard, operational status, settings and preferences. |
| `realtime/`, `snapshot/`, `shared/` | Event handling, snapshot refresh, formatting and small DOM/text helpers. |

`decisions/case-detail.mjs`, `case-summary.mjs` and `case-reasoning.mjs` own
case rendering previously interleaved with notification renderers.
`ontology/macro-view.mjs` and `ontology/strategy.mjs` own ontology projections
previously interleaved with strategy/proposal rendering. These moves preserved
the current function bodies, including subsequent manual fixes.

State initialization evaluates all original field initializers before assigning
the feature slices. This preserves the former initializer-time behavior where
the original `state` variable was not yet assigned. Features import only the
slices they use. Narrow runtime cells retain original shared scalar semantics
where multiple modules still use a scalar; they are not a copy of every global.

## Explicit Runtime Contracts

- `createJsonClient({fetch, begin, end, record, ...})` owns its maps privately.
  Reads expose `request`, `active`, cache invalidation and disposal, not maps.
  Cache identity includes the actual URL as well as the caller's logical key.
  Invalidated or superseded requests cannot repopulate the cache.
- `createLatestRequestLane()` returns request operations with `current()`,
  `track()` and `finish()`. Notification/research/universe query results and
  account-scoped portfolio/decision loads publish only while current.
- `createViewLifetime()` owns cleanup callbacks and generation predicates.
  Navigation invalidates pending layout work even for A -> B -> A routes.
- `createInfiniteListObserver()` accepts a root, observer constructor and
  prefetch distance. Detached, duplicate and late observations are ignored;
  the adapter owns disconnect/rebind across renders.
- `createPanelScrollMemory()` retains each case tab's scroll offset in a
  DOM-scoped WeakMap. Switching through shorter panels does not discard the
  previous tab's offset. Same-panel asynchronous updates keep current scroll.
- Timeline and graph modules own engine creation, resize observers, delayed
  callbacks and destruction. Timeline keys include account, symbol and range;
  legacy symbol/range event deep links are normalized on lookup. Stable chart
  container/payload/theme triples are reused, retaining zoom across renders.

`navigation/detail.mjs` and the shell renderer are composition points: they
explicitly select feature view/load functions. They necessarily import more
features than leaf modules. Original cross-feature dependencies and ESM cycles
remain; this is not a claim of independently deployable bounded contexts.
State slices are still mutable objects, not immutable stores or strict write
capabilities. Several existing feature files are large. Further decomposition
should follow use cases, not introduce a shared context to conceal dependencies.

## Build and Cache Contract

Run `npm run frontend:build` after editing maintained modules or core assets.
The `modules-<16 hex>` release hashes all module bytes, core asset bytes,
normalized HTML/service-worker references, the Cloudflare header file and the
build script itself (including compiler pin/options). Generated version strings
are normalized out of the inputs so repeated builds do not change the hash.

One release appears in the bundle banner/registration, HTML asset queries,
service-worker precache URLs and cache name. `npm run frontend:check` rejects a
stale bundle, HTML or worker. The worker does not cache APIs or WebSockets and
uses network-first navigation/mutable assets, including native module paths.

`public/_headers` supplies no-cache/no-store rules for Cloudflare Pages static
delivery. Cloudflare Tunnel origin headers are backend-owned; this file does
not configure a tunnel or purge a deployed cache. Parent integration must verify
the actual local/shared origin after managed restart. No live cache purge or
deployment was performed by this frontend task.

## Validation and Dependencies

```sh
npm run frontend:build
npm run frontend:check
npm run frontend:test
npm run frontend:test:browser
# Parent integration gates, after source-stable handoff:
npm test
npm run test:full
```

Build requires `esbuild@0.25.10`. Structural/parity checks require
`acorn@8.15.0` and `eslint-scope@8.4.0`. Unit tests use only Node's test runner.
Browser tests require Playwright plus Chromium or installed Google Chrome;
they discover the bundled Codex Playwright through `frontend-toolchain.cjs`.
`FRONTEND_NODE_MODULES` can point at another dependency directory and
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select a browser. No runtime dependency or
framework was added. Package scripts/dependency pins are parent-owned.

`scripts/extract-frontend-modules.cjs` is archival migration tooling, NOT a
build step. It requires the exact original IIFE and `acorn-walk@8.3.4`, refuses
to overwrite `public/modules`, and must not be rerun after deleting those
sources: that would lose the maintained fixes and ownership refinements.
`frontend-extraction-manifest.json` records baseline, not current, ownership.

The structural check resolves imports/unbound names and compares normalized
function token hashes against that baseline. Of 1,737 original functions,
1,736 have baseline records (the monolithic action binder was decomposed).
1,694 retain token parity; 42 deliberate changes have reasons in
`frontend-behavior-changes.json`. This is regression evidence, not proof of
whole-application semantic equivalence. Dependency wiring and asynchronous
behavior require the additional browser tests.

`readFrontendContractSource()` is an inspection-only normalization helper for
legacy text checks. It puts the shell catalog first to avoid local `tabs`
variables confusing old text searches. It must not be executed as an app or
used for bundle/version checks. Asset-version assertions inspect the actual
built bundle. Infinite-observer assertions now inspect the private observer
contract and its injected prefetch distance, not the old inline expression.

## Browser Evidence and Limits

`scripts/test-frontend-browser.cjs` serves real public modules/bundle/CSS/vendor
assets on an ephemeral loopback HTTP server. All API data is synthetic, labeled
MOCK where supported, and external requests are blocked. The fixture matches
the timeline contract with 41 candles and a 41-record MOCK source. It never
reads environment credentials or account stores, invokes Python, sends real
notifications, or uses a live account. The private browser/server are closed.

The existing `renderInstrumentTimelineSources` and `renderInstrumentChart`
functions hardcode `ACTUAL` badges for non-static timelines. Their function
bodies retain pre-migration parity, so those badges also appear with the MOCK
fixtures. The fixture's source text and candle counts are explicit, but not all
visible provenance labels identify synthetic data correctly. This is a
pre-existing UI limitation, not evidence that these tests used actual market
data; changing provenance rendering is outside this modularization's scope.

Desktop (1440 x 1000) and mobile (390 x 844) exercise both module and bundle
modes: actual navigation and case-tab handlers, measured append/return scroll,
retained row identity, unique appended rows, delayed legacy detail close/return,
chart engine/pixel/count/cleanup checks, and light/dark screenshots. The
latest-query race and AI-review rendering additionally use native-module
imports, not a recreated test implementation. Unit tests directly import the
production request/lifetime/observer/panel-scroll modules.

Screenshots and measured results are written to
`/tmp/orbit-frontend-screenshots` (override with `FRONTEND_SCREENSHOTS`).
`browser-validation.json` identifies the fixture and records measurements.
Representative files are `bundle-desktop.png`, `modules-desktop-dark.png`,
`bundle-chart.png`, `modules-dark-chart.png`,
`bundle-mobile-retained-scroll.png`, `modules-mobile-case-scroll.png`, and
`modules-mobile-inbox-top.png`.

Service workers are deliberately blocked in these fixture browser contexts.
Offline update behavior, deployed Cloudflare caching, live WebSockets,
actual read-only authorization enforcement, every backend response shape and
all optional graph/detail workflows still require parent/live integration
verification. URL auth query preservation and viewer UI restrictions are
fixture-tested, not a security audit. No shared runtime was restarted and no
commit, push or handoff notification was performed by this task.
