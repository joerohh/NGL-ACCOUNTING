/**
 * NGL Accounting — Electron main process.
 *
 * 1. Spawns the bundled Python agent server (PyInstaller .exe)
 * 2. Waits for localhost:8787 to respond
 * 3. Opens a BrowserWindow pointing at the agent's web UI
 * 4. Kills the agent on window close
 */

// Top-level crash catcher — writes to Desktop so we can always find it
const _fs = require("fs");
const _path = require("path");
const _crashLog = _path.join(require("os").homedir(), "Desktop", "ngl-crash.txt");
process.on("uncaughtException", (err) => {
  _fs.writeFileSync(_crashLog, `[${new Date().toISOString()}] UNCAUGHT: ${err.stack}\n`, { flag: "a" });
  process.exit(1);
});

const { app, BrowserWindow, Tray, Menu, nativeImage, dialog, globalShortcut } = require("electron");
const path = _path;
const fs = _fs;
const { spawn } = require("child_process");
const http = require("http");

// ── Debug logging to file ──────────────────────────────────────────
let _logFile = null;
function log(msg) {
  if (!_logFile) {
    try { _logFile = path.join(app.getPath("userData"), "ngl-debug.log"); }
    catch { _logFile = path.join(__dirname, "ngl-debug.log"); }
  }
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try { fs.appendFileSync(_logFile, line); } catch {}
  console.log(msg);
}

// ── Paths ──────────────────────────────────────────────────────────
const isDev = !app.isPackaged;

// In dev: files are right next to this script and ../agent
// In prod: extraResources are in process.resourcesPath
const resourcesPath = isDev ? path.join(__dirname, "..") : process.resourcesPath;
const agentDir = isDev
  ? path.join(resourcesPath, "agent")
  : path.join(resourcesPath, "agent");
const appDir = isDev
  ? path.join(resourcesPath, "app")
  : path.join(resourcesPath, "webapp");
const iconPath = path.join(appDir, "assets", "images", "ngl-desktop.ico");

// Agent executable — PyInstaller output
const agentExe = isDev
  ? null // In dev mode we run `python main.py` directly
  : path.join(agentDir, "ngl-agent", "ngl-agent.exe");

const AGENT_PORT = 8787;
const AGENT_URL = `http://localhost:${AGENT_PORT}`;

let mainWindow = null;
let tray = null;
let agentProcess = null;
let isQuitting = false;

// ── Agent lifecycle ────────────────────────────────────────────────

function startAgent() {
  log(`isDev=${isDev} agentExe=${agentExe} agentDir=${agentDir} appDir=${appDir}`);
  log(`agentExe exists: ${agentExe ? fs.existsSync(agentExe) : "N/A"}`);
  if (isDev) {
    // Dev mode: run Python directly from the agent folder
    const venvPython = path.join(
      __dirname, "..", "agent", "venv", "Scripts", "python.exe"
    );
    agentProcess = spawn(venvPython, ["main.py"], {
      cwd: path.join(__dirname, "..", "agent"),
      stdio: "pipe",
      windowsHide: true,
    });
  } else {
    // Production: run the PyInstaller-bundled exe
    const agentExeDir = path.dirname(agentExe);
    agentProcess = spawn(agentExe, [], {
      cwd: agentExeDir,
      stdio: "pipe",
      windowsHide: true,
      env: {
        ...process.env,
        // Ensure the agent knows where its data files are
        NGL_AGENT_DIR: agentExeDir,
        // Web app location (resources/app/)
        NGL_APP_DIR: appDir,
      },
    });
  }

  agentProcess.stdout.on("data", (data) => {
    log(`[agent] ${data.toString().trim()}`);
  });

  agentProcess.stderr.on("data", (data) => {
    log(`[agent-err] ${data.toString().trim()}`);
  });

  agentProcess.on("error", (err) => {
    console.error("Failed to start agent:", err.message);
    dialog.showErrorBox(
      "NGL Accounting — Agent Error",
      `Could not start the agent server.\n\n${err.message}`
    );
    app.quit();
  });

  agentProcess.on("exit", (code) => {
    log(`Agent exited with code ${code}`);
    if (!isQuitting) {
      dialog.showErrorBox(
        "NGL Accounting — Agent Stopped",
        `The agent server stopped unexpectedly (code ${code}).\nThe app will close.`
      );
      app.quit();
    }
  });
}

function stopAgent() {
  if (agentProcess && !agentProcess.killed) {
    agentProcess.kill();
    agentProcess = null;
  }
}

/**
 * Poll localhost:8787/health until it responds (max ~30 seconds).
 */
