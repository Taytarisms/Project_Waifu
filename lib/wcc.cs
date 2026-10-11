using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WebsocketCollab;

public sealed class ProtocolMessage
{
    [JsonPropertyName("version")]
    public int Version { get; set; } = WebsocketCollabClient.ProtocolVersion;

    [JsonPropertyName("type")]
    public string Type { get; set; } = string.Empty;

    [JsonPropertyName("from")]
    public string From { get; set; } = string.Empty;

    [JsonPropertyName("to")]
    public List<string> To { get; set; } = [];

    [JsonPropertyName("payload")]
    public Payload Payload { get; set; } = null!;
}

public sealed class Payload
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = null!;

    [JsonPropertyName("content")]
    public string Content { get; set; } = null!;
}

public sealed class WebsocketCollabClientOptions
{
    public int MaxReconnectAttempts { get; init; } = 5;
    public TimeSpan ReconnectBaseDelay { get; init; } = TimeSpan.FromSeconds(1);
    public TimeSpan ReconnectMaxDelay { get; init; } = TimeSpan.FromSeconds(30);
    public int MaxMessageBytes { get; init; } = 64 * 1024;
    public TimeSpan ConnectTimeout { get; init; } = TimeSpan.FromSeconds(10);
    public TimeSpan CloseTimeout { get; init; } = TimeSpan.FromSeconds(5);
}

