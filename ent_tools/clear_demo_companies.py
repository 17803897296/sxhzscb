"""清空 demo 里内置的 8 家演示企业，改从采集结果读。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
f = ROOT / "demo" / "index.html"
s = f.read_text(encoding="utf-8")

# COMPANIES -> []
m = re.search(r"const COMPANIES = \[.*?\n\];", s, re.S)
assert m, "找不到 COMPANIES"
s = s[:m.start()] + "const COMPANIES = [];" + s[m.end():]

# RESULTS -> []
m = re.search(r"const RESULTS = \[.*?\n\];", s, re.S)
assert m, "找不到 RESULTS"
s = s[:m.start()] + "const RESULTS = [];" + s[m.end():]

# 启动时从采集页拿企业数据
old = "  try { renderRank(); renderSheet(); } catch (e) {}"
new = """  // 企业数据来自采集页联网补全（localStorage.companies），不再内置演示数据
  try {
    const rawC = localStorage.getItem("companies");
    if (rawC) {
      const list = JSON.parse(rawC);
      if (Array.isArray(list) && list.length) companyPool = list;
    }
  } catch (e) {}

  try { renderRank(); renderSheet(); } catch (e) {}"""
assert old in s, "找不到启动钩子"
s = s.replace(old, new, 1)

f.write_text(s, encoding="utf-8")
print("index.html：COMPANIES / RESULTS 已清空，改为读 localStorage.companies")
