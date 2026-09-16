const $ = (id) => document.getElementById(id);

async function refresh(message) {
  const { cloudApiBase, cloudToken } = await chrome.storage.local.get(["cloudApiBase", "cloudToken"]);
  $("base").value = cloudApiBase || "";
  $("token").value = cloudToken || "";
  const queued = (await JhiCloudSync.queuedCaptures(chrome.storage)).length;
  const state = cloudApiBase && cloudToken ? "On" : "Off";
  $("status").textContent = `${message ? message + " · " : ""}${state} · ${queued} capture(s) queued`;
}

$("save").addEventListener("click", async () => {
  const base = $("base").value.trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  let origin;
  try {
    origin = new URL(base).origin;
  } catch (_err) {
    return refresh("Not a valid URL");
  }
  if (!token.startsWith("jhi_")) return refresh("The token should start with jhi_");
  // Host access for the cloud origin is asked for here, on a click, rather
  // than granted to every site in the manifest.
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  if (!granted) return refresh("Permission for that site was not granted");
  await chrome.storage.local.set({ cloudApiBase: base, cloudToken: token });
  refresh("Saved");
});

$("clear").addEventListener("click", async () => {
  await chrome.storage.local.remove(["cloudApiBase", "cloudToken"]);
  refresh("Turned off (queued captures are kept)");
});

$("flush").addEventListener("click", async () => {
  const sent = await JhiCloudSync.flushQueue(chrome.storage, (u, i) => fetch(u, i));
  refresh(`Sent ${sent}`);
});

refresh();
