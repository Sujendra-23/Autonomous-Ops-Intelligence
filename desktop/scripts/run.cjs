const { spawn } = require("node:child_process");
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require("electron"), ["."], {
  cwd: require("node:path").join(__dirname, ".."),
  env,
  stdio: "inherit",
});
child.on("exit", (code) => {
  process.exitCode = code || 0;
});
