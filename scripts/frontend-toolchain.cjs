const path = require("node:path");

// Runtime dependencies stay optional for serving the checked-in classic bundle.
function frontendDependency(name) {
  const roots = [process.cwd(), process.env.FRONTEND_NODE_MODULES, process.env.NODE_PATH,
    path.join(process.env.HOME || "", ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules")];
  for (const root of roots.filter(Boolean)) {
    try { return require(require.resolve(name, { paths: [root, path.dirname(root)] })); } catch (error) {
      if (error.code !== "MODULE_NOT_FOUND") throw error;
    }
  }
  throw new Error("Missing frontend development dependency: " + name + ". Set FRONTEND_NODE_MODULES to its node_modules directory.");
}

module.exports = { frontendDependency };
