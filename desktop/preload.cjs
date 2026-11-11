const { contextBridge, ipcRenderer } = require("electron");
const call = async (name, ...args) => {
  const result = await ipcRenderer.invoke(name, ...args);
  if (!result.ok) throw new Error(result.error);
  return result.value;
};
contextBridge.exposeInMainWorld("desktop", {
  config: () => call("desktop:config"),
  state: () => call("desktop:state"),
  saveConfig: (value) => call("desktop:save-config", value),
  check: () => call("desktop:check"),
  authorizeCapture: () => call("desktop:authorize-capture"),
  begin: () => call("desktop:begin"),
  finish: () => call("desktop:finish"),
  openDashboard: () => call("desktop:dashboard"),
  openRecordings: () => call("desktop:recordings"),
  permissions: () => call("desktop:permissions"),
  openPermissions: () => call("desktop:open-permissions"),
  pcm: (buffer) => {
    if (buffer instanceof ArrayBuffer && buffer.byteLength <= 65536)
      ipcRenderer.send("desktop:pcm", new Uint8Array(buffer));
  },
  onEvent: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("capture:event", listener);
    return () => ipcRenderer.removeListener("capture:event", listener);
  },
});
