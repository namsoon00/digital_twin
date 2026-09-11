// ARCHIVAL one-time migration, not a build command. Never run against the bundle.
// Deleting maintained modules to rerun this would discard all subsequent fixes.
// See docs/frontend-modularization.md; normal development uses build-frontend.cjs.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { frontendDependency } = require("./frontend-toolchain.cjs");
const acorn = frontendDependency("acorn");
const walk = frontendDependency("acorn-walk");
const scope = frontendDependency("eslint-scope");
const input = process.argv[2];
if (!input) throw new Error("Usage: node scripts/extract-frontend-modules.cjs /path/to/original-app.js");
const original = fs.readFileSync(input, "utf8");
if (!original.startsWith("(function () {")) throw new Error("Expected the original IIFE source.");
const destination = path.resolve("public/modules");
if (fs.existsSync(destination)) throw new Error("Refusing to overwrite maintained module sources.");
const parse = code => acorn.parse(code, { ecmaVersion: 2022, ranges: true, locations: true });
const initial = parse(original).body[0].expression.callee;

// These are semantic feature/subfeature boundaries, not arbitrary line chunks.
const ranges = [
  [788, "shared/text"], [835, "shell/network-activity"], [1078, "requests/json"],
  [1159, "instruments/timeline"], [1574, "state/storage"], [1678, "notifications/recipient"],
  [1689, "requests/mutations"], [1721, "realtime/events"], [1948, "shell/snackbar"],
  [2003, "settings/preferences"], [2076, "shell/install"], [2166, "shell/static-preview"],
  [2213, "navigation/routes"], [2952, "navigation/scroll"], [2974, "navigation/infinite-list"],
  [3048, "navigation/scroll"], [3314, "navigation/chrome"], [3405, "navigation/tab-strip"],
  [3650, "navigation/preload"], [3825, "navigation/router"], [3924, "settings/storage"],
  [3987, "notifications/policy"], [4226, "notifications/requests"], [4526, "notifications/policy"],
  [4749, "notifications/template-preview"], [5440, "notifications/policy"],
  [5682, "accounts/commands"], [6055, "settings/requests"], [6304, "ontology/requests"],
  [6469, "settings/language"], [6608, "ontology/requests"], [6688, "portfolio/lifecycle"],
  [6742, "ontology/requests"], [7281, "experiments/requests"], [7319, "accounts/identity"],
  [7323, "overview/requests"], [7349, "market/requests"], [7380, "portfolio/requests"],
  [7515, "operations/requests"], [7556, "decisions/requests"], [7752, "experiments/requests"],
  [7977, "proposals/requests"], [8025, "hypotheses/workspace"], [9013, "proposals/requests"],
  [9122, "instruments/catalog"], [9388, "instruments/universe"], [9825, "instruments/suggestions"],
  [9952, "accounts/commands"], [9990, "snapshot/requests"], [10005, "research/requests"],
  [10246, "calendar/commands"], [10836, "shell/static-preview"], [10874, "shared/format"],
  [11029, "settings/formulas"], [11297, "shared/format"], [11317, "decisions/signals"],
  [12060, "notifications/alerts"], [12265, "snapshot/requests"], [12408, "shell/forms"],
  [12456, "render/reconcile"], [12700, "render/scheduler"], [12974, "shell/layout"],
  [13090, "navigation/detail"], [13421, "portfolio/detail"], [13485, "ontology/detail"],
  [13534, "decisions/model"], [13764, "ontology/inference"], [14100, "notifications/detail-links"],
  [14128, "accounts/detail"], [14164, "notifications/detail-links"], [14200, "decisions/detail-links"],
  [14229, "ontology/catalog"], [14548, "ontology/detail"], [14562, "proposals/detail"],
  [14571, "ontology/detail"], [14585, "experiments/detail"], [14621, "market/detail"],
  [14631, "ontology/graphs"], [14660, "shell/navigation"], [14864, "navigation/palette"],
  [14955, "shell/navigation"], [14969, "shared/console"], [15211, "portfolio/selectors"],
  [15238, "market/selectors"], [15351, "decisions/selectors"], [15531, "notifications/selectors"],
  [15566, "overview/workspace"], [15766, "market/workspace"], [15896, "decisions/workspace"],
  [16288, "notifications/inbox"], [16352, "decisions/workspace"], [16420, "experiments/validation"],
  [16512, "settings/workspace"], [16615, "instruments/workspace"], [17136, "portfolio/workspace"],
  [17149, "operations/workspace"], [17165, "shell/pages"], [17191, "calendar/workspace"],
  [18016, "operations/guide"], [18101, "ontology/audit"], [18327, "operations/guide"],
  [18619, "shell/commands"], [18718, "accounts/identity"], [18728, "shell/commands"],
  [19054, "decisions/workspace"], [19063, "experiments/workspace"],
  [20100, "proposals/workspace"], [20700, "decisions/strategy"],
  [21739, "decisions/today"], [22041, "decisions/actions"], [22254, "decisions/evidence"],
  [22894, "ontology/execution"], [23112, "decisions/strategy"], [23368, "ontology/world"],
  [23993, "shell/commands"], [24041, "overview/legacy"], [24136, "accounts/watchlist"],
  [24257, "realtime/labels"], [24278, "accounts/directory"], [24571, "operations/monitoring"],
  [24688, "accounts/balance"], [25000, "portfolio/lifecycle"], [25167, "accounts/editor"],
  [25394, "notifications/workspace"], [25800, "notifications/history"],
  [26700, "notifications/reasoning"], [27555, "notifications/detail"], [28000, "notifications/editor"],
  [28421, "decisions/legacy"], [28620, "ontology/graphs"], [30155, "ontology/governance"],
  [30484, "decisions/legacy"], [30636, "notifications/alerts-view"], [30750, "settings/fields"],
  [30839, "portfolio/valuation"], [31026, "operations/monitoring"], [31347, "instruments/universe-view"],
  [31811, "market/feed"], [32813, "market/settings"], [33148, "research/quality"],
  [33663, "research/workspace"], [34133, "settings/fields"], [34320, "settings/legacy"],
  [35105, "settings/share"], [35214, "settings/legacy"], [35331, "shell/delegated-actions"],
  [35702, "shell/actions"]
];
function ownerAt(line) { return ranges.filter(([start]) => start <= line).at(-1)?.[1] || "shell/catalog"; }
const stateOwner = key => {
  if (/^(activeTab|previousTab|tabBar|tabScroll|pageView|workDetail|monitoringDetail|commandPalette)/.test(key)) return "navigation";
  if (/^(instrument)/.test(key)) return "instruments";
  if (/^(investmentCalendar|calendarEntry)/.test(key)) return "calendar";
  if (/^(notification|messageSchedules|activeNotification)/.test(key)) return "notifications";
  if (/^(researchEvidence|expandedResearch|expandedFeed)/.test(key)) return "research";
  if (/^(marketRead|consoleMarket|marketWorkspace|activeFeed)/.test(key)) return "market";
  if (/^(portfolio|activePortfolio)/.test(key)) return "portfolio";
  if (/^(serviceAccount|account|editingAccount|activeAccount|activeWatch|editingWatch|watchlist|watchSuggest)/.test(key)) return "accounts";
  if (/^(symbolUniverse|activeSymbol)/.test(key)) return "universe";
  if (/^(hypothesis|activeHypothesis)/.test(key)) return "hypotheses";
  if (/^(ontologyExperiment|activeExperiment|activeOntologyExperiment)/.test(key)) return "experiments";
  if (/^(strategyProposal|activeStrategyProposal)/.test(key)) return "proposals";
  if (/^(ontology|activeOntology|expandedOntology|activeInvestmentGraph)/.test(key)) return "ontology";
  if (/^(investmentFlow|investmentCase|investmentModel|investmentAction|activeInvestment|expandedInvestment|consoleDecision|activeStrategy)/.test(key)) return "decisions";
  if (/^(settings|server|share|runtimeIdentity|showSecrets|activeSettings|investmentLanguage|activeInvestmentLanguage)/.test(key)) return "settings";
  if (/^(operations|activeOperations)/.test(key)) return "operations";
  return "shell";
};
function actionOwner(code) {
  const selector = code.match(/\[data-([^\]"' =]+)/)?.[1] || code.match(/var (\w+)/)?.[1] || "shell";
  if (/notification|message|alert/i.test(selector)) return "notifications";
  if (/calendar/i.test(selector)) return "calendar";
  if (/hypothesis/i.test(selector)) return "hypotheses";
  if (/experiment/i.test(selector)) return "experiments";
  if (/proposal/i.test(selector)) return "proposals";
  if (/ontology|rulebox/i.test(selector)) return "ontology";
  if (/account|watch/i.test(selector)) return "accounts";
  if (/symbol/i.test(selector)) return "instruments";
  if (/feed|research/i.test(selector)) return "research";
  if (/investment|strategy|decision/i.test(selector)) return "decisions";
  if (/setting|template|formula|number|boolean|quiet/i.test(selector)) return "settings";
  return "shell";
}

// Split independent event-binding blocks, retaining declaration/use components.
const actions = initial.body.body.find(n => n.id?.name === "bindActions");
const statements = actions.body.body.slice(2);
const initialScopes = scope.analyze(parse(original), { ecmaVersion: 2022 });
const actionScope = initialScopes.scopes.find(s => s.block.start === actions.start);
const parents = statements.map((_, i) => i);
const find = i => parents[i] === i ? i : (parents[i] = find(parents[i]));
const indexOf = node => statements.findIndex(s => s.start <= node.start && node.end <= s.end);
for (const variable of actionScope.variables) {
  if (variable.name === "app") continue;
  const indices = [...variable.identifiers, ...variable.references.map(r => r.identifier)].map(indexOf).filter(i => i >= 0);
  for (const i of indices) parents[find(i)] = find(indices[0]);
}
const groups = new Map();
statements.forEach((s, i) => { const k = find(i); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(s); });
const bindings = new Map();
for (const group of groups.values()) {
  const text = group.map(n => original.slice(n.start, n.end)).join("\n");
  const owner = actionOwner(text);
  if (!bindings.has(owner)) bindings.set(owner, []);
  bindings.get(owner).push(...group);
}
const extraOwners = new Map();
let actionSource = "function bindActions(app) {\napp = app || document.getElementById(\"app\");\nif (!app) return;\n";
for (const owner of bindings.keys()) actionSource += `bind${owner[0].toUpperCase() + owner.slice(1)}Controls(app);\n`;
actionSource += "}\n";
for (const [owner, nodes] of bindings) {
  const name = `bind${owner[0].toUpperCase() + owner.slice(1)}Controls`;
  extraOwners.set(name, `${owner}/actions`);
  actionSource += `function ${name}(app) {\n` + nodes.sort((a, b) => a.start - b.start).map(n => original.slice(n.start, n.end)).join("\n") + "\n}\n";
}
let source = original.slice(0, actions.start) + actionSource + original.slice(actions.end);
const ast = parse(source);
const body = ast.body[0].expression.callee.body.body;
const scopes = scope.analyze(ast, { ecmaVersion: 2022 });
const global = scopes.scopes.find(s => s.block === ast.body[0].expression.callee);
const records = [];
const byName = new Map();
const stateNode = body.find(n => n.type === "VariableDeclaration" && n.declarations[0].id.name === "state");
const stateFields = new Map(stateNode.declarations[0].init.properties.map(p => [p.key.name, stateOwner(p.key.name)]));
const runtimeVars = new Set(["ontologyGraphInstances", "instrumentTimelineChart", "instrumentTimelineChartObserver", "instrumentTimelineChartFrame", "mobileInfiniteScrollObserver", "mobileInfiniteScrollMode", "overlayScrollPosition", "deferredInstallPrompt", "appServiceWorkerRegistration", "serviceWorkerReloadPending", "pendingTabTransition", "pendingScrollableTabReveal", "scrollableTabRevealFrame", "tabDataPreloadPrerequisitesReady", "tabDataPreloadRequestStarting", "renderSuppressionDepth", "renderQueuedDuringSuppression", "dashboardRegionReplacementOccurred", "symbolUniverseRefreshCollapseTimer"]);
const varOwners = {
  app: "shell/root", defaultSettings: "settings/defaults", cachedSnapshot: "state/initialize",
  ontologyGraphInstances: "ontology/runtime", instrumentTimelineChart: "instruments/runtime", instrumentTimelineChartObserver: "instruments/runtime", instrumentTimelineChartFrame: "instruments/runtime",
  mobileInfiniteScrollObserver: "navigation/infinite-runtime", mobileInfiniteScrollMode: "navigation/infinite-runtime", overlayScrollPosition: "navigation/overlay-runtime",
  deferredInstallPrompt: "shell/install-runtime", appServiceWorkerRegistration: "shell/install-runtime", serviceWorkerReloadPending: "shell/install-runtime", appShellStatus: "shell/install-runtime",
  pendingTabTransition: "navigation/transition-runtime", pendingScrollableTabReveal: "navigation/tab-runtime", scrollableTabRevealFrame: "navigation/tab-runtime",
  settingsMemoryStore: "settings/storage", snapshotMemoryStore: "state/storage", researchEvidenceMemoryStore: "research/requests", symbolUniverseMemoryStore: "instruments/universe",
  DEFAULT_RESEARCH_EVIDENCE_LIMIT: "research/constants", DEFAULT_SYMBOL_UNIVERSE_LIMIT: "instruments/constants", INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE: "calendar/constants",
  staticBuildConfigPromise: "shell/static-preview", watchSuggestTimer: "accounts/actions", watchSuggestRequestId: "instruments/suggestions", snackbarTimer: "shell/snackbar",
  realtimeSocket: "realtime/events", realtimeReconnectTimer: "realtime/events", realtimeReloadTimer: "realtime/events", realtimeSeenEventIds: "realtime/events",
  symbolUniverseRefreshPollTimer: "instruments/universe", symbolUniverseRefreshCollapseTimer: "instruments/refresh-runtime", symbolUniverseRefreshFocusTimer: "instruments/universe", symbolUniverseRefreshLoadedJobId: "instruments/universe", symbolUniverseRefreshNotifiedJobId: "instruments/universe",
  snapshotPollTimer: "snapshot/requests", snapshotLoadPromise: "snapshot/requests", snapshotLastCheckedAt: "snapshot/requests", snapshotPollAttempts: "snapshot/requests", SNAPSHOT_RESUME_CHECK_INTERVAL_MS: "snapshot/requests", SNAPSHOT_REFRESH_POLL_LIMIT: "snapshot/requests",
  scheduledRenderFrame: "render/scheduler", pendingRenderTransition: "render/scheduler", renderSuppressionDepth: "render/runtime", renderQueuedDuringSuppression: "render/runtime", dashboardRegionReplacementOccurred: "render/runtime",
  activeJsonRequests: "requests/active", jsonResponseCache: "requests/json", REQUEST_TIMEOUT_MS: "requests/json", JSON_RESPONSE_CACHE_MS: "requests/json",
  networkActivitySequence: "shell/network-activity", networkActivities: "shell/network-activity", networkActivityRevealTimer: "shell/network-activity", pendingNetworkControl: "shell/network-activity", pendingNetworkControlTimer: "shell/network-activity", NETWORK_ACTIVITY_REVEAL_MS: "shell/network-activity",
  cytoscapeLoadPromise: "ontology/graphs", lastPageScrollActivityAt: "navigation/scroll", workDetailReturnFocus: "navigation/detail", delegatedConsoleActionsBound: "shell/delegated-actions", marketSearchTimer: "shell/delegated-actions"
};
for (const n of body) {
  if (n === stateNode) continue;
  if (n.type === "FunctionDeclaration") {
    const name = n.id.name;
    const originalNode = initial.body.body.find(x => x.id?.name === name);
    const owner = extraOwners.get(name) || ownerAt(originalNode?.loc.start.line || 35702);
    const rec = { name, node: n, owner }; records.push(rec); byName.set(name, rec);
  } else if (n.type === "VariableDeclaration" && n.start < actions.start) {
    for (const d of n.declarations) {
      const name = d.id.name;
      const owner = varOwners[name] || (/^tabDataPreload|^TAB_DATA_PRELOAD/.test(name) ? "navigation/preload" : /^appNav|^topbar/.test(name) ? "navigation/chrome" : ownerAt(n.loc.start.line));
      const rec = { name, node: d, owner, variable: true }; records.push(rec); byName.set(name, rec);
    }
  } else records.push({ name: "startup", node: n, owner: "shell/startup", effect: true });
}
// Every lexical dependency is resolved by eslint-scope, including writes.
const rewrites = [];
const references = [];
const stateMembers = new Map();
walk.full(ast, n => { if (n.type === "MemberExpression") stateMembers.set(n.object, n); });
for (const v of global.variables) {
  if (v.name === "state") {
    for (const ref of v.references) {
      if (ref.init) continue;
      const member = stateMembers.get(ref.identifier);
      if (!member) {
        if (![2026, 5707].includes(ref.identifier.loc.start.line)) throw new Error("Unscoped state access at " + ref.identifier.loc.start.line);
        rewrites.push({ start: ref.identifier.start, end: ref.identifier.end, value: "settingsState" });
        references.push({ node: ref.identifier, name: "settingsState", owner: "state/settings" });
        continue;
      }
      if (member.computed) throw new Error("Dynamic state access at " + ref.identifier.loc.start.line);
      const key = member.property.name;
      if (!stateFields.has(key)) stateFields.set(key, stateOwner(key));
      rewrites.push({ start: ref.identifier.start, end: ref.identifier.end, value: stateFields.get(key) + "State" });
      references.push({ node: ref.identifier, name: stateFields.get(key) + "State", owner: "state/" + stateFields.get(key) });
    }
  } else if (byName.has(v.name)) {
    const rec = byName.get(v.name);
    const writes = v.references.filter(r => r.isWrite() && !r.init);
    if (writes.some(r => !records.some(x => x.owner === rec.owner && x.node.start <= r.identifier.start && r.identifier.end <= x.node.end))) runtimeVars.add(v.name);
    for (const ref of v.references) {
      if (ref.init) continue;
      if (runtimeVars.has(v.name)) rewrites.push({ start: ref.identifier.start, end: ref.identifier.end, value: v.name + "Cell.value" });
      references.push({ node: ref.identifier, name: v.name + (runtimeVars.has(v.name) ? "Cell" : ""), owner: rec.owner });
    }
  }
}
function edited(node) {
  let text = source.slice(node.start, node.end);
  for (const edit of rewrites.filter(r => r.start >= node.start && r.end <= node.end).sort((a, b) => b.start - a.start)) {
    text = text.slice(0, edit.start - node.start) + edit.value + text.slice(edit.end - node.start);
  }
  return text.replace(/^  /gm, "");
}
const modules = new Map();
function moduleFor(owner) {
  if (!modules.has(owner)) modules.set(owner, { chunks: [], imports: new Map(), exports: new Set() });
  return modules.get(owner);
}
function dependency(target, owner, name) {
  if (target === owner) return;
  const mod = moduleFor(target);
  if (!mod.imports.has(owner)) mod.imports.set(owner, new Set());
  mod.imports.get(owner).add(name); moduleFor(owner).exports.add(name);
}
function dependencies(rec) {
  for (const ref of references) if (rec.node.start <= ref.node.start && ref.node.end <= rec.node.end) dependency(rec.owner, ref.owner, ref.name);
}
const init = moduleFor("state/initialize");
const effects = [];
for (const rec of records) {
  if (rec.name === "cachedSnapshot") continue;
  dependencies(rec);
  let text = edited(rec.node);
  if (rec.variable) text = runtimeVars.has(rec.name) ? `const ${rec.name}Cell = { value: ${edited(rec.node.init)} };` : "var " + text + ";";
  if (rec.effect) effects.push(text); else moduleFor(rec.owner).chunks.push(text);
}
for (const owner of new Set(stateFields.values())) {
  const fields = stateNode.declarations[0].init.properties.filter(p => stateFields.get(p.key.name) === owner);
  const mod = moduleFor("state/" + owner);
  mod.chunks.push(`const ${owner}State = {};`);
  mod.chunks.push(`function initialize${owner[0].toUpperCase() + owner.slice(1)}State(cachedSnapshot) {\n  return {\n` + fields.map(p => "    " + edited(p)).join(",\n") + "\n  };\n}");
  fields.forEach(node => dependencies({ node, owner: "state/" + owner }));
  mod.imports.delete("state/initialize");
  dependency("state/initialize", "state/" + owner, "initialize" + owner[0].toUpperCase() + owner.slice(1) + "State");
  dependency("state/initialize", "state/" + owner, owner + "State");
}
dependency("state/initialize", "state/storage", "loadCachedSnapshot");
init.exports.delete("cachedSnapshot");
init.chunks.push("function initializeState() {\n  var cachedSnapshot = loadCachedSnapshot();\n  var initial = [\n" + [...new Set(stateFields.values())].map(o => `    [${o}State, initialize${o[0].toUpperCase() + o.slice(1)}State(cachedSnapshot)]`).join(",\n") + "\n  ];\n  initial.forEach(function (entry) { Object.assign(entry[0], entry[1]); });\n}");
moduleFor("shell/startup").chunks.push("function startApplication() {\n" + effects.join("\n") + "\n}");
dependency("bootstrap", "state/initialize", "initializeState");
dependency("bootstrap", "shell/startup", "startApplication");
moduleFor("bootstrap").chunks.push("initializeState();\nstartApplication();");

for (const [owner, mod] of modules) {
  const imports = [...mod.imports].sort(([a], [b]) => a.localeCompare(b)).map(([dep, names]) => {
    let relative = path.posix.relative(path.posix.dirname(owner), dep) + ".mjs";
    if (!relative.startsWith(".")) relative = "./" + relative;
    return `import { ${[...names].sort().join(", ")} } from ${JSON.stringify(relative)};`;
  });
  const output = imports.join("\n") + "\n\n" + mod.chunks.join("\n\n") + (mod.exports.size ? "\n\nexport { " + [...mod.exports].sort().join(", ") + " };" : "") + "\n";
  acorn.parse(output, { ecmaVersion: 2022, sourceType: "module" });
  const file = path.join(destination, owner + ".mjs"); fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, output);
}
const report = {
  baselineSha256: crypto.createHash("sha256").update(original).digest("hex"),
  originalFunctions: initial.body.body.filter(n => n.type === "FunctionDeclaration").length,
  extractedFunctions: records.filter(r => !r.variable && !r.effect).length,
  stateFields: [...stateFields].map(([field, owner]) => ({ field, owner })),
  modules: [...modules].map(([name, m]) => ({ name, exports: m.exports.size, imports: [...m.imports.values()].reduce((n, v) => n + v.size, 0) })),
  preservedFunctions: initial.body.body.filter(n => n.type === "FunctionDeclaration" && n.id.name !== "bindActions").map(n => ({ name: n.id.name, owner: byName.get(n.id.name).owner, sha256: crypto.createHash("sha256").update(original.slice(n.start, n.end)).digest("hex") }))
};
fs.writeFileSync(path.resolve("scripts/frontend-extraction-manifest.json"), JSON.stringify(report, null, 2) + "\n");
console.log(`Extracted ${report.originalFunctions} functions into ${modules.size} ESM modules, ${stateFields.size} state fields. All references statically resolved.`);
