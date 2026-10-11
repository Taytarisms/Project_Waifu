"use strict";

const PROTOCOL_VERSION = 1;
const POLICY_VIOLATION = 1008;
const NORMAL_CLOSURE = 1000;

function assert(condition, message) {
  if (!condition) throw new Error(`WCC: ${message}`);
}

function log(...args) {
  console.log("WCC:", ...args);
}

function warn(...args) {
  console.warn("WCC:", ...args);
}

function reportError(...args) {
  console.error("WCC:", ...args);
}

function validateRoomId(roomId) {
  assert(
    typeof roomId === "string" && /^[a-z0-9]{4,64}$/.test(roomId),
    "channel_id must contain 4-64 lowercase letters or digits."
  );
}

function buildRoomUrl(url, roomId) {
  validateRoomId(roomId);

  const uri = url instanceof URL ? new URL(url.toString()) : new URL(url);
  assert(
    uri.protocol === "ws:" || uri.protocol === "wss:",
    "url must use the ws or wss scheme."
  );
  assert(uri.hostname.length > 0, "url must include a host.");
  assert(
    !uri.username && !uri.password,
    "credentials must not be embedded in the URL."
  );
  assert(!uri.search && !uri.hash, "url must not contain a query or fragment.");

  const basePath = uri.pathname.replace(/\/+$/, "");
  uri.pathname = `${basePath}/${roomId}`;
  return uri;
}

