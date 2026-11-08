# -*- coding: utf-8 -*-
"""
无人机道路巡检系统 - 统一守护器
同时管理两个子进程，保证竞赛评审期间服务永不中断：
  1. server.py  — 本地Web服务（8080端口）
  2. SSH隧道    — localhost.run公网穿透（将本地8080暴露到公网）

特性：
  - 服务崩溃/被杀 → 自动拉起
  - 隧道断线/假死 → 自动重连
  - 公网URL变更 → 自动检测并更新 output/public_url.txt
  - 全程日志 → output/service_guard.log
  - 快速重启保护 → 防止死循环崩溃

启动方式：
    python-embed\\python.exe service_guard.py
    python-embed\\python.exe service_guard.py --tunnel-user nokey   （匿名模式，URL每次变）
    python-embed\\python.exe service_guard.py --tunnel-key .ssh_tunnel_key  （密钥模式，固定URL）
"""
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = os.path.join(PROJECT_ROOT, "python-embed", "python.exe")
SERVER_SCRIPT = os.path.join(PROJECT_ROOT, "server.py")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
LOG_FILE = os.path.join(OUTPUT_DIR, "service_guard.log")
TUNNEL_LOG = os.path.join(OUTPUT_DIR, "tunnel.log")
PUBLIC_URL_FILE = os.path.join(OUTPUT_DIR, "public_url.txt")

# SSH隧道配置
SSH_EXE = "ssh"
TUNNEL_HOST = "ssh.localhost.run"
TUNNEL_REMOTE = "80:127.0.0.1:8080"
TUNNEL_KEY = os.path.join(PROJECT_ROOT, ".ssh_tunnel_key")  # 密钥文件（注册后启用）

# 健康检查配置
LOCAL_CHECK_INTERVAL = 10     # 本地服务检查间隔（秒）
PUBLIC_CHECK_INTERVAL = 30    # 公网URL检查间隔（秒）
RESTART_COOLDOWN = 5          # 重启冷却（秒）
MAX_FAST_RESTARTS = 5         # 连续快速重启上限
TUNNEL_STARTUP_WAIT = 12      # 隧道启动后等待URL输出（秒）

# 端口探测范围（与 server.py find_free_port 一致）
PORT_START = 8080
PORT_END = 8099

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def save_public_url(url):
    """将当前公网URL写入文件，供外部工具读取"""
    try:
        with open(PUBLIC_URL_FILE, "w", encoding="utf-8") as f:
            f.write(url + "\n")
    except Exception:
        pass


def read_last_tunnel_url():
    """从隧道日志解析最新的 lhr.life 公网URL"""
    try:
        if not os.path.exists(TUNNEL_LOG):
            return None
        with open(TUNNEL_LOG, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        # 匹配所有 lhr.life URL，取最后一个
        urls = re.findall(r"https://([a-z0-9]+\.lhr\.life)", content)
        if urls:
            return "https://" + urls[-1]
    except Exception:
        pass
    return None


def is_local_alive():
    """检查本地Web服务是否可用（必须8080端口，隧道转发目标）"""
    try:
        r = urllib.request.urlopen(
            "http://127.0.0.1:8080/api/status", timeout=3)
        if r.status == 200:
            return True, 8080
    except Exception:
        pass
    return False, None


def kill_all_servers():
    """杀死所有运行server.py的python进程，确保端口8080释放"""
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object "
             "{$_.CommandLine -like '*server.py*' -and $_.Name -eq 'python.exe'} | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=10,
            creationflags=0x08000000 if os.name == "nt" else 0)
    except Exception:
        pass


# lhr.life 真实IP（校园网DNS被污染，需直连真实IP + SSL SNI 绕过）
LHR_LIFE_REAL_IPS = ["3.234.18.192", "54.172.225.3", "3.208.46.244"]


