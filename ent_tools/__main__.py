# -*- coding: utf-8 -*-
"""统一入口：`python -m ent_tools <命令>`（评审 P0 第二刀）。

不用再记 35 个脚本名，克隆后四条命令覆盖日常：

  serve      本地服务端（fair_server，正本 8102 / 副本 8103，参数透传）
  build      重建 demo/ 正本薄页（--offline-full 走离线全量变体）
  offline    离线副本同步（sync_offline：现场构建变体并叠加到副本）
  pipeline   全流程（refresh_all：缓存 -> 推断 -> 构建 -> 页面 -> 回归）
  sync-repo  生成同步包并打版本标签（sync_repo）

各脚本内部的顶层互导（import fair_bridge 等）依赖 ent_tools 目录在
sys.path 上，这里统一补上，让包内 runpy 派发与直接跑脚本行为一致。
"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

COMMANDS = {
    "serve": "fair_server",
    "serve-stable": "stable_server",  # 脱离会话的常驻启动（start/status/stop/ensure-task）
    "build": "build_fair_page",
    "offline": "sync_offline",
    "pipeline": "refresh_all",
    "sync-repo": "sync_repo",
    "virtual-e2e": "virtual_user_e2e",  # 虚拟用户端到端（推荐全量分层+常识复核，--with-match 加 145 匹配）
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        print("用法：python -m ent_tools <%s> [原脚本参数]" % "|".join(COMMANDS))
        return 2
    mod = COMMANDS[argv[0]]
    rest = argv[1:]
    sys.path.insert(0, str(HERE))
    # 让 argparse 的 usage/报错显示真实脚本路径，而不是 __main__
    sys.argv = [str(HERE / (mod + ".py"))] + rest
    runpy.run_module(mod, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
