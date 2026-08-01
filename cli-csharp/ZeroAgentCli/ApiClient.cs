// ============================================================
//  ApiClient - HTTP API（登录、会话管理、SSE 流式聊天）
//  使用 HttpClient + System.Text.Json（均为标准库）
// ============================================================
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace ZeroAgentCli;

public class ApiClient
{
    private readonly HttpClient _http;
    private readonly ClientConfig _cfg;
    public string Token { get; set; } = "";

    public ApiClient(ClientConfig cfg)
    {
        _cfg = cfg;
        _http = new HttpClient { Timeout = TimeSpan.FromSeconds(300) };
        _http.BaseAddress = new Uri(cfg.Server);
    }

    // ---- 登录 ----
    public string Login()
    {
        var body = $"{{\"username\":{JsonUtil.Str(_cfg.Username)},\"password\":{JsonUtil.Str(_cfg.Password)}}}";
        var resp = _http.PostAsync("/api/login",
            new StringContent(body, Encoding.UTF8, "application/json")).GetAwaiter().GetResult();

        var respBody = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();

        if (!resp.IsSuccessStatusCode)
        {
            string detail = "";
            try { detail = JsonNode.Parse(respBody)?["detail"]?.ToString() ?? ""; } catch { }
            throw new Exception($"登录失败: {(detail.Length > 0 ? detail : resp.StatusCode.ToString())}");
        }

        var json = JsonNode.Parse(respBody);
        Token = json?["token"]?.ToString() ?? "";
        if (Token.Length == 0) throw new Exception("登录失败: 服务器未返回 token");
        return Token;
    }

    // ---- 获取会话列表 ----
    public List<SessionInfo> GetSessions()
    {
        var req = new HttpRequestMessage(HttpMethod.Get, "/api/sessions");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        if (!resp.IsSuccessStatusCode) return new List<SessionInfo>();

        var body = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        var json = JsonNode.Parse(body);
        var result = new List<SessionInfo>();
        if (json?["sessions"] is JsonArray arr)
        {
            foreach (var s in arr)
            {
                result.Add(new SessionInfo
                {
                    SessionId = s?["session_id"]?.ToString() ?? "",
                    Name = s?["name"]?.ToString() ?? "未命名",
                    MessageCount = s?["message_count"]?.GetValue<int>() ?? 0,
                });
            }
        }
        return result;
    }

    // ---- 创建会话 ----
    public SessionInfo? CreateSession(string name)
    {
        var payload = $"{{\"name\":{JsonUtil.Str(name)}}}";
        var req = new HttpRequestMessage(HttpMethod.Post, "/api/sessions")
        {
            Content = new StringContent(payload, Encoding.UTF8, "application/json")
        };
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        if (!resp.IsSuccessStatusCode) return null;
        var body = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        var json = JsonNode.Parse(body);
        return new SessionInfo
        {
            SessionId = json?["session_id"]?.ToString() ?? "",
            Name = json?["name"]?.ToString() ?? name,
            MessageCount = 0,
        };
    }

    // ---- 删除会话 ----
    public bool DeleteSession(string sessionId)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"/api/sessions/{sessionId}");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        return resp.IsSuccessStatusCode;
    }

    // ---- 删除某一轮 ----
    public bool DeleteRound(string sessionId, int roundIndex)
    {
        var req = new HttpRequestMessage(HttpMethod.Delete, $"/api/sessions/{sessionId}/messages/{roundIndex}");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        return resp.IsSuccessStatusCode;
    }

    // ---- 读取设置 ----
    public JsonObject? GetSettings()
    {
        var req = new HttpRequestMessage(HttpMethod.Get, "/api/settings");
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        if (!resp.IsSuccessStatusCode) return null;
        var body = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        try { return JsonNode.Parse(body) as JsonObject; } catch { return null; }
    }

    // ---- 保存设置 ----
    public bool SaveSettings(JsonObject updates)
    {
        var req = new HttpRequestMessage(HttpMethod.Put, "/api/settings")
        {
            Content = new StringContent(updates.ToJsonString(), Encoding.UTF8, "application/json")
        };
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);
        var resp = _http.Send(req);
        return resp.IsSuccessStatusCode;
    }

    // ---- SSE 流式聊天 ----
    // 注意：异步迭代器中 yield 不能出现在 try/catch 块内，
    // 因此把请求发送封装到普通异步方法，解析错误用局部变量规避。
    public async IAsyncEnumerable<SseEvent> ChatStream(
        string sessionId,
        string message,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken ct = default)
    {
        var payload = $"{{\"message\":{JsonUtil.Str(message)},\"session_id\":{JsonUtil.Str(sessionId)}}}";
        var req = new HttpRequestMessage(HttpMethod.Post, "/chat/stream")
        {
            Content = new StringContent(payload, Encoding.UTF8, "application/json")
        };
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", Token);

        // 发送请求（在普通方法中捕获异常）
        var sendResult = await SendStreamRequestAsync(req, ct);
        if (sendResult.Error != null)
        {
            yield return new SseEvent { Type = "error", Content = sendResult.Error };
            yield break;
        }

        using var resp = sendResult.Response!;
        if (!resp.IsSuccessStatusCode)
        {
            yield return new SseEvent { Type = "error", Content = $"服务器错误 ({(int)resp.StatusCode})" };
            yield break;
        }

        using var stream = await resp.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream, Encoding.UTF8);

        while (!reader.EndOfStream)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line is null) break;
            var trimmed = line.Trim();
            if (!trimmed.StartsWith("data: ")) continue;
            var jsonStr = trimmed.Substring(6);

            // 解析错误时返回 null（不在 try 内 yield）
            var ev = TryParseSse(jsonStr);
            if (ev != null)
                yield return ev;
        }
    }

    private record StreamSendResult(HttpResponseMessage? Response, string? Error);

    private async Task<StreamSendResult> SendStreamRequestAsync(HttpRequestMessage req, CancellationToken ct)
    {
        try
        {
            var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
            return new StreamSendResult(resp, null);
        }
        catch (Exception e)
        {
            return new StreamSendResult(null, $"无法连接: {e.Message}");
        }
    }

    private static SseEvent? TryParseSse(string jsonStr)
    {
        try
        {
            var node = JsonNode.Parse(jsonStr);
            if (node is null) return null;
            return new SseEvent
            {
                Type = node["type"]?.ToString() ?? "",
                Content = node["content"]?.ToString() ?? "",
                Name = node["name"]?.ToString() ?? "",
                SessionId = node["session_id"]?.ToString() ?? "",
                Stopped = node["stopped"]?.GetValue<bool>() ?? false,
            };
        }
        catch { return null; }
    }
}

public class SessionInfo
{
    public string SessionId { get; set; } = "";
    public string Name { get; set; } = "未命名";
    public int MessageCount { get; set; }
}

public class SseEvent
{
    public string Type { get; set; } = "";      // token / tool_start / tool_end / done / error / thinking
    public string Content { get; set; } = "";
    public string Name { get; set; } = "";
    public string SessionId { get; set; } = "";
    public bool Stopped { get; set; }
}
