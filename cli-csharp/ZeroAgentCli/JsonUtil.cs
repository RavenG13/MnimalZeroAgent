// ============================================================
//  JsonUtil - 手动 JSON 转义工具
//  NativeAOT 下避免 JsonSerializer 反射（AOT 安全）
// ============================================================
using System.Text;

namespace ZeroAgentCli;

public static class JsonUtil
{
    /// <summary>将字符串转义为合法的 JSON 字符串字面量（含引号）</summary>
    public static string Str(string s)
    {
        if (s == null) return "null";
        var sb = new StringBuilder(s.Length + 2);
        sb.Append('"');
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (c < 0x20)
                        sb.Append("\\u").Append(((int)c).ToString("x4"));
                    else
                        sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
        return sb.ToString();
    }
}
