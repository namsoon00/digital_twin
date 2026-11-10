const childProcess = require("node:child_process");
const path = require("node:path");

function isolateMysqlTestEnvironment(environment, spawnSync = childProcess.spawnSync) {
  const rootDir = path.resolve(__dirname, "..");
  const result = spawnSync(environment.PYTHON_BIN || "python3", ["python_service/tests/mysql_test_runtime.py"], {
    cwd: rootDir, env: environment, encoding: "utf8", timeout: 65000,
  });
  if (result.error || result.status !== 0) {
    throw new Error("Isolated MySQL test setup failed. " + String(result.stderr || (result.error && result.error.code) || ""));
  }
  const values = JSON.parse(result.stdout);
  for (const key of ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_UNIX_SOCKET", "MYSQL_URL", "DATABASE_URL"]) {
    if (typeof values[key] !== "string") throw new Error("Invalid isolated MySQL test receipt.");
  }
  return Object.assign({}, environment, values);
}

module.exports = { isolateMysqlTestEnvironment };