/// <summary>
/// Protocol-v1 WebSocket client for the Discord AI collaboration network.
/// </summary>
public sealed class WebsocketCollabClient : IAsyncDisposable
{
    public const int ProtocolVersion = 1;

    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = false
    };

    private readonly WebsocketCollabClientOptions _options;
    private readonly SemaphoreSlim _connectionGate = new(1, 1);
    private readonly SemaphoreSlim _sendGate = new(1, 1);
    private readonly object _stateLock = new();

    private ClientWebSocket? _socket;
    private CancellationTokenSource? _lifetimeCancellation;
    private Task? _watcherTask;
    private bool _manualDisconnect;
    private string _user = string.Empty;
    private string _password = string.Empty;

    public bool Connected { get; private set; }
    public string ChannelId { get; private set; } = string.Empty;
    public Uri? ServerUrl { get; private set; }

    public event EventHandler<ProtocolMessage>? OnAllMessages;
    public event EventHandler<ProtocolMessage>? OnTextMessage;
    public event EventHandler<ProtocolMessage>? OnDataMessage;
    public event Action<bool>? OnConnectionStateChanged;

    public WebsocketCollabClient(WebsocketCollabClientOptions? options = null)
    {
        _options = options ?? new WebsocketCollabClientOptions();
        ValidateOptions(_options);
    }

    public async Task Connect(
        string url,
        string channelId,
        string user,
        string password,
        CancellationToken cancellationToken = default)
    {
        var roomUrl = BuildRoomUri(url, channelId);
        _ = CreateAuthorizationHeader(user, password);

        await _connectionGate.WaitAsync(cancellationToken);
        try
        {
            if (_socket is not null || Connected)
            {
                throw new InvalidOperationException(
                    "This client is already connected or connecting.");
            }

            _manualDisconnect = false;
            _user = user;
            _password = password;
            ChannelId = channelId;
            ServerUrl = roomUrl;
            _lifetimeCancellation = new CancellationTokenSource();

            try
            {
                await OpenSocketAsync(cancellationToken);
            }
            catch
            {
                ClearServerData();
                throw;
            }

            _watcherTask = WatchAsync(_lifetimeCancellation.Token);
        }
        finally
        {
            _connectionGate.Release();
        }
    }

    public async Task Disconnect(CancellationToken cancellationToken = default)
    {
        await _connectionGate.WaitAsync(cancellationToken);
        try
        {
            _manualDisconnect = true;
            _lifetimeCancellation?.Cancel();

            ClientWebSocket? socket;
            Task? watcher;
            lock (_stateLock)
            {
                socket = _socket;
                watcher = _watcherTask;
                SetConnected(false);
            }

            if (socket?.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                using var closeCancellation =
                    CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                closeCancellation.CancelAfter(_options.CloseTimeout);
                try
                {
                    await socket.CloseOutputAsync(
                        WebSocketCloseStatus.NormalClosure,
                        "Client disconnecting.",
                        closeCancellation.Token);
                }
                catch (Exception exception) when (
                    exception is OperationCanceledException or WebSocketException)
                {
                    socket.Abort();
                }
            }

            if (watcher is not null && watcher.Id != Task.CurrentId)
            {
                try
                {
                    await watcher.WaitAsync(_options.CloseTimeout, cancellationToken);
                }
                catch (TimeoutException)
                {
                    socket?.Abort();
                }
                catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
                {
                    // The lifetime cancellation normally ends the receive loop.
                }
            }

            socket?.Dispose();
            ClearServerData();
            Log("Disconnected from WebSocket Collab Server.");
        }
        finally
        {
            _connectionGate.Release();
        }
    }

    public Task SendText(
        string sender,
        string content,
        string[]? to = null,
        CancellationToken cancellationToken = default) =>
        Send(
            "message",
            new Payload { Name = sender, Content = content },
            to,
            cancellationToken);

    public Task SendData(
        string dataName,
        string data,
        string[]? to = null,
        CancellationToken cancellationToken = default) =>
        Send(
            "data",
            new Payload { Name = dataName, Content = data },
            to,
            cancellationToken);

    public async Task Send(
        string messageType,
        Payload payload,
        string[]? to = null,
        CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(messageType);
        ArgumentNullException.ThrowIfNull(payload);
        ArgumentNullException.ThrowIfNull(payload.Name);
        ArgumentNullException.ThrowIfNull(payload.Content);

        var recipients = to ?? ["all"];
        if (recipients.Length is < 1 or > 64 ||
            recipients.Any(string.IsNullOrWhiteSpace))
        {
            throw new ArgumentException(
                "Recipients must contain 1-64 non-empty names.",
                nameof(to));
        }

        var message = new ProtocolMessage
        {
            Version = ProtocolVersion,
            Type = messageType,
            From = _user,
            To = [.. recipients],
            Payload = payload
        };
        var json = JsonSerializer.Serialize(message, JsonOptions);
        var bytes = Encoding.UTF8.GetBytes(json);
        if (bytes.Length > _options.MaxMessageBytes)
        {
            throw new ArgumentException(
                "Serialized message exceeds the configured maximum size.",
                nameof(payload));
        }

        await _sendGate.WaitAsync(cancellationToken);
        try
        {
            var socket = GetSocket();
            if (!Connected || socket?.State != WebSocketState.Open)
            {
                throw new InvalidOperationException(
                    "The collab connection is closed.");
            }

            await socket.SendAsync(
                bytes,
                WebSocketMessageType.Text,
                endOfMessage: true,
                cancellationToken);
        }
        finally
        {
            _sendGate.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        await Disconnect();
        _connectionGate.Dispose();
        _sendGate.Dispose();
        _lifetimeCancellation?.Dispose();
    }

    internal static Uri BuildRoomUri(string url, string channelId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(url);
        if (!IsValidRoomId(channelId))
        {
            throw new ArgumentException(
                "Channel ID must contain 4-64 lowercase letters or digits.",
                nameof(channelId));
        }

        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != "ws" && uri.Scheme != "wss") ||
            string.IsNullOrWhiteSpace(uri.Host))
        {
            throw new ArgumentException(
                "URL must be an absolute ws:// or wss:// address.",
                nameof(url));
        }

        if (!string.IsNullOrEmpty(uri.UserInfo))
        {
            throw new ArgumentException(
                "Credentials must not be embedded in the URL.",
                nameof(url));
        }

        if (!string.IsNullOrEmpty(uri.Query) || !string.IsNullOrEmpty(uri.Fragment))
        {
            throw new ArgumentException(
                "URL must not contain a query or fragment.",
                nameof(url));
        }

        var builder = new UriBuilder(uri)
        {
            Path = $"{uri.AbsolutePath.TrimEnd('/')}/{Uri.EscapeDataString(channelId)}",
            Query = string.Empty,
            Fragment = string.Empty,
            UserName = string.Empty,
            Password = string.Empty
        };
        return builder.Uri;
    }

    internal static string CreateAuthorizationHeader(string user, string password)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(user);
        ArgumentException.ThrowIfNullOrEmpty(password);
        if (user.Contains(':'))
        {
            throw new ArgumentException(
                "User cannot contain a colon.",
                nameof(user));
        }

        var credentials = Convert.ToBase64String(
            Encoding.UTF8.GetBytes($"{user}:{password}"));
        return $"Basic {credentials}";
    }

    internal static bool TryParseMessage(string json, out ProtocolMessage? message)
    {
        message = null;
        try
        {
            var parsed = JsonSerializer.Deserialize<ProtocolMessage>(json, JsonOptions);
            if (parsed is null ||
                parsed.Version != ProtocolVersion ||
                string.IsNullOrWhiteSpace(parsed.Type) ||
                string.IsNullOrWhiteSpace(parsed.From) ||
                parsed.To is not { Count: >= 1 and <= 64 } ||
                parsed.To.Any(string.IsNullOrWhiteSpace) ||
                parsed.Payload is null ||
                parsed.Payload.Name is null ||
                parsed.Payload.Content is null)
            {
                return false;
            }

            message = parsed;
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task OpenSocketAsync(CancellationToken cancellationToken)
    {
        var roomUrl = ServerUrl ??
            throw new InvalidOperationException("Server URL is not configured.");

        var socket = new ClientWebSocket();
        socket.Options.SetRequestHeader(
            "Authorization",
            CreateAuthorizationHeader(_user, _password));
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(20);

        using var connectCancellation =
            CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        connectCancellation.CancelAfter(_options.ConnectTimeout);

        try
        {
            await socket.ConnectAsync(roomUrl, connectCancellation.Token);
        }
        catch
        {
            socket.Dispose();
            throw;
        }

        ClientWebSocket? oldSocket;
        lock (_stateLock)
        {
            oldSocket = _socket;
            _socket = socket;
            SetConnected(true);
        }
        oldSocket?.Dispose();
    }

    private async Task WatchAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var socket = GetSocket();
                if (socket is null)
                {
                    return;
                }

                ReceivedMessage received;
                try
                {
                    received = await ReceiveMessageAsync(socket, cancellationToken);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    return;
                }
                catch (WebSocketException exception)
                {
                    Log($"WebSocket receive failed: {exception.Message}");
                    received = new ReceivedMessage(null, null, RetryAllowed: true);
                }

                if (received.Text is not null)
                {
                    DispatchMessage(received.Text);
                    continue;
                }

                SetConnected(false);
                if (_manualDisconnect || cancellationToken.IsCancellationRequested)
                {
                    return;
                }

                if (!received.RetryAllowed ||
                    received.CloseStatus is WebSocketCloseStatus.NormalClosure or
                        WebSocketCloseStatus.PolicyViolation)
                {
                    Log(
                        $"Connection closed without retry ({received.CloseStatus?.ToString() ?? "unknown"}).");
                    return;
                }

                if (!await ReconnectAsync(cancellationToken))
                {
                    return;
                }
            }
        }
        finally
        {
            SetConnected(false);
        }
    }

    private async Task<ReceivedMessage> ReceiveMessageAsync(
        ClientWebSocket socket,
        CancellationToken cancellationToken)
    {
        var buffer = new byte[Math.Min(_options.MaxMessageBytes, 16 * 1024)];
        using var message = new MemoryStream();

        while (true)
        {
            var result = await socket.ReceiveAsync(
                new ArraySegment<byte>(buffer),
                cancellationToken);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return new ReceivedMessage(
                    null,
                    result.CloseStatus,
                    RetryAllowed: result.CloseStatus != WebSocketCloseStatus.PolicyViolation);
            }

            if (result.MessageType != WebSocketMessageType.Text)
            {
                await CloseInvalidSocketAsync(
                    socket,
                    WebSocketCloseStatus.InvalidMessageType,
                    "Protocol version 1 accepts text messages only.");
                return new ReceivedMessage(null, null, RetryAllowed: false);
            }

            if (message.Length + result.Count > _options.MaxMessageBytes)
            {
                await CloseInvalidSocketAsync(
                    socket,
                    WebSocketCloseStatus.MessageTooBig,
                    "Message exceeds the configured client limit.");
                return new ReceivedMessage(null, null, RetryAllowed: false);
            }

            message.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                try
                {
                    return new ReceivedMessage(
                        StrictUtf8.GetString(message.GetBuffer(), 0, checked((int)message.Length)),
                        null,
                        RetryAllowed: true);
                }
                catch (DecoderFallbackException)
                {
                    await CloseInvalidSocketAsync(
                        socket,
                        WebSocketCloseStatus.InvalidPayloadData,
                        "Message is not valid UTF-8.");
                    return new ReceivedMessage(null, null, RetryAllowed: false);
                }
            }
        }
    }

    private async Task<bool> ReconnectAsync(CancellationToken cancellationToken)
    {
        for (var attempt = 0; attempt < _options.MaxReconnectAttempts; attempt++)
        {
            var multiplier = Math.Pow(2, attempt);
            var delayMilliseconds = Math.Min(
                _options.ReconnectBaseDelay.TotalMilliseconds * multiplier,
                _options.ReconnectMaxDelay.TotalMilliseconds);
            var delay = TimeSpan.FromMilliseconds(delayMilliseconds);
            Log($"Reconnecting in {delay.TotalSeconds:g} seconds...");

            await Task.Delay(delay, cancellationToken);
            try
            {
                await OpenSocketAsync(cancellationToken);
                Log("Reconnected successfully.");
                return true;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return false;
            }
            catch (Exception exception)
            {
                Log($"Reconnect attempt failed: {exception.Message}");
            }
        }

        Log("Maximum reconnect attempts reached.");
        return false;
    }

    private void DispatchMessage(string json)
    {
        if (Encoding.UTF8.GetByteCount(json) > _options.MaxMessageBytes ||
            !TryParseMessage(json, out var message) ||
            message is null)
        {
            Log("Ignored a malformed protocol-v1 message.");
            return;
        }

        InvokeHandlers(OnAllMessages, message);
        if (!message.To.Contains("all") && !message.To.Contains(_user))
        {
            return;
        }
        if (message.From == _user)
        {
            return;
        }

        if (message.Type == "message")
        {
            InvokeHandlers(OnTextMessage, message);
        }
        else if (message.Type == "data")
        {
            InvokeHandlers(OnDataMessage, message);
        }
    }

    private void InvokeHandlers(
        EventHandler<ProtocolMessage>? handlers,
        ProtocolMessage message)
    {
        if (handlers is null)
        {
            return;
        }

        foreach (EventHandler<ProtocolMessage> handler in handlers.GetInvocationList())
        {
            try
            {
                handler(this, message);
            }
            catch (Exception exception)
            {
                Log($"A client callback failed: {exception.Message}");
            }
        }
    }

    private void SetConnected(bool connected)
    {
        if (Connected == connected)
        {
            return;
        }

        Connected = connected;
        var handlers = OnConnectionStateChanged;
        if (handlers is null)
        {
            return;
        }

        foreach (Action<bool> handler in handlers.GetInvocationList())
        {
            try
            {
                handler(connected);
            }
            catch (Exception exception)
            {
                Log($"A connection-state callback failed: {exception.Message}");
            }
        }
    }

    private ClientWebSocket? GetSocket()
    {
        lock (_stateLock)
        {
            return _socket;
        }
    }

    private void ClearServerData()
    {
        lock (_stateLock)
        {
            _socket = null;
            _watcherTask = null;
            Connected = false;
            ChannelId = string.Empty;
            ServerUrl = null;
            _user = string.Empty;
            _password = string.Empty;
        }

        _lifetimeCancellation?.Dispose();
        _lifetimeCancellation = null;
    }

    private static async Task CloseInvalidSocketAsync(
        ClientWebSocket socket,
        WebSocketCloseStatus status,
        string reason)
    {
        if (socket.State is not (WebSocketState.Open or WebSocketState.CloseReceived))
        {
            return;
        }

        try
        {
            await socket.CloseOutputAsync(status, reason, CancellationToken.None);
        }
        catch (WebSocketException)
        {
            socket.Abort();
        }
    }

    private static bool IsValidRoomId(string? roomId) =>
        roomId is { Length: >= 4 and <= 64 } &&
        roomId.All(character =>
            character is >= 'a' and <= 'z' or >= '0' and <= '9');

    private static void ValidateOptions(WebsocketCollabClientOptions options)
    {
        if (options.MaxReconnectAttempts < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(options.MaxReconnectAttempts));
        }
        if (options.MaxMessageBytes < 1024)
        {
            throw new ArgumentOutOfRangeException(nameof(options.MaxMessageBytes));
        }
        if (options.ReconnectBaseDelay <= TimeSpan.Zero ||
            options.ReconnectMaxDelay <= TimeSpan.Zero ||
            options.ConnectTimeout <= TimeSpan.Zero ||
            options.CloseTimeout <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(
                nameof(options),
                "Timeouts and reconnect delays must be positive.");
        }
    }

    private static void Log(string message) => Console.WriteLine($"WCC: {message}");

    private sealed record ReceivedMessage(
        string? Text,
        WebSocketCloseStatus? CloseStatus,
        bool RetryAllowed);
}
