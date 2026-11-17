# -*- coding: utf-8 -*-
"""
v4 报告渲染层：采集端实时驱动匹配端。

相对 v3 的变化：
  - 报告不再是生成时算好的静态快照。学生改专业 / 勾选岗位 / 家乡 / 五项权重后，
    页面读取 fair_profile 并实时重算排序分、算分依据、现场提问优先级、队列位次与顺延目标。
  - 加入企业端事实校验徽标：模型结论与真实 JD / 行业是否一致，低可信的显式标出。
  - 加入筛选（全部 / 高可信 / 推断待核实）与折叠。

形式约束不变：不新增 CSS、不改 HTML 骨架、不改聊天流程，只往 #mres 填内容，
复用 .tier / .row / .rowhead / .nm / .sc / .calc / .jobbox / .note。
"""

# 页面规则块已冻结为真实源文件 js/report.js（同 js/fair.js 的做法）。
# 这里只负责拼回 <script> 壳；REPORT_JS 的最终内容与抽取前逐字节一致。
from pathlib import Path as _Path  # noqa: E402

_REPORT_SRC = (_Path(__file__).resolve().parent / "js" / "report.js").read_bytes().decode("utf-8").replace("\r\n", "\n")
# 保持 LF（与 git blob 里的原串一致）；页面写出时 write_text 的文本模式会在 Windows 上统一转 CRLF
REPORT_JS = "<script>\n" + _REPORT_SRC + "</script>\n"

# —— 职能族表注入 ——
# 单一数据源在 job_family.FAMILIES：页面侧不手抄第二份，构建时序列化进来。
# 这样「Python 判定」和「JS 判定」共用同一张表，parity 门禁只需比对逻辑，不用比对数据。
import json as _json          # noqa: E402
import job_family as _job_family   # noqa: E402

REPORT_JS = REPORT_JS.replace(
    "__MI_FUNC__",
    _json.dumps(_job_family.JS_TABLE, ensure_ascii=False, separators=(",", ":")))
