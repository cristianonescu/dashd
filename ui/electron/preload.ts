/**
 * Context-isolation bridge between renderer (React) and main (Node).
 * Only the methods exposed here are reachable from the UI.
 */
import { contextBridge, ipcRenderer } from "electron";

const api = {
  // Subscribe to live agent messages (state frames + events).
  onMessage: (cb: (msg: any) => void) => {
    const handler = (_e: any, msg: any) => cb(msg);
    ipcRenderer.on("agent:message", handler);
    return () => ipcRenderer.off("agent:message", handler);
  },
  onStdio: (cb: (line: string) => void) => {
    // Main may send either a single line (legacy) or a batched array of
    // lines (current). Normalize to per-line callbacks here.
    const handler = (_e: any, payload: string | string[]) => {
      if (Array.isArray(payload)) payload.forEach(cb);
      else cb(payload);
    };
    ipcRenderer.on("agent:stdio", handler);
    return () => ipcRenderer.off("agent:stdio", handler);
  },
  onStatus: (cb: (s: any) => void) => {
    const handler = (_e: any, s: any) => cb(s);
    ipcRenderer.on("agent:status", handler);
    return () => ipcRenderer.off("agent:status", handler);
  },
  onIpcState: (cb: (s: { connected: boolean }) => void) => {
    const handler = (_e: any, s: any) => cb(s);
    ipcRenderer.on("agent:ipc", handler);
    return () => ipcRenderer.off("agent:ipc", handler);
  },
  sendCmd: (cmd: object) => ipcRenderer.invoke("agent:sendCmd", cmd),
  restartAgent: () => ipcRenderer.invoke("agent:restart"),
  getPrefs: () => ipcRenderer.invoke("ui:getPrefs"),
  setPrefs: (p: object) => ipcRenderer.invoke("ui:setPrefs", p),
  getAutostart: () => ipcRenderer.invoke("autostart:get"),
  setAutostart: (enable: boolean) => ipcRenderer.invoke("autostart:set", enable),

  // Auto-update controls. The renderer drives the popup off `onUpdate`;
  // `getUpdateState` lets a re-shown window prime its UI without waiting
  // for the next main → renderer push.
  onUpdate: (cb: (s: any) => void) => {
    const handler = (_e: any, s: any) => cb(s);
    ipcRenderer.on("update:state", handler);
    return () => ipcRenderer.off("update:state", handler);
  },
  getUpdateState: () => ipcRenderer.invoke("update:getState"),
  checkForUpdates: () => ipcRenderer.invoke("update:checkNow"),
  downloadUpdate: () => ipcRenderer.invoke("update:download"),
  installUpdate: () => ipcRenderer.invoke("update:install"),
  openReleasesPage: () => ipcRenderer.invoke("update:openReleases"),

  /** Open the main window and switch to its Usage tab. Used by the
   *  popover's "Usage Dashboard" link. */
  openUsageDashboard: () => ipcRenderer.invoke("usage:openDashboard"),

  /** Subscribe to navigation requests from main (e.g. when the popover
   *  asks to switch to a specific tab). */
  onNav: (cb: (target: string) => void) => {
    const handler = (_e: any, target: string) => cb(target);
    ipcRenderer.on("nav:to", handler);
    return () => ipcRenderer.off("nav:to", handler);
  },
};

contextBridge.exposeInMainWorld("dashd", api);
export type DashdAPI = typeof api;
