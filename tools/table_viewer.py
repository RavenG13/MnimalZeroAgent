import os
import pandas as pd
import sys
from openai.types.chat import ChatCompletionToolParam

# ---- 本模块的工具定义 ----
table_tools: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "read_table",
            "description": "读取表格文件（CSV、Excel、TSV、JSON）并返回前10行+后5行+列信息+数值统计摘要。尽量不要使用emoji操作Excel，用索引访问sheet比用名字更稳定",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "表格文件路径"
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "Excel 工作表名称，默认为第一个 sheet"
                    },
                    "nrows": {
                        "type": "integer",
                        "description": "限制读取的行数（可选）"
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_table",
            "description": "对表格文件执行 pandas query 表达式进行条件筛选查询。例如：'年龄 > 30' 或 '城市 == \"北京\"'",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "表格文件路径"
                    },
                    "query": {
                        "type": "string",
                        "description": "查询条件表达式，如：'年龄 > 30'"
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "Excel 工作表名称（可选）"
                    }
                },
                "required": ["file_path", "query"],
            },
        },
    },
]


def read_table(file_path: str, sheet_name=None, nrows=None, head_only=False):
    """
    读取表格文件并返回内容摘要。
    支持 CSV、Excel (.xlsx/.xls)、TSV、JSON 等格式。
    """
    if not os.path.exists(file_path):
        return f"错误：文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else ","
            if nrows:
                df = pd.read_csv(file_path, sep=sep, nrows=nrows)
            else:
                df = pd.read_csv(file_path, sep=sep)
        elif ext in (".xlsx", ".xls"):
            if sheet_name is None:
                # 默认读取第一个 sheet
                df = pd.read_excel(file_path, sheet_name=0, nrows=nrows)
            else:
                df = pd.read_excel(file_path, sheet_name=sheet_name, nrows=nrows)
        elif ext == ".json":
            df = pd.read_json(file_path)
            if nrows:
                df = df.head(nrows)
        else:
            return f"错误：不支持的文件格式 '{ext}'，支持 CSV、TSV、Excel、JSON"

    except Exception as e:
        return f"错误：读取文件失败 - {e}"

    # 统计数据信息
    rows, cols = df.shape
    info = f"[表格信息] {rows} 行 x {cols} 列\n"
    info += f"[列名] {', '.join(str(c) for c in df.columns)}\n"

    # 各列数据类型
    dtypes_info = df.dtypes.to_string()
    info += f"[数据类型]\n{dtypes_info}\n\n"

    if head_only:
        info += "[前几行数据]\n"
        info += df.head(10).to_string(index=False)
    else:
        info += "[前 10 行数据]\n"
        info += df.head(10).to_string(index=False)
        info += "\n\n[后 5 行数据]\n"
        info += df.tail(5).to_string(index=False)

    # 基本统计信息（数值列）
    numeric_cols = df.select_dtypes(include=["number"]).columns
    if len(numeric_cols) > 0:
        info += "\n\n[数值列统计]\n"
        info += df[numeric_cols].describe().to_string()

    return info


def query_table(file_path: str, query: str, sheet_name=None):
    """
    对表格执行 pandas 查询（query 表达式）。
    例如：query="年龄 > 30" 或 query="城市 == '北京'"
    """
    if not os.path.exists(file_path):
        return f"错误：文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else ","
            df = pd.read_csv(file_path, sep=sep)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, sheet_name=sheet_name or 0)
        elif ext == ".json":
            df = pd.read_json(file_path)
        else:
            return f"错误：不支持的文件格式 '{ext}'"

        result = df.query(query)
        rows, cols = result.shape
        output = f"[查询结果] {rows} 行 x {cols} 列\n"
        output += f"[查询条件] {query}\n\n"
        output += result.to_string(index=False)
        return output

    except Exception as e:
        return f"错误：查询失败 - {e}"


def group_table(file_path: str, by: str, agg: str = "mean", sheet_name=None):
    """
    对表格进行分组聚合。
    by: 分组列名
    agg: 聚合方式 (mean, sum, count, max, min)
    """
    if not os.path.exists(file_path):
        return f"错误：文件不存在 - {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else ","
            df = pd.read_csv(file_path, sep=sep)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(file_path, sheet_name=sheet_name or 0)
        elif ext == ".json":
            df = pd.read_json(file_path)
        else:
            return f"错误：不支持的文件格式 '{ext}'"

        result = df.groupby(by).agg(agg)
        output = f"[分组聚合结果] 按 '{by}' 列，聚合方式：{agg}\n"
        output += f"共 {len(result)} 个分组\n\n"
        output += result.to_string()
        return output

    except Exception as e:
        return f"错误：分组失败 - {e}"


if __name__ == "__main__":
    print("=" * 60)
    print("[表格查看工具] (pandas)")
    print("支持格式: CSV, TSV, Excel (.xlsx/.xls), JSON")
    print("=" * 60)

    while True:
        file_path = input("\n[文件路径] (输入 exit 退出): ").strip()
        if file_path.lower() == "exit":
            break

        if not os.path.exists(file_path):
            print(f"[错误] 文件不存在：{file_path}")
            continue

        print("\n请选择操作:")
        print("  1. 查看表格概览（前10行+后5行+统计）")
        print("  2. 执行查询（条件筛选）")
        print("  3. 分组聚合")
        choice = input("请输入编号 (1/2/3): ").strip()

        if choice == "1":
            print(read_table(file_path))
        elif choice == "2":
            query = input("请输入查询条件 (如: 年龄 > 30): ").strip()
            print(query_table(file_path, query))
        elif choice == "3":
            by = input("请输入分组列名: ").strip()
            agg = input("请输入聚合方式 (mean/sum/count/max/min): ").strip() or "mean"
            print(group_table(file_path, by, agg))
        else:
            print("[错误] 无效选择")
