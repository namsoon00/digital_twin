const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { frontendDependency } = require("./frontend-toolchain.cjs");
const { sourceFiles } = require("./build-frontend.cjs");
const { tokenHash } = require("./frontend-source-contract.cjs");
const manifest = require("./frontend-extraction-manifest.json");
const acorn = frontendDependency("acorn");
const scope = frontendDependency("eslint-scope");
const root = path.resolve(__dirname, "..");
const files = sourceFiles(path.join(root, "public/modules"));
const functions = new Map();
const globals = new Set([...Object.getOwnPropertyNames(globalThis), "window", "document", "navigator", "ResizeObserver", "__FRONTEND_ASSET_VERSION__"]);
const failures = [];
for (const file of files) {
  const code = fs.readFileSync(file, "utf8");
  const ast = acorn.parse(code, { ecmaVersion: 2022, sourceType: "module", ranges: true });
  for (const node of ast.body) {
    const declaration = node.declaration || node;
    if (declaration.type === "FunctionDeclaration") {
      if (functions.has(declaration.id.name)) failures.push("Duplicate function: " + declaration.id.name);
      functions.set(declaration.id.name, { file, hash: tokenHash(code.slice(declaration.start, declaration.end)) });
    }
    if (node.type === "ImportDeclaration") {
      assert(!node.specifiers.some(s => s.type === "ImportNamespaceSpecifier"), "Namespace/service-locator import: " + file);
      assert(node.source.value.startsWith("."), "Non-local runtime dependency: " + file);
      assert(fs.existsSync(path.resolve(path.dirname(file), node.source.value)), "Missing module: " + node.source.value);
    }
  }
  const scopes = scope.analyze(ast, { ecmaVersion: 2022, sourceType: "module" });
  for (const reference of scopes.globalScope.through) {
    if (!globals.has(reference.identifier.name)) failures.push(path.relative(root, file) + ": unresolved " + reference.identifier.name);
  }
}
const changed = [];
for (const before of manifest.preservedFunctions) {
  const after = functions.get(before.name);
  if (!after || after.hash !== before.tokenSha256) changed.push(before.name);
}
const approved = new Set(require("./frontend-behavior-changes.json").flatMap(row => row.functions));
const unexpected = changed.filter(name => !approved.has(name));
if (unexpected.length) failures.push("Unreviewed extraction changes: " + unexpected.join(", "));
if (failures.length) {
  console.error([...new Set(failures)].join("\n")); process.exitCode = 1;
} else console.log(`${files.length} modules checked; ${manifest.preservedFunctions.length - changed.length} functions retain token parity; ${changed.length} intentional changes.`);

if (process.argv.includes("--changes")) console.log(JSON.stringify(changed, null, 2));