function waitForAgent(retries = 30) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      attempts++;
      const req = http.get(`${AGENT_URL}/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else if (attempts < retries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error("Agent did not become healthy"));
        }
      });
      req.on("error", () => {
        if (attempts < retries) {
          setTimeout(check, 1000);
        } else {
          reject(new Error("Agent did not start in time"));
        }
      });
      req.end();
    };
    check();
  });
}

// ── Window ─────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    icon: iconPath,
    title: "NGL Accounting",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false, // Show after content loads
  });

  mainWindow.loadURL(AGENT_URL);

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  // F5 / Ctrl+R to refresh, F12 / Ctrl+Shift+I for DevTools
  mainWindow.webContents.on("before-input-event", (event, input) => {
    if (input.key === "F5" || (input.control && input.key.toLowerCase() === "r")) {
      mainWindow.webContents.reload();
      event.preventDefault();
    }
    if (input.key === "F12" || (input.control && input.shift && input.key.toLowerCase() === "i")) {
      mainWindow.webContents.toggleDevTools();
      event.preventDefault();
    }
  });

  // Confirm before closing — shows themed in-app modal
  let closeConfirmPending = false;
  mainWindow.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      if (closeConfirmPending) return; // don't stack modals
      closeConfirmPending = true;
      mainWindow.webContents.executeJavaScript(`
        new Promise(resolve => {
          // Overlay
          const ov = document.createElement('div');
          ov.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,0.5);z-index:99999;display:flex;align-items:center;justify-content:center;animation:fadeIn .15s ease';
          // Modal
          const m = document.createElement('div');
          m.style.cssText = 'background:#fff;border-radius:16px;padding:32px 36px 28px;max-width:400px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.25);font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;text-align:center;animation:scaleIn .2s ease';
          // Icon
          m.innerHTML = '<div style="width:52px;height:52px;border-radius:50%;background:#FFF7ED;display:flex;align-items:center;justify-content:center;margin:0 auto 18px">'
            + '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#ea580c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div>'
            + '<h3 style="margin:0 0 8px;font-size:1.15rem;font-weight:700;color:#0f172a">Exit NGL Accounting?</h3>'
            + '<p style="margin:0 0 24px;font-size:0.85rem;color:#64748b;line-height:1.5">The agent server will shut down and any active jobs will be stopped.</p>'
            + '<div style="display:flex;gap:10px;justify-content:center">'
            + '<button id="_nglStay" style="flex:1;padding:10px 0;border:1px solid #e2e8f0;background:#fff;border-radius:10px;font-size:0.85rem;font-weight:600;color:#475569;cursor:pointer;transition:all .15s">Cancel</button>'
            + '<button id="_nglExit" style="flex:1;padding:10px 0;border:none;background:#ea580c;border-radius:10px;font-size:0.85rem;font-weight:600;color:#fff;cursor:pointer;transition:all .15s">Exit</button></div>';
          ov.appendChild(m);
          document.body.appendChild(ov);
          // Animations
          const style = document.createElement('style');
          style.textContent = '@keyframes fadeIn{from{opacity:0}to{opacity:1}}@keyframes scaleIn{from{opacity:0;transform:scale(0.95)}to{opacity:1;transform:scale(1)}}#_nglStay:hover{background:#f8fafc;border-color:#cbd5e1}#_nglExit:hover{background:#dc4a0a}';
          document.head.appendChild(style);
          // Handlers
          const cleanup = (val) => { ov.remove(); style.remove(); resolve(val); };
          document.getElementById('_nglExit').onclick = () => cleanup(true);
          document.getElementById('_nglStay').onclick = () => cleanup(false);
          ov.onclick = (e) => { if (e.target === ov) cleanup(false); };
        })
      `).then((shouldExit) => {
        closeConfirmPending = false;
        if (shouldExit) {
          isQuitting = true;
          mainWindow.close();
        }
      }).catch(() => {
        closeConfirmPending = false;
      });
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ── Tray ───────────────────────────────────────────────────────────

function createTray() {
  const icon = nativeImage.createFromPath(iconPath);
  tray = new Tray(icon.resize({ width: 16, height: 16 }));
  tray.setToolTip("NGL Accounting");

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "Open NGL Accounting",
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    {
      label: "Refresh",
      click: () => {
        if (mainWindow) {
          mainWindow.webContents.reload();
          mainWindow.show();
          mainWindow.focus();
        }
      },
    },
    { type: "separator" },
    {
      label: "Quit",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on("double-click", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ── Single instance lock ─────────────────────────────────────────
// Prevent multiple copies from fighting over port 8787.
log("Requesting single instance lock...");
const gotTheLock = app.requestSingleInstanceLock();
log(`Single instance lock result: ${gotTheLock}`);
if (!gotTheLock) {
  log("Another instance is already running — quitting.");
  app.quit();
} else {
  app.on("second-instance", () => {
    // If user tries to open a second instance, focus the existing window
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ── App lifecycle ──────────────────────────────────────────────────

app.whenReady().then(async () => {
  log("Starting NGL Accounting...");
  log(`userData: ${app.getPath("userData")}`);
  log(`logFile: ${_logFile}`);

  startAgent();

  try {
    await waitForAgent();
    log("Agent is ready!");
  } catch (err) {
    dialog.showErrorBox(
      "NGL Accounting — Startup Error",
      "The agent server did not start in time.\nPlease try again."
    );
    stopAgent();
    app.quit();
    return;
  }

  createWindow();
  createTray();
});

app.on("before-quit", () => {
  isQuitting = true;
  stopAgent();
});

app.on("window-all-closed", () => {
  // On Windows, quit when all windows closed
  if (process.platform !== "darwin") {
    isQuitting = true;
    app.quit();
  }
});

app.on("activate", () => {
  if (mainWindow === null) {
    createWindow();
  }
});
