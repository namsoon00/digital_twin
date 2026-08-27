const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const testDir = path.join(rootDir, "python_service", "tests");
const manifestPath = path.join(testDir, "suite_manifest.json");
const tiers = new Set(["unit", "contract", "integration", "system"]);

function availableTestFiles() {
  return fs.readdirSync(testDir)
    .filter((name) => /^test_[a-z0-9_]+\.py$/i.test(name))
    .sort();
}

function loadManifest() {
  const parsed = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const entries = Array.isArray(parsed.tests) ? parsed.tests : [];
  const names = new Set();
  for (const entry of entries) {
    if (!entry || typeof entry.file !== "string" || !tiers.has(entry.tier)) {
      throw new Error("Invalid Python test manifest entry: " + JSON.stringify(entry));
    }
    if (names.has(entry.file)) throw new Error("Duplicate Python test manifest entry: " + entry.file);
    names.add(entry.file);
  }
  const discovered = availableTestFiles();
  const declared = Array.from(names).sort();
  if (JSON.stringify(discovered) !== JSON.stringify(declared)) {
    const missing = discovered.filter((name) => !names.has(name));
    const absent = declared.filter((name) => !discovered.includes(name));
    throw new Error(
      "Python test manifest mismatch. Add/remove the file explicitly. " +
      "Undeclared: " + JSON.stringify(missing) + "; missing files: " + JSON.stringify(absent),
    );
  }
  return entries;
}

function testFilesForMode(mode) {
  const entries = loadManifest();
  if (tiers.has(mode)) return entries.filter((entry) => entry.tier === mode).map((entry) => entry.file);
  if (mode === "core") return entries.filter((entry) => entry.core).map((entry) => entry.file);
  if (mode === "full") return entries.map((entry) => entry.file);
  throw new Error("Unknown Python test mode: " + mode);
}

function pythonPath(environment) {
  const entries = [
    path.join(rootDir, "python_service"),
    path.join(rootDir, "python_service", "tests"),
  ];
  if (environment.PYTHONPATH) entries.push(environment.PYTHONPATH);
  return entries.join(path.delimiter);
}

function run(mode) {
  const files = testFilesForMode(mode);
  if (files.length === 0) throw new Error("No Python tests selected for mode: " + mode);
  const environment = Object.assign({}, process.env, {
    PYTHONPATH: pythonPath(process.env),
    ORBIT_RUNTIME_ENV: "test",
    ORBIT_RUNTIME_REVISION: "python-test-suite",
    ORBIT_INFRASTRUCTURE_OVERRIDE_ENABLED: "1",
    MYSQL_DATABASE: "orbit_alpha_test",
    MYSQL_TEST_DATABASE: "orbit_alpha_test",
    TYPEDB_ADDRESS: "127.0.0.1:1739",
    TYPEDB_HTTP_ADDRESS: "127.0.0.1:8010",
    TYPEDB_DATA_PATH: path.join(rootDir, "data", "test-runtime", "typedb-data"),
    TYPEDB_DATABASE: "orbit_alpha_ontology_test",
  });
  const result = childProcess.spawnSync(
    environment.PYTHON_BIN || "python3",
    [
      path.join("python_service", "tests", "minimal_suite_runner.py"),
      "--mode",
      mode,
    ].concat(files),
    { cwd: rootDir, env: environment, stdio: "inherit" },
  );
  if (result.error) throw result.error;
  process.exit(result.status === null ? 1 : result.status);
}

if (require.main === module) {
  try {
    run(process.argv[2] || "core");
  } catch (error) {
    console.error(error && error.message ? error.message : error);
    process.exit(1);
  }
}

module.exports = { availableTestFiles, loadManifest, testFilesForMode };
