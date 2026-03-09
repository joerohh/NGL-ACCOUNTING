/**
 * NGL Accounting — Electron preload script.
 *
 * Runs in the renderer's isolated context. Exposes minimal
 * desktop-specific APIs to the web app via window.nglDesktop.
 */

const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("nglDesktop", {
  /** True when running inside the Electron shell (vs. plain browser). */
  isDesktop: true,

  /** App version from package.json. */
  version: require("./package.json").version,
});
