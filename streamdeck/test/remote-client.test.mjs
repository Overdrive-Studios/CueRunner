import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRequest, messageFor, sendCommand, validate } from "../com.overdrive.cuerunner.sdPlugin/bin/remote-client.mjs";

const good = { host: "192.168.1.20", port: "8080", pin: "1234", command: "cue:3" };

test("accepts the command set the remote understands", () => {
  for (const command of ["go", "stop", "pause", "panic", "cue:0", "pad:63"]) {
    assert.equal(validate({ ...good, command }).command, command);
  }
});

test("rejects unknown commands and short PINs", () => {
  assert.throws(() => validate({ ...good, command: "explode" }));
  assert.throws(() => validate({ ...good, pin: "12" }));
  assert.throws(() => validate({ ...good, host: "" }));
});

test("builds the request the remote expects", () => {
  const { url, init } = buildRequest(good);
  assert.equal(url, "http://192.168.1.20:8080/api/cmd");
  assert.equal(init.method, "POST");
  assert.equal(init.headers["X-PIN"], "1234");
  assert.deepEqual(JSON.parse(init.body), { cmd: "cue:3" });
});

test("uses port 8080 when none is set", () => {
  const { url } = buildRequest({ host: "10.0.0.5", pin: "1234", command: "go" });
  assert.equal(url, "http://10.0.0.5:8080/api/cmd");
});

test("sendCommand resolves on success", async () => {
  const result = await sendCommand(good, async () => ({ ok: true, status: 200 }));
  assert.equal(result, true);
});

test("sendCommand explains a wrong PIN and a locked remote", async () => {
  await assert.rejects(sendCommand(good, async () => ({ ok: false, status: 403 })), /Wrong PIN/);
  await assert.rejects(sendCommand(good, async () => ({ ok: false, status: 429 })), /locked/);
  assert.equal(messageFor(500), "CueRunner returned an error (500).");
});

test("sendCommand reports an unreachable computer", async () => {
  await assert.rejects(
    sendCommand(good, async () => {
      throw new Error("ECONNREFUSED");
    }),
    /Could not reach CueRunner/,
  );
});
