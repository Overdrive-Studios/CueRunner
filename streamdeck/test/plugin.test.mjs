import { test } from "node:test";
import { once } from "node:events";
import assert from "node:assert/strict";
import http from "node:http";
import { WebSocketServer } from "ws";
import { start } from "../com.overdrive.cuerunner.sdPlugin/bin/plugin.mjs";

function fakeRemote(status = 200) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, pin: req.headers["x-pin"], body: JSON.parse(body || "{}") });
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end('{"ok": true}');
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, port: server.address().port })));
}

async function fakeStreamDeck() {
  const wss = new WebSocketServer({ port: 0, host: "127.0.0.1" });
  await once(wss, "listening");
  const messages = [];
  let client;
  const connected = new Promise((resolve) => {
    wss.on("connection", (ws) => {
      client = ws;
      ws.on("message", (raw) => messages.push(JSON.parse(raw.toString())));
      resolve();
    });
  });
  return { wss, messages, connected, port: () => wss.address().port, send: (m) => client.send(JSON.stringify(m)) };
}

const waitFor = async (predicate, ms = 3000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error("timed out");
};

test("registers with Stream Deck, sends the command with the PIN, and shows OK on the key", async () => {
  const remote = await fakeRemote(200);
  const deck = await fakeStreamDeck();
  const socket = start(["-port", String(deck.port()), "-pluginUUID", "abc", "-registerEvent", "registerPlugin"]);
  await deck.connected;
  await waitFor(() => deck.messages.some((m) => m.event === "registerPlugin"));
  assert.deepEqual(deck.messages[0], { event: "registerPlugin", uuid: "abc" });

  deck.send({
    event: "keyDown",
    context: "ctx1",
    payload: { settings: { host: "127.0.0.1", port: String(remote.port), pin: "4321", command: "cue:2" } },
  });
  await waitFor(() => deck.messages.some((m) => m.event === "showOk"));
  assert.equal(remote.seen.length, 1);
  assert.equal(remote.seen[0].pin, "4321");
  assert.equal(remote.seen[0].url, "/api/cmd");
  assert.deepEqual(remote.seen[0].body, { cmd: "cue:2" });

  socket.close();
  remote.server.close();
  deck.wss.close();
});

test("shows an alert on the key when the remote rejects the PIN", async () => {
  const remote = await fakeRemote(403);
  const deck = await fakeStreamDeck();
  const socket = start(["-port", String(deck.port()), "-pluginUUID", "abc", "-registerEvent", "registerPlugin"]);
  await deck.connected;
  deck.send({
    event: "keyDown",
    context: "ctx2",
    payload: { settings: { host: "127.0.0.1", port: String(remote.port), pin: "0000", command: "go" } },
  });
  await waitFor(() => deck.messages.some((m) => m.event === "showAlert"));
  assert.ok(!deck.messages.some((m) => m.event === "setTitle"));

  socket.close();
  remote.server.close();
  deck.wss.close();
});
