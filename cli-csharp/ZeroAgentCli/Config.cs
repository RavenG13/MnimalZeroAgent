// ============================================================
//  Config - 连接配置收集（命令行参数 + 交互输入）
// ============================================================

namespace ZeroAgentCli;

public class ClientConfig
{
    public string Server { get; set; } = "http://127.0.0.1:8010";
    public string Username { get; set; } = "";
    public string Password { get; set; } = "";
    public string Device { get; set; } = "";
    public bool Interactive { get; set; } = false;   // 默认自动执行模式
    public string WorkRoot { get; set; } = "";
}

public static class Config
{
    public static ClientConfig? Collect(string[] args)
    {
        var cfg = new ClientConfig();

        // ---- 命令行参数 ----
        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--server" or "-s":
                    if (i + 1 < args.Length) cfg.Server = args[++i].TrimEnd('/');
                    break;
                case "--root" or "-r":
                    if (i + 1 < args.Length) cfg.WorkRoot = args[++i];
                    break;
                case "--device" or "-d":
                    if (i + 1 < args.Length) cfg.Device = args[++i];
                    break;
                case "--mode" or "-m":
                    // auto → 自动执行；interactive → 需确认
                    if (i + 1 < args.Length) cfg.Interactive = args[++i] != "auto";
                    break;
                case "--help" or "-h":
                    PrintHelp();
                    return null;
            }
        }

        Program.WLBold("▶ 连接设置");
        Console.WriteLine();

        // 服务器地址
        var server = Prompt($"  服务器地址 [{cfg.Server}]: ", cfg.Server);
        cfg.Server = server.TrimEnd('/');

        // 用户名
        while (string.IsNullOrWhiteSpace(cfg.Username))
        {
            cfg.Username = Prompt("  用户名: ", "").Trim();
            if (string.IsNullOrWhiteSpace(cfg.Username))
                Program.WLRed("    用户名不能为空");
        }

        // 密码（不回显）
        while (string.IsNullOrWhiteSpace(cfg.Password))
        {
            cfg.Password = ReadPassword("  密码: ");
            if (string.IsNullOrWhiteSpace(cfg.Password))
                Program.WLRed("    密码不能为空");
        }

        // 设备名称
        string hostname = Environment.MachineName;
        if (string.IsNullOrEmpty(cfg.Device))
            cfg.Device = hostname;
        Program.W($"  设备名称 [{cfg.Device}]: ", ConsoleColor.Green);
        cfg.Device = (Console.ReadLine()?.Trim() ?? "").Trim();
        if (string.IsNullOrEmpty(cfg.Device)) cfg.Device = hostname;

        // 工作目录
        if (string.IsNullOrEmpty(cfg.WorkRoot))
            cfg.WorkRoot = Prompt("  工作目录限制 (留空=无限制): ", "").Trim();
        if (!string.IsNullOrEmpty(cfg.WorkRoot))
        {
            cfg.WorkRoot = Path.GetFullPath(Environment.ExpandEnvironmentVariables(cfg.WorkRoot));
            Program.W($"  → 工作目录限制为: {cfg.WorkRoot}", ConsoleColor.Yellow);
            Console.WriteLine();
        }

        Console.WriteLine();
        Program.WLBold("▶ 配置摘要");
        Program.W($"  服务器:   {cfg.Server}", ConsoleColor.Cyan);
        Console.WriteLine();
        Console.WriteLine($"  用户:     {cfg.Username}");
        Program.W($"  设备名称: {cfg.Device}", ConsoleColor.Green);
        Console.WriteLine();
        Program.W($"  安全模式: {(cfg.Interactive ? "交互确认" : "自动执行")}", ConsoleColor.Yellow);
        Console.WriteLine();
        Program.W($"  工作目录: {(cfg.WorkRoot.Length > 0 ? cfg.WorkRoot : "无限制")}",
            cfg.WorkRoot.Length > 0 ? ConsoleColor.Gray : ConsoleColor.DarkGray);
        Console.WriteLine();
        Console.WriteLine();
        return cfg;
    }

    static void PrintHelp()
    {
        Console.WriteLine("用法: zeroagent-cli [选项]");
        Console.WriteLine();
        Console.WriteLine("选项:");
        Console.WriteLine("  -s, --server <url>    服务器地址 (默认 http://127.0.0.1:8010)");
        Console.WriteLine("  -u, --username <name> 用户名 (不填则交互输入)");
        Console.WriteLine("  -p, --password <pass> 密码 (不填则交互输入)");
        Console.WriteLine("  -d, --device <name>   设备名称 (默认主机名)");
        Console.WriteLine("  -r, --root <dir>      工作目录限制");
        Console.WriteLine("  -m, --mode <mode>     安全模式: auto=自动执行(默认) / interactive=需确认");
        Console.WriteLine("  -h, --help            显示帮助");
    }

    static string Prompt(string prompt, string defaultValue)
    {
        Console.Write(prompt);
        var input = Console.ReadLine()?.Trim() ?? "";
        return input.Length > 0 ? input : defaultValue;
    }

    // ---- 密码输入（不回显；输入被重定向时回退为普通读取）----
    static string ReadPassword(string prompt)
    {
        Console.Write(prompt);
        try
        {
            var chars = new List<char>();
            while (true)
            {
                var key = Console.ReadKey(true);
                if (key.Key == ConsoleKey.Enter)
                {
                    Console.WriteLine();
                    break;
                }
                if (key.Key == ConsoleKey.Backspace)
                {
                    if (chars.Count > 0)
                    {
                        chars.RemoveAt(chars.Count - 1);
                        Console.Write("\b \b");
                    }
                    continue;
                }
                if (key.KeyChar >= 32)
                {
                    chars.Add(key.KeyChar);
                    Console.Write("*");
                }
            }
            return new string(chars.ToArray());
        }
        catch (InvalidOperationException)
        {
            // 输入被重定向（管道/文件），无法 ReadKey，回退普通读取
            return Console.ReadLine()?.Trim() ?? "";
        }
    }
}
