#!/usr/bin/env python3
"""ssh ProxyCommand：把 ssh 的 TCP 连接经 HTTP CONNECT 代理转发。

用途：本机直连 GitHub 被限速（实测 git clone 仅约 33KB/s、codeload 直接超时），
而 Clash 代理可达 5.7MB/s。runner.py 支持用 LONGBLOG_GIT_SSH_COMMAND 覆盖 ssh 命令，
这里作为它的 ProxyCommand 使用：

    ProxyCommand=python3 /app/service/git_proxy.py %h %p

代理地址从 LONGBLOG_GIT_PROXY 读取（默认 host.docker.internal:7890，Clash mixed 端口）。
连不上代理时自动回退直连，避免代理挂了导致完全不可用。
"""
import os
import select
import socket
import sys
import base64


def connect_proxy(host, port):
    """经 HTTP CONNECT 建立到 host:port 的隧道；失败返回 None。"""
    proxy = os.environ.get("LONGBLOG_GIT_PROXY", "host.docker.internal:7890")
    if not proxy:
        return None
    phost, _, pport = proxy.rpartition(":")
    if not phost:
        return None
    try:
        sock = socket.create_connection((phost, int(pport)), timeout=15)
    except OSError as error:
        sys.stderr.write(f"[git_proxy] 代理 {proxy} 不可达（{error}），回退直连\n")
        return None
    req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    auth = os.environ.get("LONGBLOG_GIT_PROXY_AUTH", "")
    if auth:
        req += "Proxy-Authorization: Basic " + base64.b64encode(auth.encode()).decode() + "\r\n"
    req += "Proxy-Connection: keep-alive\r\n\r\n"
    try:
        sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError as error:
        sys.stderr.write(f"[git_proxy] 代理握手失败（{error}），回退直连\n")
        sock.close()
        return None
    status = buf.split(b"\r\n", 1)[0]
    if b" 200" not in status:
        sys.stderr.write(f"[git_proxy] 代理拒绝 CONNECT：{status!r}，回退直连\n")
        sock.close()
        return None
    sock.setblocking(True)
    sock.settimeout(None)
    return sock, buf.split(b"\r\n\r\n", 1)[1]


def relay(sock, leftover=b""):
    if leftover:
        os.write(1, leftover)
    while True:
        try:
            readable, _, _ = select.select([sock, 0], [], [], 3600)
        except (OSError, ValueError):
            break
        if not readable:
            break
        if sock in readable:
            try:
                data = sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            os.write(1, data)
        if 0 in readable:
            try:
                data = os.read(0, 65536)
            except OSError:
                break
            if not data:
                break
            try:
                sock.sendall(data)
            except OSError:
                break


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: git_proxy.py <host> <port>\n")
        return 2
    host, port = sys.argv[1], int(sys.argv[2])
    tunneled = connect_proxy(host, port)
    if tunneled:
        sock, leftover = tunneled
        try:
            relay(sock, leftover)
        finally:
            sock.close()
        return 0
    direct = socket.create_connection((host, port), timeout=30)
    direct.settimeout(None)
    try:
        relay(direct)
    finally:
        direct.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
