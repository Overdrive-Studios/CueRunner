// Stream Deck plugin entry point. Stream Deck starts this file with -port, -pluginUUID and -registerEvent arguments.
// Stream Deck runs Node 20, which has no global WebSocket, so the `ws` package is bundled alongside this plugin.
import WebSocket from "ws";
import { sendCommand } from "./remote-client.mjs";

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) out[argv[i]] = argv[i + 1];
  return out;
}

export function start(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const socket = new WebSocket(`ws://127.0.0.1:${args["-port"]}`);

  const send = (message) => socket.send(JSON.stringify(message));

  socket.on("open", () => {
    send({ event: args["-registerEvent"], uuid: args["-pluginUUID"] });
  });

  socket.on("message", async (raw) => {
    const message = JSON.parse(raw.toString());
    if (message.event === "keyDown") {
      await onKeyDown(message, send);
    }
  });

  return socket;
}

export async function onKeyDown({ context, payload }, send) {
  try {
    await sendCommand(payload?.settings ?? {});
    send({ event: "showOk", context });
  } catch (err) {
    send({ event: "showAlert", context });
    console.error(err.message);
  }
}

if (process.argv[1] && process.argv[1].endsWith("plugin.mjs")) start();
