const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { frontendDependency } = require("./frontend-toolchain.cjs");
const { sourceFiles } = require("./build-frontend.cjs");
const root = path.resolve(__dirname, "..");
const manifest = require("./frontend-extraction-manifest.json");
const owners = [...new Set(manifest.stateFields.map(row => row.owner + "State"))];

function normalizeReferences(code) {
  return code.replace(new RegExp("\\b(" + owners.join("|") + ")\\b", "g"), "state")
    .replace(/\b(\w+)Cell\.value\b/g, "$1");
}

function tokenHash(code) {
  const tokens = [...frontendDependency("acorn").tokenizer(normalizeReferences(code), { ecmaVersion: 2022 })];
  return crypto.createHash("sha256").update(JSON.stringify(tokens.map(t => [t.type.label, t.value]))).digest("hex");
}

// For legacy text-only contracts. This is inspection input, never executable
// source or a bundle: runtime tests exercise the real module graph separately.
function readFrontendContractSource() {
  const catalog = path.join(root, "public/modules/shell/catalog.mjs");
  const files = sourceFiles(path.join(root, "public/modules"));
  return [catalog, ...files.filter(file => file !== catalog)].map(file => normalizeReferences(fs.readFileSync(file, "utf8"))).join("\n");
}

module.exports = { normalizeReferences, tokenHash, readFrontendContractSource };
