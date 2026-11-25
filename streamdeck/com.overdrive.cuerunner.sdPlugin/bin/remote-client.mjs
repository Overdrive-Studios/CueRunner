// Talks to CueRunner's phone remote: POST /api/cmd with the PIN in the X-PIN header.
// Pure helpers are exported so they can be tested without Stream Deck or CueRunner running.

export const COMMAND_RE = /^(go|stop|pause|panic|cue:\d+|pad:\d+)$/;
export const DEFAULT_PORT = 8080;

const MESSAGES = {
  400: "CueRunner did not accept that command.",
  403: "Wrong PIN.",
  429: "CueRunner locked the remote after wrong PINs. Restart CueRunner.",
};

export function validate(settings = {}) {
  const host = String(settings.host ?? "").trim();
  const pin = String(settings.pin ?? "").trim();
  const command = String(settings.command ?? "").trim();
  const port = Number(settings.port ?? DEFAULT_PORT) || DEFAULT_PORT;
  if (!host) throw new Error("Set the CueRunner computer's address.");
  if (pin.length < 4) throw new Error("Set the 4+ digit PIN from CueRunner.");
  if (!COMMAND_RE.test(command)) throw new Error("Command must be go, stop, pause, panic, cue:N or pad:N.");
  return { host, port, pin, command };
}

export function buildRequest(settings) {
  const { host, port, pin, command } = validate(settings);
  return {
    url: `http://${host}:${port}/api/cmd`,
    init: {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PIN": pin },
      body: JSON.stringify({ cmd: command }),
    },
  };
}

export function messageFor(status) {
  return MESSAGES[status] ?? `CueRunner returned an error (${status}).`;
}

export async function sendCommand(settings, fetchImpl = globalThis.fetch) {
  const { url, init } = buildRequest(settings);
  let response;
  try {
    response = await fetchImpl(url, init);
  } catch (err) {
    throw new Error("Could not reach CueRunner. Check the address and that the phone remote is started.");
  }
  if (!response.ok) throw new Error(messageFor(response.status));
  return true;
}
