// ============================================================
//  ChatConsole - 终端聊天控制台
//  SSE 流式对话 + 会话管理 + /settings 设置界面
// ============================================================
using System.Text.Json.Nodes;

namespace ZeroAgentCli;

public class ChatConsole
{
    private readonly ClientConfig _cfg;
    private readonly ApiClient _api;
    private readonly CancellationTokenSource _cts;
    private string _sessionId = "";
    private string _sessionName = "终端对话";
    private int _msgCount;

    public ChatConsole(ClientConfig cfg, string token, CancellationTokenSource cts)
    {
        _cfg = cfg;
        _cts = cts;
        _api = new ApiClient(cfg);
        _api.Token = token;
    }

    public async Task RunAsync()
    {
        await Task.Delay(300);

        // ---- 选择或创建会话 ----
        var sessions = _api.GetSessions();
        if (sessions.Count > 0)
        {
            Console.WriteLine();
            Program.WLBold("  📋 已有会话:");
            for (int i = 0; i < Math.Min(8, sessions.Count); i++)
            {
                Program.W($"    [{i}]", ConsoleColor.Green);
                Program.W($" {sessions[i].Name}  ");
                Program.W($"({sessions[i].MessageCount}轮)", ConsoleColor.DarkGray);
                Console.WriteLine();
            }
            Program.W("    [n]", ConsoleColor.Cyan);
            Program.WL(" 新建会话");

            Program.W("  选择 > ", ConsoleColor.Cyan);
            var choice = Console.ReadLine()?.Trim() ?? "";
            if (choice == "n")
            {
                var newS = _api.CreateSession("终端对话");
                if (newS != null) { _sessionId = newS.SessionId; _sessionName = newS.Name; }
            }
            else if (int.TryParse(choice, out var idx) && idx >= 0 && idx < sessions.Count)
            {
                _sessionId = sessions[idx].SessionId;
                _sessionName = sessions[idx].Name;
                _msgCount = sessions[idx].MessageCount;
            }
            else
            {
                _sessionId = sessions[0].SessionId;
                _sessionName = sessions[0].Name;
                _msgCount = sessions[0].MessageCount;
            }
        }
        else
        {
            var newS = _api.CreateSession("终端对话");
            if (newS != null) _sessionId = newS.SessionId;
        }

        if (_sessionId.Length == 0)
        {
            Program.WLRed("  创建会话失败，退出");
            return;
        }

        // ---- 聊天提示 ----
        Console.WriteLine();
        Program.WLDim("  ─" + new string('─', 44));
        Program.W("  💬 开始对话 — 会话: ", ConsoleColor.White);
        Program.WL(_sessionName, ConsoleColor.Cyan);
        Program.WLDim("  输入消息与 AI 对话 | /undo 撤回 | /settings 设置 | /delete 删会话 | /sessions | /new | /exit");
        Program.WLDim("  ─" + new string('─', 44));
        Console.WriteLine();

        // ---- 聊天循环 ----
        while (true)
        {
            Program.W("You: ", ConsoleColor.White);
            var input = Console.ReadLine()?.Trim() ?? "";
            if (input.Length == 0) continue;

            // 特殊命令
            if (input.StartsWith("/"))
            {
                var cmd = input.Split(' ', 2)[0].ToLower();
                if (cmd is "/exit" or "/quit" or "/q") break;
                if (cmd == "/sessions") { HandleSessions(); continue; }
                if (cmd == "/new") { HandleNew(input); continue; }
                if (cmd == "/undo") { HandleUndo(); continue; }
                if (cmd == "/delete") { HandleDelete(); continue; }
                if (cmd == "/settings") { HandleSettings(); continue; }
                // 未知命令当作普通消息发送
            }

            Console.WriteLine();
            await SendAndDisplay(input);
        }
    }

