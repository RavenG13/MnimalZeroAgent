// ============================================================
//  ZeroAgentCli - 单文件 CLI 客户端（无外部依赖，NativeAOT）
//  连接 AI 大脑，支持登录 / SSE 流式聊天 / WebSocket 工具注册
//
//  颜色方案：使用 Console.ForegroundColor（跨平台原生 API，
//  不依赖 ANSI 转义，兼容双击打开的旧控制台 conhost）
//  编码方案：启动时强制控制台代码页为 UTF-8(65001)，解决中文乱码
// ============================================================
using System.Runtime.InteropServices;

namespace ZeroAgentCli;

class Program
{
    // Windows: 设置控制台代码页（65001 = UTF-8）
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern bool SetConsoleOutputCP(uint cp);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern bool SetConsoleCP(uint cp);

    // ---- 跨平台彩色输出 ----
    // 用法: Program.WL("文本", ConsoleColor.Green)
    //       Program.W("AI: ", ConsoleColor.Cyan)

    public static void W(string text, ConsoleColor? color = null)
    {
        if (color.HasValue) Console.ForegroundColor = color.Value;
        Console.Write(text);
        if (color.HasValue) Console.ResetColor();
    }

    public static void WL(string text = "", ConsoleColor? color = null)
    {
        if (color.HasValue) Console.ForegroundColor = color.Value;
        Console.WriteLine(text);
        if (color.HasValue) Console.ResetColor();
    }

    // ---- 便捷颜色别名 ----
    public static void WGreen(string s)  => W(s, ConsoleColor.Green);
    public static void WCyan(string s)   => W(s, ConsoleColor.Cyan);
    public static void WYellow(string s) => W(s, ConsoleColor.Yellow);
    public static void WRed(string s)    => W(s, ConsoleColor.Red);
    public static void WDim(string s)    => W(s, ConsoleColor.DarkGray);

    public static void WLGreen(string s)  => WL(s, ConsoleColor.Green);
    public static void WLCyan(string s)   => WL(s, ConsoleColor.Cyan);
    public static void WLYellow(string s) => WL(s, ConsoleColor.Yellow);
    public static void WLRed(string s)    => WL(s, ConsoleColor.Red);
    public static void WLBold(string s)   => WL(s, ConsoleColor.White);
    public static void WLDim(string s)    => WL(s, ConsoleColor.DarkGray);

    static void Main(string[] args)
    {
        // ---- 强制 UTF-8 控制台，解决中文乱码 ----
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            try
            {
                SetConsoleOutputCP(65001);  // 输出代码页 UTF-8
                SetConsoleCP(65001);        // 输入代码页 UTF-8
            }
            catch { /* 忽略 */ }
        }
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        Console.InputEncoding = System.Text.Encoding.UTF8;

        PrintBanner();

        try
        {
            var config = Config.Collect(args);
            if (config == null) return;

            W("  ⏳ 正在登录...", ConsoleColor.DarkGray);
            Console.WriteLine();
            var api = new ApiClient(config);
            var token = api.Login();
            WL("  ✓ 登录成功", ConsoleColor.Green);

            // 启动 WebSocket 工具注册（后台）
            using var cts = new CancellationTokenSource();
            _ = Task.Run(() => WsClient.RunAsync(config, token, cts.Token));

            // 聊天循环
            var chat = new ChatConsole(config, token, cts);
            chat.RunAsync().GetAwaiter().GetResult();
        }
        catch (Exception ex)
        {
            WL($"  ✗ {ex.Message}", ConsoleColor.Red);
        }

        Console.WriteLine();
        WL("  客户端已退出", ConsoleColor.DarkGray);
    }

    static void PrintBanner()
    {
        Console.WriteLine();
        WLCyan("  ╔" + new string('═', 48) + "╗");
        W("  ║", ConsoleColor.Cyan);
        W("  ZeroAgent CLI Client".PadLeft(25).PadRight(46), ConsoleColor.White);
        WLCyan("║");
        W("  ║", ConsoleColor.Cyan);
        W("  连接 AI 大脑 · 单文件原生可执行".PadLeft(29).PadRight(46), ConsoleColor.DarkGray);
        WLCyan("║");
        WLCyan("  ╚" + new string('═', 48) + "╝");
        Console.WriteLine();
    }
}
