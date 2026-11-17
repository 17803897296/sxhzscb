"""详细页：没有企业数据时给个明确的空状态，不要白屏。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
f = ROOT / "demo" / "index.html"
s = f.read_text(encoding="utf-8")

anchor = '  const results = rank(companyPool, profile);\n\n  $("#tiers").innerHTML = TIERS.map(tier => {'
assert anchor in s, "找不到 renderRank 主体"

new = '''  const results = rank(companyPool, profile);

  if (!results.length) {
    $("#tiers").innerHTML =
      '<div style="background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:22px 18px;'
      + 'font-size:13.5px;color:#6b7280;line-height:1.9">'
      + '<b style="color:#16181d;font-size:15px;display:block;margin-bottom:6px">还没有企业数据</b>'
      + '企业画像来自「采集 + 匹配」页的联网补全。<br>'
      + '<a href="fair.html" style="color:#2f5bd3;font-weight:600">去采一份 →</a>'
      + '</div>';
    return;
  }

  $("#tiers").innerHTML = TIERS.map(tier => {'''
s = s.replace(anchor, new, 1)

f.write_text(s, encoding="utf-8")
print("index.html：已加空状态")
