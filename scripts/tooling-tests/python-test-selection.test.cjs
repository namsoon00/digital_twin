const test = require("node:test");
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const entries = JSON.parse(fs.readFileSync(path.join(root, "python_service/tests/suite_manifest.json"), "utf8")).tests;
function cli(args, env = {}) {
  return spawnSync(process.execPath, ["scripts/run-python-tests.js", ...args], {
    cwd: root, env: { ...process.env, PYTHON_BIN: "/nonexistent-test-python", ...env }, encoding: "utf8",
  });
}
function listed(args) {
  const result = cli([...args, "--list"]);
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout).files;
}

test("default selection is narrow while explicit core/full and focused coverage remain available", () => {
  assert.deepEqual(listed([]), entries.filter((entry) => entry.tier === "unit").map((entry) => entry.file));
  assert.deepEqual(listed(["core"]), entries.filter((entry) => entry.core).map((entry) => entry.file));
  assert.deepEqual(listed(["full"]), entries.map((entry) => entry.file));
  assert.deepEqual(listed(["focus", "test_observation_checkpoints", "python_service/tests/test_ai_evidence_wake.py",
    "./python_service/tests/test_observation_checkpoints.py"]), ["test_observation_checkpoints.py", "test_ai_evidence_wake.py"]);
});

test("empty, mistyped, wildcard and out-of-scope selections fail without a broad fallback", () => {
  for (const args of [["focus"], ["focus", "test_missing_file"], ["focus", "test_*.py"],
    ["focus", "../test_storage_durability.py"], ["unit", "test_storage_durability.py"], ["--unknown"]]) {
    const result = cli([...args, "--list"]);
    assert.equal(result.status, 1, JSON.stringify(args));
    assert.doesNotMatch(result.stderr, /ENOENT/, "Invalid selection must fail before starting Python");
  }
});

test("focused execution retains test storage isolation and propagates a failed Python run", (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "orbit-test-runner-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const executable = path.join(dir, "python-stub");
  const capture = path.join(dir, "capture.json");
  fs.writeFileSync(executable, '#!/usr/bin/env node\nif(process.argv[2].endsWith("mysql_test_runtime.py")){if(process.env.TEST_SETUP_FAILURE)process.exit(9);console.log(JSON.stringify({MYSQL_HOST:"127.0.0.1",MYSQL_PORT:"33307",MYSQL_USER:"root",MYSQL_PASSWORD:"",MYSQL_UNIX_SOCKET:"/tmp/test-only.sock",MYSQL_URL:"",DATABASE_URL:""}));process.exit(0);}\nrequire("node:fs").writeFileSync(process.env.TEST_CAPTURE, JSON.stringify({args:process.argv.slice(2), mysql:process.env.MYSQL_DATABASE, mysqlTest:process.env.MYSQL_TEST_DATABASE, port:process.env.MYSQL_PORT,socket:process.env.MYSQL_UNIX_SOCKET,url:process.env.MYSQL_URL,databaseUrl:process.env.DATABASE_URL,typedb:process.env.TYPEDB_DATABASE, address:process.env.TYPEDB_ADDRESS, runtime:process.env.ORBIT_RUNTIME_ENV}));\nprocess.exit(7);\n', { mode: 0o700 });
  const result = cli(["focus", "test_storage_durability"], {
    PYTHON_BIN: executable, TEST_CAPTURE: capture, MYSQL_DATABASE: "must-not-use",
    MYSQL_PORT:"3306",MYSQL_UNIX_SOCKET:"/production/mysql.sock",MYSQL_URL:"private-production-url",DATABASE_URL:"private-production-url",
    MYSQL_TEST_DATABASE: "must-not-use", TYPEDB_DATABASE: "must-not-use", ORBIT_RUNTIME_ENV: "production",
  });
  assert.equal(result.status, 7, result.stderr);
  assert.deepEqual(JSON.parse(fs.readFileSync(capture, "utf8")), {
    args: ["python_service/tests/minimal_suite_runner.py", "--mode", "focus", "test_storage_durability.py"],
    mysql: "orbit_alpha_test", mysqlTest: "orbit_alpha_test", typedb: "orbit_alpha_ontology_test",
    port:"33307",socket:"/tmp/test-only.sock",url:"",databaseUrl:"",
    address: "127.0.0.1:1739", runtime: "test",
  });
  fs.unlinkSync(capture);
  const failed = cli(["focus", "test_storage_durability"], {
    PYTHON_BIN: executable, TEST_CAPTURE: capture, TEST_SETUP_FAILURE:"1",
  });
  assert.equal(failed.status,1);
  assert.match(failed.stderr,/Isolated MySQL test setup failed/);
  assert.equal(fs.existsSync(capture),false,"An unavailable isolated server must not start the suite against the app DB");
});
