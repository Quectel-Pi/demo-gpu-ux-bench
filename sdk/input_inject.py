#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uinput 虚拟键盘注入（纯标准库，无第三方依赖）

用途
----
桌面动画、网页滚动这类"真实场景"必须走真实的输入路径才有意义（合成器收到的是
输入事件 + 重绘请求）。本工具通过 /dev/uinput 创建一个虚拟键盘，注入按键事件，
GNOME(libinput) 会把它当成真键盘，从而触发概览/工作区切换/翻页滚动等真实交互。

板子实测：内核 CONFIG_INPUT_UINPUT=y，/dev/uinput 存在，root 可直接使用。

用法
----
  input_inject.py tap leftmeta                 # 单击 Super（切换概览）
  input_inject.py combo leftctrl leftalt up    # Ctrl+Alt+Up（切换工作区）
  input_inject.py tap pagedown --count 20 --interval 0.5
  input_inject.py -                            # 从 stdin 读脚本，复用同一个虚拟键盘
      # 脚本语法（每行一条）：
      #   tap <key> | keydown <key> | keyup <key> | combo <k1> <k2> ... |
      #   sleep <秒> | quit
"""

import argparse
import fcntl
import os
import struct
import sys
import time

UINPUT_DEV = "/dev/uinput"

# ioctl 编号
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

# event 类型
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00

UINPUT_MAX_NAME_SIZE = 80
BUS_USB = 0x03

# 仅收录本方案用到的键，避免过度设计
KEYS = {
    "escape": 1, "enter": 28, "leftctrl": 29, "space": 57, "leftalt": 56,
    "leftmeta": 125, "tab": 15,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "pageup": 104, "pagedown": 109, "home": 102, "end": 107,
    "a": 30, "s": 31, "d": 32, "w": 17, "q": 16, "f": 33, "m": 50,
}

# struct uinput_user_dev（legacy，现代内核仍兼容）
UINPUT_USER_DEV_FMT = "80sHHHHi" + "i" * 256
UINPUT_USER_DEV_SIZE = struct.calcsize(UINPUT_USER_DEV_FMT)

# struct input_event（64 位：timeval 两个 long）
INPUT_EVENT_FMT = "llHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)


def key_code(name):
    code = KEYS.get(name.lower())
    if code is None:
        raise SystemExit("未知按键: %s（可用: %s）" % (name, ", ".join(sorted(KEYS))))
    return code


class VirtualKeyboard(object):
    def __init__(self, name="gpu-bench-virtual-kbd", settle=0.4):
        self.fd = os.open(UINPUT_DEV, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in set(KEYS.values()):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        dev = struct.pack(
            UINPUT_USER_DEV_FMT,
            name.encode()[:UINPUT_MAX_NAME_SIZE - 1],
            BUS_USB, 0x1234, 0x5678, 1,
            0, *( [0] * 256 ),
        )
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(settle)  # 等 libinput / 合成器识别新设备

    def _emit(self, ev_type, code, value):
        now = time.time()
        os.write(self.fd, struct.pack(
            INPUT_EVENT_FMT, int(now), int((now % 1) * 1e6), ev_type, code, value))

    def _sync(self):
        self._emit(EV_SYN, SYN_REPORT, 0)

    def key_down(self, name):
        self._emit(EV_KEY, key_code(name), 1)
        self._sync()

    def key_up(self, name):
        self._emit(EV_KEY, key_code(name), 0)
        self._sync()

    def tap(self, name, press_s=0.06):
        self.key_down(name)
        time.sleep(press_s)
        self.key_up(name)

    def combo(self, names, press_s=0.08):
        for n in names:
            self.key_down(n)
            time.sleep(0.03)
        time.sleep(press_s)
        for n in reversed(names):
            self.key_up(n)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


def parse_args():
    ap = argparse.ArgumentParser(description="uinput 虚拟键盘注入")
    ap.add_argument("action", nargs="?",
                    help="tap|keydown|keyup|combo|- （'-' 表示从 stdin 读脚本）")
    ap.add_argument("keys", nargs="*", help="按键名")
    ap.add_argument("--count", type=int, default=1, help="重复次数")
    ap.add_argument("--interval", type=float, default=0.3, help="重复间隔（秒）")
    ap.add_argument("--max-seconds", type=float, default=300.0,
                    help="整体最长运行时长（安全护栏，默认 300s）")
    return ap.parse_args()


def run_script(kbd, max_seconds):
    """从 stdin 读取动作脚本，复用同一个虚拟键盘，直到 quit / EOF / 超时。"""
    deadline = time.monotonic() + max_seconds
    for raw in sys.stdin:
        if time.monotonic() > deadline:
            print("[input_inject] 达到 max-seconds，提前退出", file=sys.stderr)
            return
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cmd, rest = parts[0], parts[1:]
        if cmd == "quit":
            return
        if cmd == "sleep":
            time.sleep(float(rest[0]))
        elif cmd == "tap":
            kbd.tap(rest[0])
        elif cmd == "keydown":
            kbd.key_down(rest[0])
        elif cmd == "keyup":
            kbd.key_up(rest[0])
        elif cmd == "combo":
            kbd.combo(rest)
        else:
            print("[input_inject] 忽略未知指令: %s" % line, file=sys.stderr)


def main():
    args = parse_args()
    if not args.action:
        raise SystemExit(__doc__)

    if args.action == "-":
        kbd = VirtualKeyboard()
        try:
            run_script(kbd, args.max_seconds)
        finally:
            kbd.close()
        return

    if args.action not in ("tap", "keydown", "keyup", "combo"):
        raise SystemExit("未知动作: %s" % args.action)
    if not args.keys:
        raise SystemExit("缺少按键参数")

    kbd = VirtualKeyboard()
    try:
        for i in range(args.count):
            if args.action == "tap":
                kbd.tap(args.keys[0])
            elif args.action == "keydown":
                kbd.key_down(args.keys[0])
            elif args.action == "keyup":
                kbd.key_up(args.keys[0])
            else:
                kbd.combo(args.keys)
            if i != args.count - 1:
                time.sleep(args.interval)
    finally:
        kbd.close()


if __name__ == "__main__":
    main()
