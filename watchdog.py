# -*- coding: utf-8 -*-
"""
无人机道路巡检系统 - 进程守护器
监控 Web 服务进程，崩溃/被杀时自动拉起，保证外部访问永不中断。
仅依赖 Python 标准库，配合项目内嵌 python-embed 运行。

启动方式：
    python-embed\\python.exe watchdog.py
"""
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = os.path.join(PROJECT_ROOT, "python-embed", "python.exe")
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "server.py")
LOG_FILE = os.path.join(PROJECT_ROOT, "output", "watchdog.log")

HEALTH_PORT_START = 8080   # 健康检查端口范围（与 server.py find_free_port 一致）
HEALTH_PORT_END = 8099
HEALTH_INTERVAL = 10        # 健康检查间隔（秒）
RESTART_COOLDOWN = 5        # 重启冷却（秒）
MAX_FAST_RESTARTS = 5       # 连续快速重启上限（超过则延长冷却）

_proc = None
_start_time = 0
_fast_restarts = 0


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_port_alive():
    """依次探测 8080-8099 端口的健康检查API，判断服务是否可用（兼容端口自动切换）"""
    import urllib.request
    for port in range(HEALTH_PORT_START, HEALTH_PORT_END + 1):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:%d/api/status" % port, timeout=3)
            if r.status == 200:
                return True
        except Exception:
            continue
    return False


def start_server():
    """拉起 server.py 子进程（无窗口）"""
    global _proc, _start_time
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    _proc = subprocess.Popen(
        [PYTHON_EXE, SERVER_SCRIPT],
        cwd=PROJECT_ROOT,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _start_time = time.time()
    log("已拉起服务进程 PID=%d" % _proc.pid)


def main():
    log("=" * 50)
    log("巡检系统守护器启动，监控间隔 %ds" % HEALTH_INTERVAL)
    log("服务脚本：%s" % SERVER_SCRIPT)
    start_server()

    while True:
        time.sleep(HEALTH_INTERVAL)
        # 进程存活检查
        if _proc is not None and _proc.poll() is None:
            # 进程在跑，再做健康探测（防假死）
            if is_port_alive():
                _fast_restarts = 0          # 服务稳定运行，重置快速重启计数
                continue
            else:
                log("服务进程存活但健康检查失败（疑似假死），执行重启")
                try:
                    _proc.kill()
                except Exception:
                    pass
        else:
            log("服务进程已退出（code=%s），执行重启" % (_proc.poll() if _proc else "N/A"))

        # 快速重启保护：短时间内反复崩溃则延长冷却
        uptime = time.time() - _start_time
        if uptime < 60:
            _fast_restarts += 1
        if _fast_restarts >= MAX_FAST_RESTARTS:
            log("连续快速重启 %d 次，延长冷却至 60 秒" % _fast_restarts)
            time.sleep(60)
            _fast_restarts = 0
        else:
            time.sleep(RESTART_COOLDOWN)

        start_server()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("守护器已停止")
        if _proc and _proc.poll() is None:
            _proc.terminate()