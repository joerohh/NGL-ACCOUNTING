/**
 * Dev launcher — clears ELECTRON_RUN_AS_NODE before spawning Electron.
 *
 * VS Code and some npm setups inject ELECTRON_RUN_AS_NODE=1 into the
 * shell environment. This env var is read at the C++ layer during Electron
 * startup, making it run as plain Node.js (no app, no BrowserWindow).
 * We must clear it BEFORE the electron process starts.
 */
const { spawn } = require("child_process");
const path = require("path");

const electronPath = require("electron");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electronPath, ["."], {
  cwd: __dirname,
  stdio: "inherit",
  env,
});

child.on("exit", (code) => process.exit(code ?? 0));
