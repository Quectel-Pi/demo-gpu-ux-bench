#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页场景 rAF 统计结果的本地接收服务（只监听 127.0.0.1）

firefox 打开的测试页在滚动结束后，用 `new Image().src = '/report?d=<json>'`
把统计结果以 GET 图片请求回传（GET 图片不受同源/CORS 限制，最稳）。
本服务收到第一条上报即写文件并退出。
"""

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {"done": False}
ARGS = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/report":
            self.send_response(404)
            self.end_headers()
            return

        # parse_qs 已完成 percent-decode，无需再 unquote
        raw = parse_qs(url.query).get("d", [""])[0]
        try:
            data = json.loads(raw)
        except Exception as exc:                      # 只做最小兜底，便于排查
            data = {"error": str(exc), "raw": raw[:500]}
        data["received_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        with open(ARGS.out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print("[collect_server] 收到网页上报 -> %s" % ARGS.out, flush=True)
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)

        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        STATE["done"] = True

    def log_message(self, *args):
        pass


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="网页场景 rAF 结果接收服务")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--out", required=True, help="结果 JSON 输出路径")
    ap.add_argument("--timeout", type=float, default=180.0, help="等待上报的最长秒数")
    ARGS = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
    server.timeout = 0.5
    deadline = time.monotonic() + ARGS.timeout
    print("[collect_server] 监听 127.0.0.1:%d，等待网页上报（最长 %ss）"
          % (ARGS.port, ARGS.timeout), flush=True)

    while not STATE["done"] and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if not STATE["done"]:
        print("[collect_server] 超时未收到上报", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