def is_public_alive(url):
    """检查公网URL是否可访问（直连真实IP绕过DNS污染）"""
    if not url:
        return False
    hostname = url.replace("https://", "").split("/")[0].split(":")[0]
    for real_ip in LHR_LIFE_REAL_IPS:
        try:
            sock = socket.create_connection((real_ip, 443), timeout=10)
            ssl_sock = _ssl_ctx.wrap_socket(sock, server_hostname=hostname)
            req_str = ("GET /api/status HTTP/1.1\r\n"
                       "Host: %s\r\n"
                       "User-Agent: Mozilla/5.0\r\n"
                       "Connection: close\r\n\r\n") % hostname
            ssl_sock.sendall(req_str.encode())
            response = b""
            while True:
                data = ssl_sock.recv(4096)
                if not data:
                    break
                response += data
                if len(response) > 8192:
                    break
            ssl_sock.close()
            status_line = response.decode("utf-8", "ignore").split("\r\n")[0]
            if " 200" in status_line:
                return True
            else:
                log("[公网检查] %s -> %s" % (hostname, status_line[:80]))
                # 502 = no tunnel，非200视为不可达
                return False
        except Exception as e:
            log("[公网检查] %s via %s 异常: %s" % (
                hostname, real_ip, str(e)[:80]))
            continue
    return False


def build_tunnel_cmd(use_key=True):
    """构建SSH隧道命令"""
    cmd = [
        SSH_EXE,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=45",
        "-o", "ServerAliveCountMax=4",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=15",
        "-R", TUNNEL_REMOTE,
    ]
    if use_key and os.path.exists(TUNNEL_KEY):
        cmd += ["-i", TUNNEL_KEY]
    cmd.append("nokey@" + TUNNEL_HOST)
    return cmd


