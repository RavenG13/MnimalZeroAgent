// ============================================================
//  WsClient - WebSocket 连接：认证 + 工具注册 + 工具调用处理
//  使用 System.Net.WebSockets.ClientWebSocket（标准库）
// ============================================================
using System.Net.WebSockets;
using System.Text;
using System.Text.Json.Nodes;

namespace ZeroAgentCli;

public static class WsClient
{
    public static async Task RunAsync(ClientConfig cfg, string token, CancellationToken ct)
    {
        try
        {
            var wsUrl = cfg.Server.Replace("https://", "wss://").Replace("http://", "ws://") + "/ws";
            using var ws = new ClientWebSocket();

            Program.W($"  ⏳ 正在连接 {wsUrl} ...", ConsoleColor.Cyan);
            Console.WriteLine();

            await ws.ConnectAsync(new Uri(wsUrl), ct);
            if (ws.State != WebSocketState.Open)
                throw new Exception("WebSocket 连接失败");

            // ---- Step 1: 认证 ----
            await SendJson(ws, $"{{\"type\":\"auth\",\"token\":\"Bearer {token}\"}}", ct);
            var authResp = await ReceiveJson(ws, ct);
            if (authResp?["type"]?.ToString() == "auth_failed")
                throw new Exception($"认证失败: {authResp["message"]}");
            if (authResp?["type"]?.ToString() != "auth_ok")
                throw new Exception("认证响应异常");

            // ---- Step 2: 注册工具（固定 schema）----
            var toolsJson = "[" + string.Join(",", NodeTools.Schemas.Select(s => s.ToToolJson())) + "]";
            var regJson = JsonNode.Parse($"{{\"type\":\"tools_register\",\"node_name\":{JsonUtil.Str(cfg.Device)},\"tools\":{toolsJson},\"work_root\":{JsonUtil.Str(cfg.WorkRoot)},\"interactive\":{(cfg.Interactive ? "true" : "false")}}}");
            await SendJson(ws, regJson!.ToJsonString(), ct);

            var regResp = await ReceiveJson(ws, ct);
            if (regResp?["type"]?.ToString() != "tools_registered")
                throw new Exception($"工具注册失败: {regResp}");

            Console.WriteLine();
            Program.WLGreen("  ✓ 已连接到 AI 大脑！");
            Program.W($"    节点: {cfg.Device}", ConsoleColor.Green);
            Console.WriteLine();
            Program.W($"    工具: {string.Join(", ", NodeTools.Schemas.Select(s => s.Name))}", ConsoleColor.DarkGray);
            Console.WriteLine();
            if (!string.IsNullOrEmpty(cfg.WorkRoot))
            {
                Program.W($"    目录: {cfg.WorkRoot}", ConsoleColor.Yellow);
                Console.WriteLine();
            }
            Program.W($"    模式: {(cfg.Interactive ? "交互确认" : "自动执行")}", ConsoleColor.Yellow);
            Console.WriteLine();

            // ---- Step 3: 循环处理工具调用 ----
            while (!ct.IsCancellationRequested && ws.State == WebSocketState.Open)
            {
                var msg = await ReceiveJson(ws, ct);
                if (msg is null) break;
                var type = msg["type"]?.ToString() ?? "";
                if (type == "tool_call")
                {
                    await HandleToolCall(ws, msg, cfg, ct);
                }
                else if (type == "ping")
                {
                    await SendJson(ws, "{\"type\":\"pong\"}", ct);
                }
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
        catch (Exception e)
        {
            Program.WLRed($"  ✗ 连接错误: {e.Message}");
        }
    }

    static async Task HandleToolCall(ClientWebSocket ws, JsonObject msg, ClientConfig cfg, CancellationToken ct)
    {
        var callId = msg["call_id"]?.ToString() ?? "?";
        var toolName = msg["tool"]?.ToString() ?? "?";
        var args = new Dictionary<string, object?>();

        if (msg["args"] is JsonObject argsObj)
        {
            foreach (var kv in argsObj)
            {
                args[kv.Key] = kv.Value?.ToString();
            }
        }

        Console.WriteLine();
        Program.W("  🔧 [AI 调用] ", ConsoleColor.Cyan);
        Program.W(toolName, ConsoleColor.White);
        Console.WriteLine();
        var argsStr = string.Join(", ", args.Select(kv => $"{kv.Key}={kv.Value}"));
        if (argsStr.Length > 120) argsStr = argsStr[..120] + "...";
        Program.W($"     参数: {argsStr}", ConsoleColor.DarkGray);
        Console.WriteLine();

        // 交互模式确认
        if (cfg.Interactive)
        {
            Program.W("     执行此操作？(y/n) ", ConsoleColor.Yellow);
            var confirm = Console.ReadLine()?.Trim().ToLower() ?? "";
            if (confirm != "y" && confirm != "yes" && confirm != "")
            {
                Program.WLDim("     → 已拒绝");
                await SendJson(ws, $"{{\"type\":\"tool_result\",\"call_id\":{JsonUtil.Str(callId)},\"result\":\"[已拒绝] 用户在终端拒绝了此操作\"}}", ct);
                return;
            }
        }

        // 执行工具（带超时）
        string result;
        int timeoutSec = toolName switch
        {
            "run_shell" => Math.Clamp(args.TryGetValue("timeout", out var t) && int.TryParse(t?.ToString(), out var tv) ? tv : 60, 1, 300) + 10,
            "run_opencode" => Math.Clamp(args.TryGetValue("timeout", out var t2) && int.TryParse(t2?.ToString(), out var tv2) ? tv2 : 300, 30, 1800) + 30,
            _ => 120,
        };

        try
        {
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(TimeSpan.FromSeconds(timeoutSec));
            result = await Task.Run(() => NodeTools.Execute(toolName, args, cfg.WorkRoot), cts.Token);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            result = $"[超时] 工具 '{toolName}' 执行超时 ({timeoutSec}s)";
        }

        // 打印结果摘要
        var preview = result.Replace("\n", " ");
        if (preview.Length > 200) preview = preview[..200] + "...";
        Program.W($"     结果: {preview}", ConsoleColor.DarkGray);
        Console.WriteLine();

        // 返回结果
        var resultJson = JsonNode.Parse($"{{\"type\":\"tool_result\",\"call_id\":{JsonUtil.Str(callId)},\"result\":{JsonUtil.Str(result)}}}");
        await SendJson(ws, resultJson!.ToJsonString(), ct);
    }

    static async Task SendJson(ClientWebSocket ws, string json, CancellationToken ct)
    {
        var bytes = Encoding.UTF8.GetBytes(json);
        await ws.SendAsync(bytes, WebSocketMessageType.Text, true, ct);
    }

    static async Task<JsonObject?> ReceiveJson(ClientWebSocket ws, CancellationToken ct)
    {
        var buffer = new byte[1024 * 1024];
        using var ms = new MemoryStream();
        WebSocketReceiveResult result;
        do
        {
            result = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), ct);
            if (result.MessageType == WebSocketMessageType.Close)
                return null;
            ms.Write(buffer, 0, result.Count);
        } while (!result.EndOfMessage);

        var text = Encoding.UTF8.GetString(ms.ToArray());
        try { return JsonNode.Parse(text) as JsonObject; } catch { return null; }
    }
}
