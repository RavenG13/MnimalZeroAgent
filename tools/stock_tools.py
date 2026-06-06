import requests
import json
from datetime import datetime
import re

# ============================================================
#  股票信息工具 - 使用网页搜索获取A股市场数据
# ============================================================

stock_tools = [
    {
        "type": "function",
        "function": {
            "name": "stock_sector_ranks",
            "description": "获取A股行业板块涨幅排行榜。通过搜索最新行情数据，返回哪些行业板块最近涨得好。",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "返回前N个板块，默认10",
                    },
                    "order": {
                        "type": "string",
                        "description": "排序方式，'desc'降序（涨幅最高在前），'asc'升序（跌幅最大在前），默认'desc'",
                        "enum": ["desc", "asc"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_market_index",
            "description": "获取A股主要指数行情（上证指数、深证成指、创业板指等）。通过搜索最新指数数据。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_concept_ranks",
            "description": "获取A股概念板块涨幅排行榜，显示哪些概念题材最近热炒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {
                        "type": "integer",
                        "description": "返回前N个概念板块，默认10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_company_news",
            "description": "查询指定股票或公司的近期新闻和公告。返回新闻标题、发布时间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码或公司名称，如 '000001'、'600519'、'贵州茅台'等",
                    },
                    "max_news": {
                        "type": "integer",
                        "description": "返回的新闻数量，默认5",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stock_financial_news",
            "description": "获取A股市场最新财经新闻和要闻。返回新闻标题和链接。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_news": {
                        "type": "integer",
                        "description": "返回的新闻数量，默认5",
                    },
                },
                "required": [],
            },
        },
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _search_bing(query: str, max_results: int = 5) -> list:
    """使用Bing搜索"""
    try:
        from urllib.parse import quote
        url = f"https://www.bing.com/search?q={quote(query)}&count={max_results}&setlang=zh-Hans"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
        
        results = []
        li_pattern = r'<li[^>]*class="b_algo"[^>]*>(.*?)</li>'
        lis = re.findall(li_pattern, html, re.DOTALL)
        
        for li in lis[:max_results]:
            title_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', li, re.DOTALL)
            if title_match:
                url_found = title_match.group(1)
                title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()
                snippet_match = re.search(r'<p[^>]*>(.*?)</p>', li, re.DOTALL)
                snippet = ""
                if snippet_match:
                    snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
                    snippet = re.sub(r'\s+', ' ', snippet)[:300]
                
                if title and url_found and "bing.com" not in url_found:
                    results.append({
                        "title": title,
                        "url": url_found,
                        "content": snippet,
                    })
        
        return results[:max_results]
    except Exception as e:
        return [{"title": "[搜索失败]", "url": "", "content": str(e)}]


def stock_sector_ranks(top_n: int = 10, order: str = "desc") -> str:
    """获取行业板块涨幅排行榜"""
    try:
        results = _search_bing("A股行业板块涨幅排行榜 今日行情", max_results=5)
        
        order_label = "涨幅" if order == "desc" else "跌幅"
        lines = [f"A股行业板块{order_label}排行榜"]
        lines.append("=" * 50)
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   {url}")
            if content:
                lines.append(f"   {content[:200]}")
        
        if not results:
            # 用搜索词返回代替
            lines.append("\n(尝试搜索行业板块数据...)")
            extra = _search_bing("今日A股领涨板块 行业涨幅榜", max_results=5)
            for item in extra:
                lines.append(f"\n- {item.get('title','')}")
                if item.get('content'):
                    lines.append(f"  {item['content'][:200]}")
        
        lines.append(f"\n数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取行业板块数据失败: {e}"


def stock_market_index() -> str:
    """获取主要指数行情"""
    try:
        results = _search_bing("上证指数 深证成指 创业板指 今日行情 最新点数", max_results=5)
        
        lines = ["A股主要指数行情"]
        lines.append("=" * 50)
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   {url}")
            if content:
                lines.append(f"   {content[:200]}")
        
        if not results:
            extra = _search_bing("今日A股大盘行情 上证指数", max_results=3)
            for item in extra:
                lines.append(f"\n- {item.get('title','')}")
                if item.get('content'):
                    lines.append(f"  {item['content'][:200]}")
        
        lines.append(f"\n数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取指数数据失败: {e}"


def stock_concept_ranks(top_n: int = 10) -> str:
    """获取概念板块涨幅排行榜"""
    try:
        results = _search_bing("A股概念板块涨幅榜 热门概念 今日", max_results=5)
        
        lines = ["A股概念板块涨幅排行榜"]
        lines.append("=" * 50)
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   {url}")
            if content:
                lines.append(f"   {content[:200]}")
        
        lines.append(f"\n数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取概念板块数据失败: {e}"


def stock_company_news(code: str, max_news: int = 5) -> str:
    """查询指定股票的近期新闻"""
    try:
        results = _search_bing(f"股票 {code} 最新新闻 公告", max_results=max_news)
        
        lines = [f"股票 {code} 近期新闻"]
        lines.append("=" * 50)
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   链接: {url}")
            if content:
                lines.append(f"   摘要: {content[:200]}")
        
        if not results:
            lines.append("\n暂无相关新闻")
        
        lines.append(f"\n数据时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取股票 {code} 新闻失败: {e}"


def stock_financial_news(max_news: int = 5) -> str:
    """获取A股市场最新财经新闻"""
    try:
        results = _search_bing("A股 财经新闻 最新 今日要闻", max_results=max_news)
        
        lines = ["A股市场最新财经新闻"]
        lines.append("=" * 50)
        
        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "")
            lines.append(f"\n{i}. {title}")
            if url:
                lines.append(f"   链接: {url}")
            if content:
                lines.append(f"   {content[:200]}")
        
        if not results:
            lines.append("\n暂无新闻数据")
        
        lines.append(f"\n更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
    except Exception as e:
        return f"获取财经新闻失败: {e}"
