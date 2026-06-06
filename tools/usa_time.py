#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查看美国主要时区当前时间的工具
"""

from datetime import datetime
import sys

def get_usa_time():
    """
    获取美国四大时区的当前日期和时间
    
    返回:
        str: 格式化的各时区时间字符串
    """
    try:
        from datetime import timezone, timedelta
    except:
        pass
    
    now = datetime.utcnow()
    
    # 美国四大时区（相对于UTC的偏移）
    timezones = {
        "东部时间 (ET / 纽约)": -5,     # UTC-5 (标准)
        "中部时间 (CT / 芝加哥)": -6,    # UTC-6
        "山地时间 (MT / 丹佛)": -7,      # UTC-7
        "太平洋时间 (PT / 洛杉矶)": -8,  # UTC-8
    }
    
    lines = []
    lines.append("=" * 45)
    lines.append("  🌎 美国主要时区当前时间")
    lines.append("  (标准时间, 不含夏令时调整)")
    lines.append("=" * 45)
    lines.append("")
    
    # 检查是否是夏令时（3月第二个周日~11月第一个周日）
    # 简化判断：3月到10月通常为夏令时
    is_dst = 3 <= now.month <= 10
    dst_str = " (夏令时 +1h)" if is_dst else ""
    
    for name, offset in timezones.items():
        if is_dst:
            offset += 1  # 夏令时加1小时
        tz_time = now + timedelta(hours=offset)
        date_str = tz_time.strftime("%Y-%m-%d")
        time_str = tz_time.strftime("%H:%M:%S")
        weekday_map = {0: "Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri", 5:"Sat", 6:"Sun"}
        weekday = weekday_map[tz_time.weekday()]
        
        lines.append(f"  {name}:")
        lines.append(f"    {date_str} {weekday} {time_str}")
    
    lines.append("")
    lines.append(f"  💡 当前UTC时间: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append(f"  {'⚠️ 当前为夏令时' if is_dst else '当前为标准时间'}{dst_str}")
    lines.append("=" * 45)
    
    return "\n".join(lines)

def main():
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    print(get_usa_time())

if __name__ == '__main__':
    main()