function createBasicAuthorization(user, password) {
  assert(typeof user === "string" && user.length > 0, "user is required.");
  assert(!user.includes(":"), "user cannot contain a colon.");
  assert(
    typeof password === "string" && password.length > 0,
    "password is required."
  );

  return `Basic ${Buffer.from(`${user}:${password}`, "utf8").toString("base64")}`;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isWellFormedProtocolMessage(value) {
  if (!isPlainObject(value)) return false;
  if (value.version !== PROTOCOL_VERSION) return false;
  if (typeof value.type !== "string" || value.type.length === 0) return false;
  if (typeof value.from !== "string" || value.from.length === 0) return false;
  if (!Array.isArray(value.to) || value.to.length < 1 || value.to.length > 64)
    return false;
  if (!value.to.every((recipient) => typeof recipient === "string" && recipient.length > 0))
    return false;
  if (!isPlainObject(value.payload)) return false;
  if (typeof value.payload.name !== "string") return false;
  if (typeof value.payload.content !== "string") return false;
  return true;
}

class WebsocketCollabClient {
  constructor(options = {}) {
    this.PROTOCOL_VERSION = PROTOCOL_VERSION;
    this.connected = false;
    this.channel_id = "";
    this.server_url = null;
    this.user = "";
    this.socket = null;

    this.maxRetries = options.maxRetries ?? 5;
    this.reconnectBaseDelayMs = options.reconnectBaseDelayMs ?? 1000;
    this.reconnectMaxDelayMs = options.reconnectMaxDelayMs ?? 30000;
    this.maxMessageBytes = options.maxMessageBytes ?? 64 * 1024;
    this.handshakeTimeoutMs = options.handshakeTimeoutMs ?? 10000;
    this.WebSocketImpl = options.WebSocketImpl ?? require("ws");

    assert(Number.isInteger(this.maxRetries) && this.maxRetries >= 0,
      "maxRetries must be a non-negative integer.");
    assert(Number.isInteger(this.maxMessageBytes) && this.maxMessageBytes >= 1024,
      "maxMessageBytes must be an integer of at least 1024.");

    this.retry_count = 0;
    this.authorization = "";
    this.manualDisconnect = false;
    this.reconnectTimer = null;

    this.onAllMessages = null;
    this.onTextMessage = null;
    this.onDataMessage = null;
    this.onConnectionStateChanged = null;
  }

  static buildRoomUrl(url, channelId) {
    return buildRoomUrl(url, channelId);
  }

  static isWellFormedProtocolMessage(value) {
    return isWellFormedProtocolMessage(value);
  }

  isWellFormedProtocolMessage(value) {
    return isWellFormedProtocolMessage(value);
  }

  isWellFormedMessagePayload(payload) {
    return (
      isPlainObject(payload) &&
      typeof payload.name === "string" &&
      typeof payload.content === "string"
    );
  }

  async connect(url, channel_id, auth) {
    assert(auth && typeof auth === "object", "auth is required.");
    assert(typeof auth.user === "string", "auth.user must be a string.");
    assert(typeof auth.pass === "string", "auth.pass must be a string.");
    assert(
      this.socket === null || this.socket.readyState === this.WebSocketImpl.CLOSED,
      "this client is already connected or connecting."
    );

    this.server_url = buildRoomUrl(url, channel_id);
    this.authorization = createBasicAuthorization(auth.user, auth.pass);
    this.user = auth.user;
    this.channel_id = channel_id;
    this.manualDisconnect = false;
    this.retry_count = 0;
    this.#cancelReconnect();

    try {
      await this.#connectWebSocket();
    } catch (error) {
      this.connected = false;
      this.socket = null;
      this.server_url = null;
      this.authorization = "";
      this.user = "";
      this.channel_id = "";
      throw error;
    }
  }

  #connectWebSocket() {
    assert(this.server_url, "server URL is not configured.");
    assert(this.authorization, "authentication is not configured.");

    return new Promise((resolve, reject) => {
      const socket = new this.WebSocketImpl(this.server_url, {
        headers: { Authorization: this.authorization },
        maxPayload: this.maxMessageBytes,
        handshakeTimeout: this.handshakeTimeoutMs,
      });
      this.socket = socket;

      let opened = false;
      let settled = false;

      const rejectInitialConnection = (error) => {
        if (settled) return;
        settled = true;
        reject(error instanceof Error ? error : new Error(String(error)));
      };

      socket.on("open", () => {
        if (socket !== this.socket || this.manualDisconnect) {
          socket.close(NORMAL_CLOSURE, "Connection superseded.");
          return;
        }

        opened = true;
        settled = true;
        this.connected = true;
        this.retry_count = 0;
        this.#notifyConnectionState(true);
        resolve();
      });

      socket.on("message", (data, isBinary) => {
        this.#handleMessage(data, isBinary);
      });

      socket.on("error", (error) => {
        if (!opened) rejectInitialConnection(error);
        else reportError("WebSocket error:", error.message ?? error);
      });

      socket.on("unexpected-response", (_request, response) => {
        const error = new Error(
          `WebSocket handshake failed with HTTP ${response.statusCode}.`
        );
        error.statusCode = response.statusCode;
        response.resume?.();
        rejectInitialConnection(error);
        socket.terminate?.();
      });

      socket.on("close", (code, reason) => {
        if (socket !== this.socket) return;

        this.connected = false;
        this.#notifyConnectionState(false);
        const reasonText = reason?.toString("utf8") || "no reason supplied";

        if (!opened) {
          rejectInitialConnection(
            new Error(`WebSocket closed during handshake (${code}: ${reasonText}).`)
          );
          return;
        }

        if (this.manualDisconnect || code === NORMAL_CLOSURE) return;
        if (code === POLICY_VIOLATION) {
          reportError(`Server rejected the session (${code}: ${reasonText}).`);
          return;
        }

        warn(`Connection closed (${code}: ${reasonText}); reconnecting.`);
        this.#scheduleReconnect();
      });
    });
  }

  #handleMessage(data, isBinary) {
    if (isBinary) {
      warn("Ignored a binary message; protocol version 1 requires JSON text.");
      return;
    }

    const byteLength = Buffer.isBuffer(data)
      ? data.length
      : Buffer.byteLength(String(data), "utf8");
    if (byteLength > this.maxMessageBytes) {
      warn("Ignored a message larger than the configured client limit.");
      return;
    }

    let message;
    try {
      message = JSON.parse(data.toString("utf8"));
    } catch {
      warn("Ignored malformed JSON from the collab server.");
      return;
    }

    if (!isWellFormedProtocolMessage(message)) {
      warn("Ignored a malformed protocol-v1 message from the collab server.");
      return;
    }

    this.#invokeCallback(this.onAllMessages, message);

    if (!(message.to.includes("all") || message.to.includes(this.user))) return;
    if (message.from === this.user) return;

    if (message.type === "message") {
      this.#invokeCallback(
        this.onTextMessage,
        message.payload.name,
        message.payload.content,
        message
      );
    } else if (message.type === "data") {
      this.#invokeCallback(
        this.onDataMessage,
        message.payload.name,
        message.payload.content,
        message
      );
    }
  }

  #invokeCallback(callback, ...args) {
    if (typeof callback !== "function") return;
    try {
      callback(...args);
    } catch (error) {
      reportError("A client callback failed:", error);
    }
  }

  #notifyConnectionState(connected) {
    this.#invokeCallback(this.onConnectionStateChanged, connected);
  }

  #scheduleReconnect() {
    if (this.manualDisconnect || this.reconnectTimer) return;
    if (this.retry_count >= this.maxRetries) {
      reportError("Maximum reconnect attempts reached.");
      return;
    }

    const delay = Math.min(
      this.reconnectBaseDelayMs * 2 ** this.retry_count,
      this.reconnectMaxDelayMs
    );
    this.retry_count++;
    log(`Reconnecting in ${delay / 1000} seconds...`);

    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      if (this.manualDisconnect) return;

      try {
        await this.#connectWebSocket();
        log("Reconnected successfully.");
      } catch (error) {
        reportError("Reconnection attempt failed:", error.message ?? error);
        this.#scheduleReconnect();
      }
    }, delay);
  }

  #cancelReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  async disconnect() {
    this.manualDisconnect = true;
    this.#cancelReconnect();
    const socket = this.socket;
    this.connected = false;

    if (socket && socket.readyState !== this.WebSocketImpl.CLOSED) {
      await new Promise((resolve) => {
        let completed = false;
        const finish = () => {
          if (completed) return;
          completed = true;
          clearTimeout(timeout);
          resolve();
        };
        const timeout = setTimeout(() => {
          socket.terminate?.();
          finish();
        }, 5000);

        socket.once("close", finish);
        if (socket.readyState === this.WebSocketImpl.CONNECTING) socket.terminate?.();
        else socket.close(NORMAL_CLOSURE, "Client disconnecting.");
      });
    }

    this.socket = null;
    this.server_url = null;
    this.authorization = "";
    this.user = "";
    this.channel_id = "";
    this.#notifyConnectionState(false);
    log("Disconnected from WebSocket Collab Server.");
  }

  send(type, payload, to = ["all"]) {
    assert(typeof type === "string" && type.length > 0, "type is required.");
    assert(isPlainObject(payload), "payload must be an object.");
    assert(typeof payload.name === "string", "payload.name must be a string.");
    assert(
      typeof payload.content === "string",
      "payload.content must be a string."
    );
    assert(Array.isArray(to) && to.length >= 1 && to.length <= 64,
      "to must contain 1-64 recipients.");
    assert(to.every((name) => typeof name === "string" && name.length > 0),
      "every recipient must be a non-empty string.");

    if (!this.socket || this.socket.readyState !== this.WebSocketImpl.OPEN) {
      warn("Cannot send because the collab connection is closed.");
      return false;
    }

    const serialized = JSON.stringify({
      version: PROTOCOL_VERSION,
      type,
      from: this.user,
      to,
      payload,
    });
    assert(
      Buffer.byteLength(serialized, "utf8") <= this.maxMessageBytes,
      "serialized message exceeds maxMessageBytes."
    );

    this.socket.send(serialized);
    return true;
  }

  sendText(sender_name, content, to = ["all"]) {
    return this.send("message", { name: sender_name, content }, to);
  }

  sendData(label, data, to = ["all"]) {
    return this.send("data", { name: label, content: data }, to);
  }

  removeAllListeners() {
    this.onAllMessages = null;
    this.onTextMessage = null;
    this.onDataMessage = null;
    this.onConnectionStateChanged = null;
  }
}

module.exports = WebsocketCollabClient;
