// ============================================================
//  NodeTools - 固定工具实现（与 Python client/node_tools.py 一致）
//  工具: read_file / write_file / list_files / run_shell / get_system_info
//  不依赖外部库，纯 .NET 标准库
// ============================================================
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace ZeroAgentCli;

public static class NodeTools
{
    // ---- 固定工具 JSON Schema（用于注册给服务器）----
    public static readonly JsonSchema[] Schemas =
    {
        new JsonSchema("read_file",
            "读取客户端本地文件的内容。支持文本文件和二进制文件。",
            new (string Name, string Type, string Desc, bool Required)[] {
                ("path", "string", "文件路径（绝对路径或相对于工作目录的路径）", true)
            }),
        new JsonSchema("write_file",
            "将内容写入客户端本地文件。如果文件已存在则覆盖，父目录不存在则自动创建。",
            new (string Name, string Type, string Desc, bool Required)[] {
                ("path", "string", "写入路径（绝对路径或相对于工作目录的路径）", true),
                ("content", "string", "要写入的文件内容", true)
            }),
        new JsonSchema("list_files",
            "列出客户端本地目录中的文件和子目录。如果不传 path 则列出工作目录。",
            new (string Name, string Type, string Desc, bool Required)[] {
                ("path", "string", "目录路径（可选，默认工作目录）", false)
            }),
        new JsonSchema("run_shell",
            "在客户端本地执行 shell 命令并返回输出。可用于运行脚本、安装软件、管理系统等。支持指定 timeout（秒），默认 60 秒。",
            new (string Name, string Type, string Desc, bool Required)[] {
                ("command", "string", "要执行的 shell 命令", true),
                ("timeout", "integer", "超时秒数，默认 60，最大 300", false)
            }),
        new JsonSchema("get_system_info",
            "获取客户端设备的系统信息：操作系统、主机名、当前工作目录、运行时版本等。",
            Array.Empty<(string, string, string, bool)>()),
    };

    // ============================================================
    //  工具执行
    // ============================================================
    public static string Execute(string name, Dictionary<string, object?> args, string root)
    {
        try
        {
            return name switch
            {
                "read_file" => ReadFile(GetArg(args, "path", "")!, root),
                "write_file" => WriteFile(GetArg(args, "path", "")!, GetArg(args, "content", "")!, root),
                "list_files" => ListFiles(GetArg(args, "path", "") ?? "", root),
                "run_shell" => RunShell(GetArg(args, "command", "")!, GetIntArg(args, "timeout", 60), root),
                "get_system_info" => GetSystemInfo(root),
                _ => $"[错误] 未知工具: {name}",
            };
        }
        catch (Exception e)
        {
            return $"[错误] 工具执行异常 ({name}): {e.Message}";
        }
    }

    static string? GetArg(Dictionary<string, object?> args, string key, string? def)
    {
        if (args.TryGetValue(key, out var v) && v is not null)
            return v.ToString();
        return def;
    }

    static int GetIntArg(Dictionary<string, object?> args, string key, int def)
    {
        if (args.TryGetValue(key, out var v) && v is not null && int.TryParse(v.ToString(), out var n))
            return Math.Clamp(n, 1, 300);
        return def;
    }