class ServiceGuard:
    """统一守护器：管理 server.py + SSH隧道"""

    def __init__(self, use_key=True):
        self.use_key = use_key
        self.server_proc = None
        self.tunnel_proc = None
        self.server_start_time = 0
        self.tunnel_start_time = 0
        self.fast_restarts = 0
        self.current_public_url = None
        self.last_public_check = 0
        self._tunnel_log_fh = None

    # ---------- 服务管理 ----------

    def start_server(self):
        # 先清理所有旧server进程，确保8080端口释放
        kill_all_servers()
        time.sleep(1)
        # 强制使用8080端口（隧道转发目标）
        env = os.environ.copy()
        env["PORT"] = "8080"
        creationflags = 0x08000000 if os.name == "nt" else 0
        self.server_proc = subprocess.Popen(
            [PYTHON_EXE, SERVER_SCRIPT],
            cwd=PROJECT_ROOT,
            env=env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.server_start_time = time.time()
        log("[服务] 已拉起 server.py PID=%d (PORT=8080)" % self.server_proc.pid)

    def restart_server(self, reason):
        log("[服务] 重启原因: %s" % reason)
        if self.server_proc and self.server_proc.poll() is None:
            try:
                self.server_proc.kill()
                self.server_proc.wait(timeout=5)
            except Exception:
                pass
        self._cooldown()
        self.start_server()

    # ---------- 隧道管理 ----------

    def start_tunnel(self):
        # 清空旧日志，便于解析新URL
        try:
            if self._tunnel_log_fh:
                self._tunnel_log_fh.close()
            with open(TUNNEL_LOG, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

        cmd = build_tunnel_cmd(self.use_key)
        creationflags = 0x08000000 if os.name == "nt" else 0
        self._tunnel_log_fh = open(TUNNEL_LOG, "a", encoding="utf-8")
        self.tunnel_proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            creationflags=creationflags,
            stdout=self._tunnel_log_fh,
            stderr=subprocess.STDOUT,
        )
        self.tunnel_start_time = time.time()
        log("[隧道] 已拉起SSH隧道 PID=%d 命令=%s" % (
            self.tunnel_proc.pid, " ".join(cmd[:4]) + " ..."))

        # 等待隧道输出URL
        time.sleep(TUNNEL_STARTUP_WAIT)
        url = read_last_tunnel_url()
        if url:
            if url != self.current_public_url:
                log("[隧道] 公网URL: %s" % url)
                self.current_public_url = url
                save_public_url(url)
        else:
            log("[隧道] 启动后未检测到URL，将在下轮检查中重试")

    def restart_tunnel(self, reason):
        log("[隧道] 重启原因: %s" % reason)
        if self.tunnel_proc and self.tunnel_proc.poll() is None:
            try:
                self.tunnel_proc.kill()
                self.tunnel_proc.wait(timeout=5)
            except Exception:
                pass
        self._cooldown()
        self.start_tunnel()

    # ---------- 冷却保护 ----------

    def _cooldown(self):
        uptime = time.time() - max(self.server_start_time,
                                   self.tunnel_start_time)
        if uptime < 60:
            self.fast_restarts += 1
        if self.fast_restarts >= MAX_FAST_RESTARTS:
            log("[保护] 连续快速重启 %d 次，冷却60秒" % self.fast_restarts)
            time.sleep(60)
            self.fast_restarts = 0
        else:
            time.sleep(RESTART_COOLDOWN)

    # ---------- 主循环 ----------

    def run(self):
        log("=" * 60)
        log("统一守护器启动（密钥模式=%s）" % self.use_key)
        log("服务脚本: %s" % SERVER_SCRIPT)
        log("隧道目标: %s" % TUNNEL_HOST)
        if self.use_key and os.path.exists(TUNNEL_KEY):
            log("隧道密钥: %s" % TUNNEL_KEY)
        else:
            log("隧道密钥: 未找到（匿名模式，URL每次重启会变）")
        log("=" * 60)

        # 先启动本地服务，等它就绪后再建隧道
        self.start_server()
        for _ in range(10):
            alive, port = is_local_alive()
            if alive:
                log("[服务] 本地服务就绪，端口 %d" % port)
                break
            time.sleep(1)
        else:
            log("[服务] 警告: 本地服务10秒内未就绪，继续启动隧道")

        self.start_tunnel()

        while True:
            time.sleep(LOCAL_CHECK_INTERVAL)

            # === 检查本地服务 ===
            alive, port = is_local_alive()
            if not alive:
                if self.server_proc and self.server_proc.poll() is None:
                    log("[服务] 进程存活但健康检查失败（假死）")
                self.restart_server("本地健康检查失败")
                continue
            else:
                self.fast_restarts = 0  # 服务正常，重置计数

            # === 检查隧道进程 ===
            if self.tunnel_proc is None or self.tunnel_proc.poll() is not None:
                exit_code = self.tunnel_proc.poll() if self.tunnel_proc else "N/A"
                self.restart_tunnel("隧道进程已退出(code=%s)" % exit_code)
                continue

            # === 检查公网URL（降频） ===
            now = time.time()
            if now - self.last_public_check >= PUBLIC_CHECK_INTERVAL:
                self.last_public_check = now
                # 先从日志更新URL（隧道可能重连换了地址）
                latest_url = read_last_tunnel_url()
                if latest_url and latest_url != self.current_public_url:
                    log("[隧道] 检测到URL变更: %s → %s" % (
                        self.current_public_url, latest_url))
                    self.current_public_url = latest_url
                    save_public_url(latest_url)

                if not is_public_alive(self.current_public_url):
                    self.restart_tunnel("公网URL不可达")
                else:
                    log("[巡检] 一切正常 本地:%d 公网:%s" % (
                        port, self.current_public_url))


def is_already_running():
    """检查是否已有其他 service_guard.py 实例在运行（防重复启动）"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*service_guard*'}).Count"],
            capture_output=True, text=True, timeout=10)
        count = int(out.stdout.strip() or "0")
        return count >= 2  # 自身会被计入，>=2表示已有其他实例
    except Exception:
        return False


def main():
    # 防重复启动：如果已有实例在运行则退出
    if is_already_running():
        print("[守护器] 已有实例在运行，本次启动退出，避免端口冲突")
        sys.exit(0)
    use_key = "--no-key" not in sys.argv
    guard = ServiceGuard(use_key=use_key)
    try:
        guard.run()
    except KeyboardInterrupt:
        log("守护器已停止")
        for p in (guard.server_proc, guard.tunnel_proc):
            if p and p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()