#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查看当前日期和时间的工具脚本
"""

from datetime import datetime
import sys
import os

def get_current_time(timezone_str='Asia/Shanghai'):
    """
    获取指定时区的当前日期和时间
    
    参数:
        timezone_str (str): 时区名称，默认 'Asia/Shanghai'（北京时间）
    
    返回:
        str: 格式化的日期时间字符串
    """
    try:
        import pytz
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
    except:
        now = datetime.now()
    
    date_str = now.strftime("%Y年%m月%d日")
    time_str = now.strftime("%H:%M:%S")
    weekday_map = {
        0: '星期一', 1: '星期二', 2: '星期三', 3: '星期四',
        4: '星期五', 5: '星期六', 6: '星期日'
    }
    weekday = weekday_map[now.weekday()]
    
    result = f"当前日期：{date_str} {weekday}\n当前时间：{time_str}"
    return result

def main():
    print("=" * 30)
    print(get_current_time())
    print("=" * 30)

if __name__ == '__main__':
    # Windows CMD 下设置编码
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    main()