    // ---- 路径安全检查 ----
    static string ResolvePath(string path, string root)
    {
        string full;
        if (Path.IsPathRooted(path))
            full = Path.GetFullPath(path);
        else if (root.Length > 0)
            full = Path.GetFullPath(Path.Combine(root, path));
        else
            full = Path.GetFullPath(path);

        if (root.Length > 0)
        {
            var rootFull = Path.GetFullPath(root);
            if (!full.StartsWith(rootFull + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                && !full.Equals(rootFull, StringComparison.OrdinalIgnoreCase))
            {
                throw new UnauthorizedAccessException(
                    $"路径越界！允许的根目录是 {rootFull}，请求的路径解析为 {full}");
            }
        }
        return full;
    }

    // ---- read_file ----
    static string ReadFile(string path, string root)
    {
        if (string.IsNullOrWhiteSpace(path))
            return "[错误] 参数 path 不能为空";
        var full = ResolvePath(path, root);
        if (!File.Exists(full))
            return $"[错误] 文件不存在: {full}";
        if (Directory.Exists(full))
            return $"[错误] 路径是目录而非文件: {full}";

        try
        {
            var fi = new FileInfo(full);
            string content;
            if (fi.Length > 5 * 1024 * 1024)
            {
                // 大文件读取前 3000 字符
                using var reader = new StreamReader(full, Encoding.UTF8);
                var buf = new char[3000];
                int read = reader.Read(buf, 0, 3000);
                content = new string(buf, 0, read);
                return $"[已截断] 文件大小 {fi.Length / 1024.0 / 1024.0:.1f} MB，仅显示前 3000 字符:\n{new string('-', 40)}\n{content}\n{new string('-', 40)}";
            }
            content = File.ReadAllText(full, Encoding.UTF8);
            return content;
        }
        catch (Exception e)
        {
            return $"[错误] 读取文件失败: {e.Message}";
        }
    }

    // ---- write_file ----
    static string WriteFile(string path, string content, string root)
    {
        if (string.IsNullOrWhiteSpace(path))
            return "[错误] 参数 path 不能为空";
        var full = ResolvePath(path, root);
        try
        {
            var dir = Path.GetDirectoryName(full);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.WriteAllText(full, content, Encoding.UTF8);
            var sizeKb = Encoding.UTF8.GetByteCount(content) / 1024.0;
            return $"[成功] 已写入: {full} ({sizeKb:.1f} KB)";
        }
        catch (Exception e)
        {
            return $"[错误] 写入文件失败: {e.Message}";
        }
    }

    // ---- list_files ----
    static string ListFiles(string path, string root)
    {
        var full = ResolvePath(string.IsNullOrWhiteSpace(path) ? "." : path, root);
        if (!Directory.Exists(full))
            return $"[错误] 目录不存在: {full}";

        try
        {
            var entries = Directory.GetFileSystemEntries(full)
                .Select(Path.GetFileName)
                .Where(n => n != null)
                .Select(n => n!)
                .OrderBy(x => x, StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (entries.Count == 0)
                return $"[空] {full} 中没有文件";

            var lines = new List<string>
            {
                $"目录: {full}",
                $"共 {entries.Count} 个项目",
                new string('-', 40)
            };
            foreach (var name in entries)
            {
                var itemPath = Path.Combine(full, name);
                string prefix = Directory.Exists(itemPath) ? "📁" : "📄";
                string sizeStr = "";
                if (!Directory.Exists(itemPath))
                {
                    try { sizeStr = $" ({FormatSize(new FileInfo(itemPath).Length)})"; } catch { }
                }
                lines.Add($"  {prefix} {name}{sizeStr}");
            }
            return string.Join("\n", lines);
        }
        catch (Exception e)
        {
            return $"[错误] 列出目录失败: {e.Message}";
        }
    }

    // ---- run_shell ----
    static string RunShell(string command, int timeout, string root)
    {
        if (string.IsNullOrWhiteSpace(command))
            return "[错误] 参数 command 不能为空";
        timeout = Math.Clamp(timeout, 1, 300);

        var psi = new ProcessStartInfo
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };

        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            psi.FileName = "cmd.exe";
            psi.Arguments = $"/c {command}";
        }
        else
        {
            psi.FileName = "/bin/sh";
            psi.Arguments = $"-c \"{command}\"";
        }

        if (!string.IsNullOrEmpty(root))
            psi.WorkingDirectory = root;

        try
        {
            using var proc = Process.Start(psi);
            if (proc is null) return "[错误] 无法启动进程";

            var stdoutTask = proc.StandardOutput.ReadToEndAsync();
            var stderrTask = proc.StandardError.ReadToEndAsync();

            if (!proc.WaitForExit(timeout * 1000))
            {
                try { proc.Kill(entireProcessTree: true); } catch { }
                return $"[超时] 命令在 {timeout}s 后未完成，已终止";
            }

            var stdout = stdoutTask.GetAwaiter().GetResult();
            var stderr = stderrTask.GetAwaiter().GetResult();

            var output = "";
            if (!string.IsNullOrEmpty(stdout)) output += stdout;
            if (!string.IsNullOrEmpty(stderr))
            {
                if (output.Length > 0 && !output.EndsWith("\n")) output += "\n";
                output += stderr;
            }
            if (string.IsNullOrWhiteSpace(output))
                output = $"[无输出] exit_code={proc.ExitCode}";
            return output.TrimEnd();
        }
        catch (Exception e)
        {
            return $"[错误] 执行命令失败: {e.Message}";
        }
    }