    // ---- 发送消息并流式显示 ----
    private async Task SendAndDisplay(string message)
    {
        Program.W("AI: ", ConsoleColor.Cyan);
        var fullReply = "";
        var toolCount = 0;

        try
        {
            await foreach (var ev in _api.ChatStream(_sessionId, message))
            {
                switch (ev.Type)
                {
                    case "thinking":
                        if (ev.Content.Length > 0 && fullReply.Length == 0)
                        {
                            Program.W(ev.Content, ConsoleColor.DarkGray);
                        }
                        break;

                    case "token":
                        fullReply += ev.Content;
                        Console.Write(ev.Content);
                        break;

                    case "tool_start":
                        toolCount++;
                        if (fullReply.Length > 0) Console.WriteLine();
                        Program.W($"  ⏳ [{toolCount}] {ev.Name} ", ConsoleColor.Yellow);
                        var argsPreview = ev.Content.Length > 60 ? ev.Content[..60] : ev.Content;
                        Program.W(argsPreview, ConsoleColor.DarkGray);
                        Console.WriteLine();
                        Program.W("AI: ", ConsoleColor.Cyan);
                        break;

                    case "tool_end":
                        var preview = ev.Content.Replace("\n", " ");
                        if (preview.Length > 120) preview = preview[..120];
                        Program.W($"\r  ✓  [{toolCount}] {ev.Name} → ", ConsoleColor.Green);
                        Program.W(preview, ConsoleColor.DarkGray);
                        Console.WriteLine();
                        break;

                    case "done":
                        if (ev.SessionId.Length > 0) _sessionId = ev.SessionId;
                        if (ev.Stopped) Program.WLYellow("  ⏹ 已停止");
                        break;

                    case "error":
                        Program.W($"\n  [错误] {ev.Content}", ConsoleColor.Red);
                        Console.WriteLine();
                        break;
                }
            }
        }
        catch (Exception e)
        {
            Program.W($"\n  [错误] {e.Message}", ConsoleColor.Red);
            Console.WriteLine();
        }

        if (fullReply.Length > 0 || toolCount > 0)
        {
            _msgCount++;
            Console.WriteLine();
        }
        else
        {
            Program.WLDim("(无回复)");
        }
    }

    // ---- /sessions ----
    private void HandleSessions()
    {
        var sessions = _api.GetSessions();
        if (sessions.Count == 0)
        {
            Program.WLDim("  (暂无会话)");
            return;
        }
        Program.WLDim("  会话列表:");
        for (int i = 0; i < sessions.Count; i++)
        {
            Program.W($"    [{i}]", ConsoleColor.Green);
            Program.W($" {sessions[i].Name}  ");
            Program.W($"{sessions[i].MessageCount}轮", ConsoleColor.DarkGray);
            Console.WriteLine();
        }
        Program.W("  选择编号 > ", ConsoleColor.Cyan);
        var choice = Console.ReadLine()?.Trim() ?? "";
        if (int.TryParse(choice, out var idx) && idx >= 0 && idx < sessions.Count)
        {
            _sessionId = sessions[idx].SessionId;
            _sessionName = sessions[idx].Name;
            _msgCount = sessions[idx].MessageCount;
            Program.W($"  ✓ 已切换到: {_sessionName}", ConsoleColor.Green);
            Console.WriteLine();
        }
    }

    // ---- /new ----
    private void HandleNew(string input)
    {
        var parts = input.Split(' ', 2);
        var name = parts.Length > 1 && parts[1].Trim().Length > 0 ? parts[1].Trim() : "终端对话";
        var newS = _api.CreateSession(name);
        if (newS != null)
        {
            _sessionId = newS.SessionId;
            _sessionName = newS.Name;
            _msgCount = 0;
            Program.W($"  ✓ 已创建: {_sessionName}", ConsoleColor.Green);
            Console.WriteLine();
        }
    }

    // ---- /undo ----
    private void HandleUndo()
    {
        if (_msgCount <= 0 || _sessionId.Length == 0)
        {
            Program.WLDim("  没有可撤回的消息");
            return;
        }
        if (_api.DeleteRound(_sessionId, _msgCount - 1))
        {
            _msgCount--;
            Program.W($"  ✓ 已撤回最后一轮对话 (剩余 {_msgCount} 轮)", ConsoleColor.Green);
            Console.WriteLine();
        }
        else
        {
            Program.WLRed("  撤回失败");
        }
    }

