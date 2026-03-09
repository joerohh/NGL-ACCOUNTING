/**
 * NGL Accounting — Electron preload script.
 *
 * Runs in the renderer's isolated context. Exposes minimal
 * desktop-specific APIs to the web app via window.nglDesktop.
 */

const { contextBridge } = require("electron");

let appVersion = "1.0.0";
try { appVersion = require("./package.json").version; } catch { /* packaged mode */ }

contextBridge.exposeInMainWorld("nglDesktop", {
  /** True when running inside the Electron shell (vs. plain browser). */
  isDesktop: true,

  /** App version from package.json. */
  version: appVersion,
});
