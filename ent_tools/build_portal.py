"""生成入口页并给页面加底部导航。

新流程（全在一页内完成）：
  fair.html  → 画像对话 → 导入名单 → 开始匹配 → 结果
  index.html → 详细视图（企业卡、一页纸）
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"

PORTAL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>双选会会前规划 · 演示</title>
<style>
:root{--blue:#2f5bd3;--ink:#16181d;--muted:#6b7280;--line:#e6e8ec;--bg:#f4f5f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
 font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.app{max-width:520px;margin:0 auto;padding:40px 20px 70px}
h1{font-size:25px;margin:0 0 6px;letter-spacing:.3px}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 22px}
.flow{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;
 font-size:13px;color:#4b5563;line-height:2.1;margin:0 0 26px}
.flow b{color:var(--blue)}
.card{display:block;text-decoration:none;color:inherit;background:#fff;border:1px solid var(--line);
 border-radius:14px;padding:20px;margin-bottom:14px;transition:.15s}
.card:hover{border-color:#c9d3ea;box-shadow:0 4px 16px rgba(30,50,110,.07)}
.card.main{border-color:#c9d3ea;background:linear-gradient(180deg,#fbfcff,#fff)}
.num{display:inline-block;width:26px;height:26px;line-height:26px;text-align:center;border-radius:8px;
 background:#eef3fe;color:var(--blue);font-weight:700;font-size:13px;margin-right:10px}
.t{font-size:16.5px;font-weight:650;vertical-align:middle}
.d{color:var(--muted);font-size:13px;margin:9px 0 0}
.who{font-size:11.5px;color:#a9afba;margin-top:8px}
.go{margin-top:14px;display:inline-block;background:var(--blue);color:#fff;font-weight:600;
 font-size:14px;padding:10px 18px;border-radius:10px}
.note{margin-top:26px;font-size:12px;color:#a9afba;line-height:1.9}
</style>
</head>
<body>
<div class="app">
  <h1>双选会会前规划</h1>
  <p class="sub">聊两句，导入名单，生成今天该走哪几家</p>

  <div class="flow">
    ① 画像对话（简历 + 4 个问题 + 偏好权重）<br>
    ② 导入参会企业名单<br>
    ③ 点<b>「开始匹配」</b> → 结果就在同一页
  </div>

  <a class="card main" href="fair.html">
    <span class="num">1</span><span class="t">开始</span>
    <p class="d">对话式采集画像，接着导入名单，一键匹配</p>
    <div class="who">全流程在同一页完成</div>
    <div class="go">进入 →</div>
  </a>

  <a class="card" href="index.html">
    <span class="num">2</span><span class="t">详细视图</span>
    <p class="d">企业卡、岗位表、一页纸作战卡（打印用）</p>
    <div class="who">给老师演示或自己带走看</div>
  </a>

  <p class="note">
    离线可运行，不需要服务器。演示数据为示例，非真实招聘信息。
  </p>
</div>
</body>
</html>
"""

NAV = """
<div id="dsh-nav" style="position:fixed;left:0;right:0;bottom:0;z-index:9999;
 background:rgba(255,255,255,.96);border-top:1px solid #e6e8ec;
 backdrop-filter:blur(8px);font:13px/1 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;">
 <div style="max-width:520px;margin:0 auto;display:flex;padding:9px 12px 11px;gap:6px;">
  <a href="start.html" style="flex:1;text-align:center;padding:8px 0;border-radius:9px;color:#6b7280;text-decoration:none;">入口</a>
  <a href="fair.html" style="flex:1;text-align:center;padding:8px 0;border-radius:9px;color:#2f5bd3;font-weight:600;text-decoration:none;">采集 + 匹配</a>
  <a href="index.html" style="flex:1;text-align:center;padding:8px 0;border-radius:9px;color:#6b7280;text-decoration:none;">详细视图</a>
 </div>
</div>
<style>body{padding-bottom:56px}</style>
"""

(DEMO / "start.html").write_text(PORTAL, encoding="utf-8")
print("生成", DEMO / "start.html")

for name in ("collect.html", "fair.html", "index.html"):
    p = DEMO / name
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8")
    # 旧的导航整块替换掉
    if 'id="dsh-nav"' in text:
        start = text.index('<div id="dsh-nav"')
        end = text.index("</style>", text.index("body{padding-bottom:56px}")) + len("</style>")
        text = text[:start] + NAV.strip() + text[end:]
        p.write_text(text, encoding="utf-8")
        print("  导航已替换", name)
    else:
        p.write_text(text.replace("</body>", NAV + "\n</body>", 1), encoding="utf-8")
        print("  已加导航", name)
