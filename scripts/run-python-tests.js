const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const testDir = path.join(rootDir, "python_service", "tests");

function availableTestFiles() {
  return fs.readdirSync(testDir)
    .filter((name) => /^test_[a-z0-9_]+\.py$/i.test(name))
    .sort();
}

function testFilesForMode(mode) {
  const all = availableTestFiles();
  if (mode === "core" || mode === "full") return all;
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
  });
  const result = childProcess.spawnSync(
    environment.PYTHON_BIN || "python3",
    ["-m", "unittest"].concat(files.map((name) => path.join("python_service", "tests", name))),
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

module.exports = { availableTestFiles, testFilesForMode };
