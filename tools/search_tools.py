import requests
import json
import re
from urllib.parse import quote

# ============================================================
#  搜索引擎工具 - 使用 Bing 网页搜索 + 网页内容查看
# ============================================================

search_tools = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取最新信息。使用公共搜索引擎搜索关键词，返回标题、链接和摘要。适合查找新闻、技术文档、百科知识等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，应简洁明确",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回的最大结果数量，默认 5 条",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "查看指定网页的详细内容。输入一个URL，返回该网页的纯文本内容（去除HTML标签），适合阅读文章、文档、新闻的正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要查看的网页完整URL，必须以 http:// 或 https:// 开头",
                    },
                    "max_length": {
                        "type": "integer",
                        "description": "返回的最大字符数，默认3000字符",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ============================================================
#  搜索功能
# ============================================================

def _search_bing(query: str, max_results: int = 5) -> list:
    """使用 Bing 搜索（通过网页抓取方式）"""
    try:
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
                
                if title and url_found and not url_found.startswith("https://www.bing.com/"):
                    results.append({
                        "title": title,
                        "url": url_found,
                        "content": snippet,
                    })
        
        if results:
            return results[:max_results]
        
        # 备用解析方式
        alt_pattern = r'<h2[^>]*><a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a></h2>'
        alt_matches = re.findall(alt_pattern, html, re.DOTALL)
        for url_found, title_html in alt_matches[:max_results]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            title = re.sub(r'\s+', ' ', title)
            results.append({
                "title": title,
                "url": url_found,
                "content": "",
            })
        
        return results[:max_results] if results else []
        
    except requests.exceptions.Timeout:
        return [{"title": "[超时]", "url": "", "content": "搜索请求超时，请稍后重试。"}]
    except requests.exceptions.ConnectionError:
        return [{"title": "[网络错误]", "url": "", "content": "无法连接到搜索引擎，请检查网络。"}]
    except Exception as e:
        return [{"title": "[搜索错误]", "url": "", "content": f"搜索失败: {str(e)}"}]


def _search_duckduckgo(query: str, max_results: int = 5) -> list:
    """使用 DuckDuckGo HTML 搜索"""
    try:
        params = {"q": query, "ia": "web"}
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text
        
        results = []
        link_pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        links = re.findall(link_pattern, html, re.DOTALL)
        
        for i, (url_found, title_html) in enumerate(links[:max_results]):
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            title = re.sub(r'\s+', ' ', title)
            
            if title and url_found:
                if url_found.startswith("//"):
                    url_found = "https:" + url_found
                results.append({
                    "title": title,
                    "url": url_found,
                    "content": "",
                })
        
        return results[:max_results] if results else []
        
    except Exception as e:
        return [{"title": "[搜索错误]", "url": "", "content": f"DuckDuckGo 搜索失败: {str(e)}"}]


def web_search(query: str, max_results: int = 5) -> str:
    """
    搜索互联网并返回格式化结果。
    
    参数:
        query: 搜索关键词
        max_results: 返回结果数量（默认5）
    
    返回:
        格式化的搜索结果文本
    """
    engines = [
        ("Bing", _search_bing),
        ("DuckDuckGo", _search_duckduckgo),
    ]
    
    all_results = []
    used_engine = "未知"
    error_info = ""
    
    for name, func in engines:
        results = func(query, max_results)
        if results:
            non_error = [r for r in results if not r.get("title", "").startswith("[")]
            if non_error:
                all_results = non_error[:max_results]
                used_engine = name
                break
            else:
                error_info = results[0].get("content", "")
    
    if not all_results:
        if error_info:
            return f"[搜索无结果] {error_info}"
        return "[搜索无结果] 所有搜索引擎均不可用。"
    
    lines = [f"[搜索结果] 来源: {used_engine}"]
    for i, item in enumerate(all_results, 1):
        title = item.get("title", "无标题")
        url = item.get("url", "")
        content = item.get("content", "")
        lines.append("")
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"    链接: {url}")
        if content:
            # 清理特殊字符
            c = content.replace('\u203a', '>').replace('\u2039', '<')
            lines.append(f"    摘要: {c[:300]}")
    
    return "\n".join(lines)


# ============================================================
#  网页内容查看功能
# ============================================================

def _extract_text_from_html(html: str) -> str:
    """从HTML中提取可读的纯文本内容"""
    # 移除 script 和 style 标签
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', '', html, flags=re.DOTALL)
    
    # 替换常见标签为换行
    html = re.sub(r'</?(?:br|p|div|h[1-6]|li|tr|td|th|blockquote|pre|section|article|header|footer)[^>]*>', '\n', html)
    html = re.sub(r'<[^>]+>', ' ', html)
    
    # 清理空白
    text = re.sub(r'&nbsp;', ' ', html)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#\d+;', ' ', text)
    
    # 合并多余空白行
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r' +', ' ', text)
    
    return text.strip()


def web_fetch(url: str, max_length: int = 3000) -> str:
    """
    查看指定网页的详细内容。
    
    参数:
        url: 网页完整URL
        max_length: 返回的最大字符数（默认3000）
    
    返回:
        网页的纯文本内容
    """
    # URL校验
    if not url.startswith(("http://", "https://")):
        return f"[错误] URL格式不正确，需要以 http:// 或 https:// 开头。当前URL: {url}"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        
        # 检测编码
        encoding = resp.encoding if resp.encoding else "utf-8"
        html = resp.text
        
        # 提取正文
        text = _extract_text_from_html(html)
        
        if not text:
            # 如果提取为空，尝试用原始方式
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
        
        if not text:
            return f"[内容为空] 无法从 {url} 提取到有效内容。"
        
        # 截取长度
        if len(text) > max_length:
            text = text[:max_length] + f"\n\n...(内容过长，仅显示前{max_length}字符，共{len(text)}字符)"
        
        return f"[网页内容] {url}\n\n{text}\n\n[获取时间] {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
    except requests.exceptions.Timeout:
        return f"[超时] 请求 {url} 超时，网站可能响应较慢。"
    except requests.exceptions.ConnectionError:
        return f"[连接失败] 无法连接到 {url}，请检查网址是否正确。"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if hasattr(e, 'response') else "未知"
        return f"[HTTP错误] 访问 {url} 返回状态码: {status}"
    except Exception as e:
        return f"[错误] 获取网页失败: {str(e)}"