    // ---- /delete ----
    private void HandleDelete()
    {
        if (_sessionId.Length == 0) return;
        Program.W($"  确定删除会话「{_sessionName}」？(y/N): ", ConsoleColor.Yellow);
        var confirm = Console.ReadLine()?.Trim().ToLower() ?? "";
        if (confirm == "y" || confirm == "yes")
        {
            if (_api.DeleteSession(_sessionId))
            {
                Program.W($"  ✓ 已删除: {_sessionName}", ConsoleColor.Green);
                Console.WriteLine();
                var newS = _api.CreateSession("终端对话");
                if (newS != null)
                {
                    _sessionId = newS.SessionId;
                    _sessionName = newS.Name;
                    _msgCount = 0;
                    Program.WLGreen("  ✓ 已创建新会话");
                }
            }
            else
            {
                Program.WLRed("  删除失败");
            }
        }
        else
        {
            Program.WLDim("  已取消");
        }
    }

    // ---- /settings 设置界面 ----
    private void HandleSettings()
    {
        var settings = _api.GetSettings();
        var thinking = settings?["thinking_enabled"]?.GetValue<bool>() ?? true;

        while (true)
        {
            Console.WriteLine();
            Program.WLBold("  ⚙️  客户端设置");
            Program.WLDim("  ─" + new string('─', 40));
            Program.W("    [1] 思考模式 (Thinking):  ", ConsoleColor.Gray);
            Program.WL(thinking ? "✓ 开启" : "✗ 关闭", thinking ? ConsoleColor.Green : ConsoleColor.Red);
            Program.W("    [2] 模型名称:              ", ConsoleColor.Gray);
            Program.WL(settings?["model"]?.ToString() ?? "(默认)", ConsoleColor.DarkGray);
            Program.W("    [3] API Base URL:          ", ConsoleColor.Gray);
            Program.WL(settings?["base_url"]?.ToString() ?? "(默认)", ConsoleColor.DarkGray);
            Program.WL("    [0] 返回");
            Program.WLDim("  ─" + new string('─', 40));
            Program.W("  请选择要修改的设置 > ", ConsoleColor.Cyan);
            var choice = Console.ReadLine()?.Trim() ?? "";

            if (choice == "0") break;
            if (choice == "1")
            {
                Program.W($"    思考模式当前为 [{(thinking ? "开启" : "关闭")}]，输入 y 开启 / n 关闭: ", ConsoleColor.Gray);
                var ans = Console.ReadLine()?.Trim().ToLower() ?? "";
                if (ans is "y" or "yes")
                {
                    thinking = true;
                    _api.SaveSettings(new JsonObject { ["thinking_enabled"] = true });
                    Program.WLGreen("    ✓ 思考模式已开启");
                    Program.WLDim("    (下次对话生效，AI 会先思考再回答)");
                }
                else if (ans is "n" or "no")
                {
                    thinking = false;
                    _api.SaveSettings(new JsonObject { ["thinking_enabled"] = false });
                    Program.WLYellow("    ✓ 思考模式已关闭");
                }
                else Program.WLDim("    未更改");
            }
            else if (choice == "2")
            {
                Program.W($"    模型名称 [{settings?["model"]?.ToString() ?? "(默认)"}]: ", ConsoleColor.Gray);
                var m = Console.ReadLine()?.Trim() ?? "";
                if (m.Length > 0)
                {
                    _api.SaveSettings(new JsonObject { ["model"] = m });
                    settings = _api.GetSettings();
                    Program.WLGreen($"    ✓ 模型已更新为: {m}");
                }
                else Program.WLDim("    未更改");
            }
            else if (choice == "3")
            {
                Program.W($"    API Base URL [{settings?["base_url"]?.ToString() ?? "(默认)"}]: ", ConsoleColor.Gray);
                var u = Console.ReadLine()?.Trim() ?? "";
                if (u.Length > 0)
                {
                    _api.SaveSettings(new JsonObject { ["base_url"] = u });
                    settings = _api.GetSettings();
                    Program.WLGreen("    ✓ Base URL 已更新");
                }
                else Program.WLDim("    未更改");
            }
            else
            {
                Program.WLRed($"    无效选项: {choice}，请输入 0-3");
            }
        }
    }
}
