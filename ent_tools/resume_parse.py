# -*- coding: utf-8 -*-
"""简历阅读：多格式取文本 + 规则抽取，可选大模型增强。

之前 /api/fair/resume 被页面离线垫片替换成写死的 DEMO_RESUME，
导致无论上传什么文件都返回「李小明 / 管理科学与工程 / 供应链实习」。
这里提供真实解析，字段结构与前端 state.resume / DEMO_RESUME 完全一致。

支持：.txt .md .markdown .html .htm .docx（zipfile 读 word/document.xml）
      .jpg .jpeg .png .webp .bmp（2026-11-13 新增：走 GLM-4V 视觉模型 OCR）
.pdf 暂不支持（环境无 pypdf/pdfminer），会如实返回 warning，不假装解析成功。

只用标准库；use_llm=true 时可选调 .env 里的 GLM-4-flash 增强抽取，
模型失败自动退回规则结果，绝不编造。图片必须先 OCR 成功才能继续，
OCR 失败会明说「没读到文字」，不会拿空字符串硬解析。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import urllib.request
from email import policy
from email.parser import BytesParser
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))

FIELDS = [
    ("name", "姓名"), ("school", "学校"), ("major", "专业"),
    ("degree", "学历"), ("grade", "年级"), ("graduation", "毕业时间"),
    ("phone", "手机"), ("email", "邮箱"),
]

SKILL_WORDS = [
    "SQL", "Python", "R 语言", "R语言", "Java", "C++", "C#", "Go", "MATLAB",
    "Stata", "SPSS", "SAS", "Excel", "Tableau", "Power BI", "FineBI",
    "Spark", "Hadoop", "TensorFlow", "PyTorch", "机器学习", "深度学习",
    "自然语言处理", "计算机视觉", "有限元分析", "ANSYS", "SolidWorks", "AutoCAD",
    "Verilog", "FPGA", "Linux", "Git", "Figma", "Axure", "Photoshop",
    "财务报表分析", "估值建模", "多因子回测", "需求预测", "供应链计划",
    "实验设计", "文献计量", "系统综述", "临床试验", "GIS", "遥感",
]

CERT_WORDS = [
    "英语六级", "英语四级", "CET-6", "CET-4", "雅思", "托福", "GRE",
    "CFA", "CPA", "FRM", "ACCA", "计算机二级", "计算机三级", "教师资格证",
    "法律职业资格", "证券从业资格", "基金从业资格", "PMP", "软考",
]

KNOWN_MAJORS = [
    "管理科学与工程", "金融工程", "金融学", "计算机科学与技术", "软件工程",
    "电子信息工程", "机械工程", "材料科学与工程", "生物医学工程", "临床医学",
    "药学", "会计学", "市场营销", "法学", "新闻传播学", "汉语言文学",
    "统计学", "应用数学", "物理学", "化学", "土木工程", "建筑学",
    "环境工程", "自动化", "人工智能", "数据科学与大数据技术", "工业工程",
    "物流管理", "工商管理", "经济学", "国际经济与贸易", "心理学", "教育学",
    "护理学", "公共卫生", "车辆工程", "电气工程", "通信工程",
]

SECTION_HINTS = {
    "education": ["教育背景", "教育经历", "基本信息"],
    "experience": ["实习经历", "项目经历", "科研经历", "实践经历", "工作经历", "实习与项目"],
    "competition": ["竞赛获奖", "竞赛经历", "获奖情况", "比赛经历", "荣誉奖项"],
    "paper": ["论文发表", "科研成果", "学术论文", "发表论文", "研究成果"],
    "skill": ["技能", "专业技能", "能力", "技能清单"],
    "cert": ["证书", "资格证书", "语言与证书"],
}


def _load_env():
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _env_cfg() -> dict:
    cfg, path = {}, os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

OCR_PROMPT = (
    "这是一份简历的照片或截图。请把图中**所有文字**原样转写出来：\n"
    "1. 保持原有的分行结构，一行对应一行；\n"
    "2. 不要总结、不要翻译、不要补充图里没有的内容；\n"
    "3. 表格按「栏目：内容」的形式逐行写出；\n"
    "4. 如果图片模糊导致某些字无法辨认，用「□」代替该字，不要猜测；\n"
    "5. 只输出转写结果，不要任何说明文字。"
)


def ocr_image(data: bytes) -> tuple:
    """图片 -> 文字。走智谱 GLM-4V 系列视觉模型（标准库实现，无需第三方 SDK）。

    返回 (text, warnings, method)。失败时 text 为空并给出可操作提示，
    绝不返回占位文本让学生以为解析成功了。
    """
    warnings = []
    cfg = _env_cfg()
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    base = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4")
    if not key:
        warnings.append("没有配置视觉模型密钥，无法识别图片；请改传 .docx / .txt，"
                        "或把简历另存为文字版再上传")
        return "", warnings, "image-no-key"

    model = (os.environ.get("DEEPSEEK_VL_MODEL") or cfg.get("DEEPSEEK_VL_MODEL")
             or "glm-4v-flash")
    b64 = base64.b64encode(data).decode("ascii")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": b64}},
            {"type": "text", "text": OCR_PROMPT},
        ]}],
        "temperature": 0,
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = json.loads(r.read().decode("utf-8"))
        text = (raw["choices"][0]["message"]["content"] or "").strip()
        # 视觉模型常把结果包进 ```plaintext 代码块，去掉围栏再解析，
        # 否则第一行会被当成「经历」抓进去。
        text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
        if not text:
            warnings.append("图片里没识别出文字，可能太模糊或不是简历；请重拍或换文字版")
            return "", warnings, "image-empty"
        n_bad = text.count("□")
        if n_bad:
            warnings.append("有 %d 处字迹模糊无法辨认，已用「□」标出，建议核对" % n_bad)
        return text, warnings, "image-ocr"
    except Exception as exc:
        warnings.append("图片识别失败（%s）；请改传 .docx / .txt 或重拍更清晰的照片"
                        % str(exc)[:60])
        return "", warnings, "image-error"


def extract_text(filename: str, data: bytes):
    """返回 (text, warnings[, method])。支持 txt/md/html/docx/图片；pdf 如实说明不支持。"""
    low = (filename or "").lower()
    warnings = []
    if low.endswith(".docx"):
        try:
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml")
            root = ET.fromstring(xml)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            lines = []
            for p in root.iter(ns + "p"):
                txt = "".join(t.text or "" for t in p.iter(ns + "t"))
                if txt.strip():
                    lines.append(txt.strip())
            return "\n".join(lines), warnings
        except Exception as exc:
            warnings.append("docx 解析失败：%s" % str(exc)[:60])
            return "", warnings
    if low.endswith(IMG_EXT):
        text, warns, method = ocr_image(data)
        warnings.extend(warns)
        return text, warnings, method
    if low.endswith(".pdf"):
        warnings.append("当前环境没有 PDF 解析库，请把简历另存为 .txt / .md / .docx 再上传")
        return "", warnings
    text = _decode(data)
    if low.endswith((".html", ".htm")):
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>|</section>",
                      "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    return text, warnings


def _field(text: str, key: str, value, evidence: str, source="regex"):
    return {"value": value, "source": source, "evidence": evidence}


def _first(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def parse_fields(text: str):
    f = {}
    for k, _ in FIELDS:
        f[k] = {"value": "", "source": "missing", "evidence": ""}

    def put(k, val, ev):
        if val:
            f[k] = _field(text, k, val, ev)

    put("name", _first(r"(?:姓名|名字)\s*[:：]\s*([^\s，,、;；]{2,6})", text), "姓名：字段")
    if not f["name"]["value"]:
        # 开头几行里像中文姓名的短行
        for line in text.splitlines()[:5]:
            s = line.strip()
            if 2 <= len(s) <= 4 and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", s):
                put("name", s, "简历开头的人名行")
                break

    school = _first(r"(?:学校|院校|毕业院校)\s*[:：]\s*([^\n，,；;]{2,20})", text)
    if not school:
        school = _first(r"([\u4e00-\u9fa5]{2,12}(?:大学|学院|学校))", text)
    put("school", school, "学校字段 / 高校名称")

    major = _first(r"(?:专业|所学专业|研究方向)\s*[:：]\s*([^\n，,；;]{2,20})", text)
    if not major:
        for m in sorted(KNOWN_MAJORS, key=len, reverse=True):
            if m in text:
                major = m
                break
    put("major", major, "专业字段 / 已知专业名")

    degree = _first(r"(博士研究生|硕士研究生|本科生|博士|硕士|本科|大专)", text)
    put("degree", degree, "学历关键词")

    grade = _first(r"(研一|研二|研三|博一|博二|博三|大二|大三|大四|大一)", text)
    put("grade", grade, "年级关键词")

    grad = _first(r"(?:毕业时间|预计毕业|毕业)\s*[:：]?\s*(20\d{2}\s*年?\s*\d{0,2}\s*月?)", text)
    if not grad:
        m = re.search(r"(?:预计|预期)?\s*(20\d{2})\s*年\s*(\d{1,2})?\s*月?\s*毕业", text)
        if m:
            grad = m.group(1) + " 年 " + (m.group(2) or "") + " 月"
    if not grad:
        # 简历照片常见写法：「2027届」「2027 届毕业生」
        m = re.search(r"(20\d{2})\s*届", text)
        if m:
            grad = m.group(1) + " 届"
    put("graduation", grad.strip() if grad else "", "毕业时间")

    # 手机号：真实简历常写成 138-0000-1234 / 138 0000 1234 / +86 138...，
    # 允许分隔符与 +86 前缀，统一存成 11 位纯数字。
    _phone = ""
    for _pat in (r"(?:\+?86[-\s]?)?(1[3-9]\d[-\s]?\d{4}[-\s]?\d{4})",
                 r"(1[3-9]\d{9})"):
        _m = re.search(_pat, text)
        if _m:
            _phone = re.sub(r"\D", "", _m.group(1))
            if len(_phone) == 11:
                break
            _phone = ""
    put("phone", _phone, "手机号")
    put("email", _first(r"([\w.\-]+@[\w.\-]+\.[A-Za-z]{2,})", text), "邮箱")
    return f


def _sections(text: str):
    """按简历里的小标题给每行打段落标签。"""
    tagged = []
    cur = "other"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hit = None
        for sec, hints in SECTION_HINTS.items():
            # 标题行必须很短；否则「…临床技能大赛二等奖」这类含「技能」的
            # 长句会被误判成小标题，整条竞赛记录就被吞掉。
            if any(h in line for h in hints) and len(line) <= 12:
                hit = sec
                break
        if hit:
            cur = hit
            continue
        tagged.append((cur, line))
    return tagged


def _clean(line: str):
    line = re.sub(r"^[·•\-\*\d\.、\s]+", "", line)
    return line.strip()


def parse_sections(text: str):
    tagged = _sections(text)
    experiences, competitions, papers, certificates, skills = [], [], [], [], []
    seen = set()
    for sec, line in tagged:
        item = _clean(line)
        if not item or item in seen or len(item) < 4:
            continue
        # 基本信息行不进经历
        if re.match(r"^(姓名|学校|专业|学历|年级|毕业时间|手机|邮箱|电话|性别|年龄)\s*[:：]", item):
            continue
        seen.add(item)
        if sec == "competition" or re.search(r"(竞赛|大赛|挑战杯|数学建模|一等奖|二等奖|金奖|冠军|获奖)", item):
            competitions.append({"text": item})
        elif sec == "paper" or re.search(r"(论文|发表|收录|SCI|SSCI|EI|期刊|见刊)", item):
            papers.append({"text": item})
        elif sec == "cert" or re.search(r"(证书|六级|四级|雅思|托福|CFA|CPA|从业资格)", item):
            certificates.append(item)
        elif sec == "skill" or re.search(r"(熟练|掌握|精通|技能)", item):
            # 把「熟练使用 A、B、C」拆成短词，不要把整句当成一个技能标签
            parts = re.split(r"[、，,／/；;]|以及|和|与", item)
            picked = []
            for _p in parts:
                _s = _p.strip()
                # 去掉「技能：」「专业技能:」这类标签前缀，别把标签当技能
                _s = re.sub(r"^(专业技能|技能清单|技能特长|技能|能力|掌握|熟练|精通|熟悉)\s*[：:]\s*", "", _s)
                _s = _s.strip("：: ")
                if 2 <= len(_s) <= 16 and not re.match(r"^(熟练|掌握|精通|熟悉|能够|具备)", _s):
                    picked.append(_s)
            skills.extend(picked if picked else [item[:20]])
        else:
            experiences.append({"text": item})
    # 技能关键词：从全文里找，去重
    for w in SKILL_WORDS:
        if re.search(re.escape(w), text, flags=re.I):
            if not any(w in s for s in skills):
                skills.append(w)
    for c in CERT_WORDS:
        if c in text and c not in certificates:
            certificates.append(c)
    # 竞赛 / 论文同时并进 experiences，让前端经历列表能显示
    for x in competitions:
        experiences.append({"text": "竞赛：" + x["text"]})
    for x in papers:
        experiences.append({"text": "论文：" + x["text"]})
    return {
        "experiences": experiences[:20],
        "competitions": competitions[:10],
        "papers": papers[:10],
        "certificates": certificates[:15],
        "skills": [{"name": s, "evidence": "简历文本命中"} for s in skills[:15]],
    }


def _llm_enhance(text: str, base: dict):
    """可选：让 glm-4-flash 补全规则没抽到的字段。失败原样返回。"""
    cfg = _load_env()
    key = os.environ.get("DEEPSEEK_API_KEY") or cfg.get("DEEPSEEK_API_KEY", "")
    if not key:
        return base, {}
    endpoint = (os.environ.get("DEEPSEEK_BASE_URL") or cfg.get("DEEPSEEK_BASE_URL")
                or "https://open.bigmodel.cn/api/paas/v4")
    model = os.environ.get("DEEPSEEK_MODEL") or cfg.get("DEEPSEEK_MODEL") or "glm-4-flash"
    prompt = (
        "从下面这份简历里抽取信息，严格输出 JSON（不编造，抽不到就留空字符串）：\n"
        '{"name":"","school":"","major":"","degree":"","grade":"","graduation":"",'
        '"phone":"","email":"","competitions":[],"papers":[],"certificates":[]}\n'
        "只输出 JSON，不要解释。简历：\n" + text[:6000])
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.1}
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = json.loads(r.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{[\s\S]*\}", content)
        data = json.loads(m.group(0)) if m else {}
        for k, _ in FIELDS:
            if not base["fields"][k]["value"] and data.get(k):
                base["fields"][k] = {"value": str(data[k]), "source": "resume_llm",
                                     "evidence": "大模型补全"}
        for key2, bucket in (("competitions", "competitions"), ("papers", "papers"),
                             ("certificates", "certificates")):
            for x in (data.get(key2) or []):
                v = str(x).strip()
                if v and v not in [i.get("text") if isinstance(i, dict) else i
                                   for i in base[bucket]]:
                    base[bucket].append({"text": v} if bucket != "certificates" else v)
        return base, {"model": model, "used": True}
    except Exception as exc:
        return base, {"model": model, "used": False, "error": str(exc)[:80]}


def parse_resume(filename: str, data: bytes, use_llm: bool = False) -> dict:
    got = extract_text(filename, data)
    # 图片走 OCR 时多返回一个 method，普通格式只有两个
    if len(got) == 3:
        text, warnings, source_method = got
    else:
        text, warnings = got
        source_method = "text"
    if not text.strip():
        hint = ("图片没读到文字，请重拍或改传 .docx / .txt"
                if source_method.startswith("image") else
                "没能从文件里读到文字；请另存为 .txt / .md / .docx")
        return {"ok": False, "error": hint, "warnings": warnings,
                "source_method": source_method}
    fields = parse_fields(text)
    sec = parse_sections(text)
    missing = [label for key, label in FIELDS if not fields[key]["value"]]
    if missing:
        warnings.append("以下字段没抽到（不会猜）：" + "、".join(missing))
    warnings.append("解析的是本次上传的文件：" + (filename or "未命名"))
    if source_method.startswith("image"):
        warnings.append("来源：图片识别（视觉模型转写），字段请自行核对一遍再提交")
    out = {
        "ok": True, "filename": filename or "resume", "chars": len(text),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "method": "rules+llm" if use_llm else "rules", "model": "",
        "source_method": source_method,
        "fields": fields, "skills": sec["skills"],
        "experiences": sec["experiences"], "competitions": sec["competitions"],
        "papers": sec["papers"], "certificates": sec["certificates"],
        "warnings": warnings, "llm": {},
    }
    if use_llm:
        out, llm = _llm_enhance(text, out)
        out["llm"] = llm
        if llm.get("used"):
            out["method"] = "rules+llm"
            out["model"] = llm.get("model", "")
        else:
            out["method"] = "rules"
    return out


def handle_multipart(content_type: str, raw: bytes, use_llm: bool = False) -> dict:
    """从 multipart/form-data 里取出 file 字段（Python 3.13 无 cgi 模块）。"""
    try:
        header = ("Content-Type: " + (content_type or "") + "\r\n"
                  "MIME-Version: 1.0\r\n\r\n").encode("utf-8")
        # Python 3.13：默认 policy 返回 Message（没有 iter_parts），
        # 指定 policy.default 才会返回 EmailMessage；再留一层 walk 兜底。
        msg = BytesParser(policy=policy.default).parsebytes(header + raw)
        parts = (list(msg.iter_parts()) if hasattr(msg, "iter_parts")
                 else [p for p in msg.walk() if not p.is_multipart()])
        for part in parts:
            cd = part.get("Content-Disposition", "")
            if 'name="file"' in cd or "filename=" in cd:
                filename = part.get_filename() or "resume.txt"
                data = part.get_payload(decode=True) or b""
                return parse_resume(filename, data, use_llm)
    except Exception as exc:
        return {"ok": False, "error": "上传内容解析失败：%s" % str(exc)[:100]}
    return {"ok": False, "error": "没收到文件（字段名应为 file）"}
