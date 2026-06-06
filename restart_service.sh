#!/bin/bash
# ZeroAgent 服务重启脚本
cd 'C:\Users\user LAN\Desktop\python\cloud-agent'
# 杀掉旧进程后重新启动
pkill -f 'python.*server.py' 2>/dev/null
sleep 1
python server.py &
echo '服务已在后台重启'