    // ---- get_system_info ----
    static string GetSystemInfo(string root)
    {
        string os, arch;
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            os = $"Windows {Environment.OSVersion.Version}";
            arch = RuntimeInformation.OSArchitecture.ToString();
        }
        else if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))
        {
            os = $"Linux {Environment.OSVersion.Version}";
            arch = RuntimeInformation.OSArchitecture.ToString();
        }
        else
        {
            os = RuntimeInformation.OSDescription;
            arch = RuntimeInformation.OSArchitecture.ToString();
        }

        var cwd = root.Length > 0 ? Path.GetFullPath(root) : Directory.GetCurrentDirectory();
        return string.Join("\n", new[]
        {
            "=== 客户端系统信息 ===",
            $"  操作系统: {os}",
            $"  主机名:   {Environment.MachineName}",
            $"  架构:     {arch}",
            $"  运行时:   {RuntimeInformation.FrameworkDescription}",
            $"  工作目录: {cwd}",
        });
    }

    static string FormatSize(long bytes)
    {
        string[] units = { "B", "KB", "MB", "GB" };
        double size = bytes;
        int i = 0;
        while (size >= 1024 && i < units.Length - 1) { size /= 1024; i++; }
        return i == 0 ? $"{size:0}{units[i]}" : $"{size:0.0}{units[i]}";
    }
}

// ---- 工具 JSON Schema 描述 ----
public class JsonSchema
{
    public string Name { get; }
    public string Description { get; }
    public (string Name, string Type, string Desc, bool Required)[] Params { get; }

    public JsonSchema(string name, string description, (string, string, string, bool)[] parameters)
    {
        Name = name;
        Description = description;
        Params = parameters;
    }

    /// <summary>生成 OpenAI 格式的工具 schema JSON 字符串</summary>
    public string ToToolJson()
    {
        var sb = new StringBuilder();
        sb.Append("{\"type\":\"function\",\"function\":{\"name\":");
        sb.Append(JsonUtil.Str(Name));
        sb.Append(",\"description\":");
        sb.Append(JsonUtil.Str(Description));
        sb.Append(",\"parameters\":{\"type\":\"object\",\"properties\":{");

        for (int i = 0; i < Params.Length; i++)
        {
            if (i > 0) sb.Append(",");
            sb.Append(JsonUtil.Str(Params[i].Name));
            sb.Append(":{\"type\":");
            sb.Append(JsonUtil.Str(Params[i].Type));
            sb.Append(",\"description\":");
            sb.Append(JsonUtil.Str(Params[i].Desc));
            sb.Append("}");
        }

        sb.Append("},\"required\":[");
        var reqs = Params.Where(p => p.Required).ToList();
        for (int i = 0; i < reqs.Count; i++)
        {
            if (i > 0) sb.Append(",");
            sb.Append(JsonUtil.Str(reqs[i].Name));
        }
        sb.Append("]}}}");
        return sb.ToString();
    }
}
