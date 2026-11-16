"""把交接包的对话式采集页打包成离线单页，并把「名单 + 匹配」接在对话后面。

输入：_handoff/shuangxuan/app/static/{fair.html,fair.js,fair.css,style.css}
      demo/index.html（取 COMPANIES 真实企业数据与排序规则）
输出：demo/fair.html

页面流程（全在一页内）：
  画像对话 → 生成画像 → 导入企业名单 → 点「开始匹配」 → 结果列表
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from fair_report_js import REPORT_JS

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
JS_DIR = HERE / "js"


def _js(name):
    """读 ent_tools/js/ 下的前端源码。

    用 bytes 读再用 utf-8 解，避免 Windows 上 read_text 把换行翻译成 \r\n ——
    这几段 JS 是逐字内联进页面的，翻译了字节就变，页面 diff 会全是噪声。
    """
    return (JS_DIR / name).read_bytes().decode("utf-8")
HANDOFF = ROOT / "_handoff" / "shuangxuan" / "app" / "static"
DEMO = ROOT / "demo"

# v10：把打分用的城市/企业类型/规模规则从 rules.py 生成成 JS 常量，注入页面端，
# 保证服务端（fair_bridge.score_parts）与页面端（fair_report_js.calcOne）用同一套数据。
sys.path.insert(0, str(HERE / "demo_v2"))
import rules as R

def _rules_js():
    payload = {
        "city_cats": R.CITY_CATS,
        "city_first": R.CITY_FIRST,
        "city_new1": R.CITY_NEW1,
        "city_cap": R.CITY_CAPITAL,
        "city_econ": R.CITY_ECON,
        "city_transit": R.CITY_TRANSIT,
        "city_eco": R.CITY_ECO,
        "PM": R.CITY_PROVINCE,
        "adj": R.ADJ_PROVINCES,
        "ct_buckets": R.COMPANY_TYPE_BUCKETS,
        "scale_tiers": R.SCALE_TIERS,
        "industry_status_tiers": R.INDUSTRY_STATUS_TIERS,
        "industry_status_floor": R.INDUSTRY_STATUS_FLOOR,
        "growth_tiers": R.GROWTH_TIERS,
        "growth_floor": R.GROWTH_FLOOR,
        "city_rank_def": R.CITY_RANK_DEFAULT,
        "ct_rank_def": R.COMPANY_TYPE_RANK_DEFAULT,
        "ind_rank_def": R.INDUSTRY_RANK_DEFAULT,
    }
    return "const RULES = " + json.dumps(payload, ensure_ascii=False) + ";"

def patch_fair_js(js):
    """按队长的要求改采集流程。只改本页打包出来的副本，不动交接包原件。"""
    edits = []

    # ① 不再问手机号，只留邮箱
    js = js.replace(
        "ask: '如果现场有企业想联系你，留个手机号或邮箱？（可跳过）',",
        "ask: '现场有企业想联系你，留个邮箱？（可跳过）',")
    js = js.replace(
        "placeholder: '手机号或邮箱，可跳过',",
        "placeholder: '邮箱，可跳过',")
    js = js.replace(
        """    const digits = t.replace(/[\\s-]/g, '');
    if (/^1[3-9]\\d{9}$/.test(digits)) return { value: { phone: digits, email: '' } };
    return { error: '看起来不是手机号或邮箱。可以直接点「跳过」。' };""",
        """    return { error: '看起来不是邮箱。可以直接点「跳过」。' };""")
    edits.append("删除手机号提问")

    # ①b 联系方式整步删除（邮件也不问）
    _before = js
    js = js.replace(
        "if (!state.answers.identity.phone && !state.answers.identity.email) flow.push(contactStep);",
        "")
    if js == _before:
        raise RuntimeError("联系方式步骤没删掉，检查 fair.js 的 buildFlow")

    # 连定义一起删，别留死代码
    c0 = js.find("const contactStep = {")
    if c0 < 0:
        c0 = js.find("contactStep = {")
    if c0 > 0:
        c1 = js.find("\n};", c0)
        if c1 > c0:
            js = js[:c0] + js[c1 + 4:]
    edits.append("删除联系方式步骤")

    # ② 删除「找秋招还是实习」整步
    stage_start = js.find("    fairStep('stage',")
    stage_end = js.find("    fairStep('direction',", stage_start)
    if stage_start > 0 and stage_end > stage_start:
        js = js[:stage_start] + js[stage_end:]
        edits.append("删除求职阶段步骤")

    # ②b 方向步骤：去掉「参考项 + alias」这套离线岗位族规则，改成纯文本「有什么实习」。
    #    学生写的软件工程 / 数据分析 / 运筹优化等原样记录，不再被硬映射成岗位族名。
    OLD_DIR = """    fairStep('direction', '想做什么方向或岗位？点参考项可以，自己写也可以。', {
      options: Q.major_directions.map(d => d.name),
      allowCustom: true,
      placeholder: '比如：供应链计划 / 采购 / 数据分析，也可以直接写岗位',
      hint: '选项只是参考，不锁定岗位族；自己写的会原样记录，仅作参考。',
      alias: {
        供应链: '运营与供应链管理', 运营: '运营与供应链管理', 物流: '运营与供应链管理',
        金融: '金融服务与金融工程', 投资: '金融服务与金融工程',
        数据: '大数据分析与决策', 分析: '大数据分析与决策', 商业分析: '大数据分析与决策',
        工程: '复杂工程与智能管理', 项目: '复杂工程与智能管理',
        智能: '智能系统管理', 系统: '智能系统管理',
      },
      after(value, a) { a.direction_interest = value; },
      reset(a) { a.direction_interest = ''; },
    }),"""
    NEW_DIR = """    fairStep('internship', '做过哪些实习 / 竞赛 / 论文？写岗位、项目或方向就行，也可以留空。', {
      allowCustom: true,
      optional: true,
      placeholder: '比如：供应链实习 / 美赛 Finalist / 本科论文《…》，做过几个写几个',
      hint: '自由填写，原样记录；这段会直接喂给后面的岗位推荐模型。不套任何岗位族，想跳过就输入「跳过」。',
      after(value, a) { a.direction_interest = value; },
      reset(a) { a.direction_interest = ''; },
    }),"""
    if OLD_DIR not in js:
        raise RuntimeError("方向步骤没匹配到，检查 fair.js 的 direction 步骤")
    js = js.replace(OLD_DIR, NEW_DIR, 1)
    edits.append("方向步骤改为纯文本「有什么实习」，去掉离线岗位族参考项与 alias")

    # ②c 删除「想在哪座城市工作？」（期望城市）整步；城市维度改按「期望城市不限，中性分 0.60」处理。
    rg_start = js.find("    fairStep('region',")
    rg_end = js.find("    fairStep('timeline',", rg_start)
    if rg_start > 0 and rg_end > rg_start:
        js = js[:rg_start] + js[rg_end:]
        edits.append("删除期望城市（想去哪个城市）步骤")

    # v13：岗位推荐不再插进 fairSteps() 数组开头（那样会排在「实习/竞赛/论文」之前，
    # 大模型拿不到经历信息）。改由 buildFlow 在经历题与教培题之后 push。
    _flow_anchor = "  fairSteps().forEach(step => flow.push(step));\n"
    if _flow_anchor not in js:
        raise RuntimeError("buildFlow 的 fairSteps forEach 定位没找到，无法调整岗位推荐顺序")
    js = js.replace(_flow_anchor,
        _flow_anchor
        + "  /* v13：先问完经历和教培接受度，再让大模型据此推荐岗位 */\n"
        + "  flow.push(noMajorAcceptStep());\n"
        + "  flow.push(jobRecommendStep());\n", 1)
    edits.append("教培接受度与岗位推荐移到实习/竞赛/论文之后、大模型推荐之前")

    # v15：简历里已抽到经历（实习/竞赛/论文）时不再重复追问。简历摘要卡已经
    # 让学生逐项确认过「从简历里抽到这些」，再问一遍「做过哪些实习/竞赛/论文」
    # 纯属重复（用户实测反馈）。没抽到经历才保留原题。
    _old_flow = "  fairSteps().forEach(step => flow.push(step));\n"
    _new_flow = (
        "  /* v15：简历已抽到经历时跳过「实习/竞赛/论文」追问，直接用简历里的。 */\n"
        "  const _resumeExpN = (function(){ const r = state.resume || {};\n"
        "    return ((r.experiences || []).length + (r.competitions || []).length\n"
        "      + (r.papers || []).length) || (((r.extraction || {}).experiences) || []).length;\n"
        "  })();\n"
        "  fairSteps().forEach(step => { if (step.id === 'internship' && _resumeExpN > 0) return; flow.push(step); });\n")
    if _old_flow not in js:
        raise RuntimeError("buildFlow 的 fairSteps forEach 定位没找到（v15 跳过经历题）")
    js = js.replace(_old_flow, _new_flow, 1)
    edits.append("简历有经历时跳过「实习/竞赛/论文」追问（v15）")

    # v15b：不再问「哪段经历最想让我们看」（用户：为什么还问什么展示，到简历里找就是）。
    # 多段经历自动取第一段并说明；想换说法学生可以在报告里改。
    _old_flag = """    const exps = state.resume.experiences || [];
    if (exps.length > 1) {
      const picked = await flagshipCard();
      if (picked && picked.text) state.answers.fair.flagship_experience = picked.text;
    } else if (exps.length === 1) {
      state.answers.fair.flagship_experience = exps[0].text;
    }"""
    _new_flag = """    const exps = state.resume.experiences || [];
    /* v15：不再弹「哪段经历最想让我们看」。多段自动取第一段，消息里说明清楚。 */
    if (exps.length) {
      state.answers.fair.flagship_experience = exps[0].text;
      await say('经历直接用简历里的（共 ' + exps.length
        + ' 段，匹配优先看第 1 段），不再重复问你。', { fast: true });
    }"""
    if _old_flag not in js:
        raise RuntimeError("init 的 flagshipCard 分支没匹配到（v15b）")
    js = js.replace(_old_flag, _new_flag, 1)
    edits.append("简历有多段经历时自动取第一段，不再弹「哪段经历最想让我们看」（v15）")

    # ③ 删除「最晚什么时候落地」整步
    tl_start = js.find("    fairStep('timeline',")
    tl_end = js.find("  ];\n}\n\nfunction buildFlow", tl_start)
    if tl_start > 0 and tl_end > tl_start:
        js = js[:tl_start] + js[tl_end:]
        edits.append("删除时间窗步骤")

    # ③b 按用户允许「删减问题」：不再插入「是否考虑专业不对口的工作」这一步。
    # 岗位对口已由采集端的「推荐岗位 → 学生勾选」承担，重复提问没有信息增量。

    # ④ 偏好卡改为必答
    js = js.replace("ask: '要不要让匹配更懂你？选填，约 20 秒。',",
                    "ask: '最后一步，必答：让匹配更懂你。',")
    js = js.replace("""        <div class="cc-title">要不要让匹配更懂你？</div>
        <div class="cc-sub">选填，约 20 秒。偏好只影响排序权重，不是硬门槛；跳过不影响这份画像。</div>""",
                    """        <div class="cc-title">让匹配更懂你</div>
        <div class="cc-sub">必答，约 20 秒。至少选 1 项最看重的；只影响排序权重，不是硬门槛。</div>""")
    js = js.replace("""          <button type="button" class="btn btn-p" data-act="save">保存并继续</button>
          <button type="button" class="btn btn-g" data-act="skip">跳过</button>""",
                    """          <button type="button" class="btn btn-p" data-act="save">保存并继续</button>""")
    js = js.replace("""        root.querySelector('[data-act="save"]').onclick = () => {
          const res = {""",
                    """        root.querySelector('[data-act="save"]').onclick = () => {
          if (!selected.length) {
            const tip = root.querySelector('.cc-sub');
            if (tip) tip.textContent = '未选「看重什么」，将按默认 7 项权重匹配（仍可正常出报告）。';
          }
          const res = {""")
    js = js.replace("""        root.querySelector('[data-act="skip"]').onclick = () => {
          state.awaiting = null;
          el.classList.add('resolved');
          root.querySelectorAll('button,input').forEach(x => { try { x.disabled = true; } catch (e) {} });
          resolve({ skipped: true });
        };
""", "")
    edits.append("偏好卡改为必答")

    # ③c v10：在偏好卡（含家乡城市）之后，追问三类排序偏好。
    #    城市 7 类 / 企业类型三档 / 行业排序，结果写入
    #    state.answers.city_rank / company_type_rank / industry_rank，
    #    经 SHIM _buildDocument 落进 doc.answers，报告端 getProfile 直接读取参与重算。
    #    v11：排序追问必须与用户在「最看重什么」里选的关注点对应——没选城市条件
    #    就不显示城市 7 类（新一线等）排序，没选企业类型就不显示三档排序，以此类推。
    _after_pref = "  flow.push(preferencesCardStep());\n"
    if _after_pref not in js:
        raise RuntimeError("偏好卡步骤没找到，检查 fair.js 的 buildFlow")
    js = js.replace(_after_pref,
        "  flow.push(preferencesCardStep());\n"
        "  /* v11：只追问用户选中的关注点；guard 在运行时读取偏好卡结果 */\n"
        "  const _prefPicked = k => (((state.answers.values || {})[k] || 0) > 0);\n"
        "  const _rankIfPicked = (s, k) => Object.assign(s, { guard: () => _prefPicked(k) });\n"
        "  flow.push(_rankIfPicked(cityRankStep(), 'city'));\n"
        "  flow.push(_rankIfPicked(companyTypeRankStep(), 'company_type'));\n"
        "  flow.push(_rankIfPicked(industryRankStep(), 'industry'));\n", 1)
    edits.append("插入城市/企业类型/行业三类排序追问（按关注点联动）")

    # ③d v11：流程引擎支持 guard，跳过的步骤连提问气泡都不出现。
    _run_anchor = "    const step = state.flow[i];\n"
    if _run_anchor not in js:
        raise RuntimeError("流程引擎 step 定位没找到，检查 fair.js 的 runFlow")
    js = js.replace(_run_anchor,
        _run_anchor + "    if (step.guard && !step.guard()) continue;  // v11：按所选关注点动态跳过\n", 1)
    edits.append("排序追问按关注点动态跳过（未选的维度不显示）")

    # ③g v12：简历上传支持图片（手机拍简历照片直接传）。
    #    服务端 resume_parse 已接 GLM-4V OCR；前端只需放开 accept，
    #    并把文案里的「支持格式」补上图片。
    _old_accept = 'accept=".docx,.pdf,.txt,.md,.markdown,.html,.htm"'
    _new_accept = 'accept=".docx,.pdf,.txt,.md,.markdown,.html,.htm,.jpg,.jpeg,.png,.webp,.bmp"'
    if _old_accept in js:
        js = js.replace(_old_accept, _new_accept, 1)
        edits.append("简历上传放开图片格式（jpg/png/webp/bmp）")
    #    只放开 accept 不够：学生看不到「能传照片」就不传，功能等于不存在。
    #    这里把提示文案补一句，和 accept 一起改，避免只改了后端没人知道。
    _old_sub = "带上简历我会直接读出学校、专业、项目经历，只再问你 4 个问题；没有简历也能聊。"
    _new_sub = _old_sub + "手机拍的简历照片（jpg / png）也能传，会先转成文字再解析。"
    if _old_sub in js and "简历照片" not in js:
        js = js.replace(_old_sub, _new_sub, 1)
        edits.append("简历上传提示补充「支持照片」说明")

    # ③f v12（2026-11-13 用户要求）：两类硬约束问答。
    #   ① hard_filter：自由填空，如「只要南京的工作」「只看互联网」。
    #      这一步产出的是**硬约束**，进匹配端后由大模型解析成结构化条件，
    #      命中的企业以外的全部剔除（不是降权）。空着就代表不设限。
    #   ② 教培接受度：教培等「无专业要求」企业默认剔除，学生明确接受才纳入。
    # 注意：这里必须挂在 buildFlow 里的 flow.push(...) 段之后，
    # 不能挂进 fairSteps() 的 return [ ... ] 数组字面量（否则 const 语句非法）。
    _anchor_hf = "  flow.push(industryRankStep(), 'industry'));\n"
    _anchor_hf2 = "  flow.push(_rankIfPicked(industryRankStep(), 'industry'));\n"
    _use = _anchor_hf2 if _anchor_hf2 in js else _anchor_hf
    if _use not in js:
        raise RuntimeError("buildFlow 的 industryRankStep 定位没找到，无法插入硬约束步骤")
    js = js.replace(_use,
        _use
        + "  /* v12：硬约束（自由填空）排在排序追问之后；教培接受度已提前到岗位推荐之前 */\n"
        + "  flow.push(hardFilterStep());\n", 1)
    # 两个 step 以函数形式定义，避免再往数组字面量里塞内容。
    _step_defs = (
        "function hardFilterStep() {\n"
        "  return fairStep('hard_filter', '有没有硬性要求？只写一句就行，比如「只要南京的工作」「只看互联网」。没有就输入「没有」。', {\n"
        "    allowCustom: true,\n"
        "    optional: true,\n"
        "    placeholder: '例如：只要南京 / 只看互联网 / 不要外企',\n"
        "    hint: '这句会被当成硬条件：不满足的企业直接从名单里删掉，不是降权。空着或输入「没有」= 不设限。',\n"
        "    after(value, a) {\n"
        "      const v = String(value || '').trim();\n"
        "      a.hard_filter = /^(没有|无|不设限|暂无|随便|都行)$/.test(v) ? '' : v;\n"
        "    },\n"
        "    reset(a) { a.hard_filter = ''; },\n"
        "  });\n"
        "}\n"
        "function noMajorAcceptStep() {\n"
        "  return fairStep('no_major_accept', '教培机构这类「不要求专业对口」的企业，你愿意考虑吗？', {\n"
        "    options: ['愿意考虑', '不考虑'],\n"
        "    alias: { 愿意: '愿意考虑', 可以: '愿意考虑', 接受: '愿意考虑', 考虑: '愿意考虑',\n"
        "             不: '不考虑', 不要: '不考虑', 拒绝: '不考虑', 不接受: '不考虑' },\n"
        "    hint: '教培、销售等岗位大多不限专业。选「不考虑」时，这类企业会从名单中剔除，避免它们占据靠前位置。',\n"
        "    after(value, a) { a.accept_no_major = (value === '愿意考虑'); },\n"
        "    reset(a) { a.accept_no_major = false; },\n"
        "  });\n"
        "}\n\n"
    )
    _def_anchor = "function fairSteps() {\n"
    if _def_anchor not in js:
        raise RuntimeError("fairSteps 定位没找到，无法定义硬约束步骤")
    js = js.replace(_def_anchor, _step_defs + _def_anchor, 1)
    edits.append("新增硬约束填空（hard_filter）与教培接受度（accept_no_major）两步")

    # ③e v11.1：?detail=企业名 / ?report=1 直接进报告页，不再从头跑采集问卷
    _init_anchor = "async function init() {\n"
    if _init_anchor not in js:
        raise RuntimeError("init() 定位没找到，检查 fair.js 的 init")
    js = js.replace(_init_anchor, _init_anchor + (
        "  /* v11.1 报告模式：新标签打开 ?detail= 时，本标签没有采集记录，\n"
        "     原逻辑会把用户当新访客从头问一遍 —— 这里直接渲染报告。 */\n"
        "  const _modeQ = new URLSearchParams(location.search);\n"
        "  if (_modeQ.get('detail') || _modeQ.get('report')) {\n"
        "    document.body.classList.add('report-only');\n"
        "    const _st = document.getElementById('status-t');\n"
        "    if (_st) _st.textContent = '双选会匹配报告';\n"
        "    document.title = (_modeQ.get('detail') ? _modeQ.get('detail') + ' · ' : '') + '双选会匹配报告';\n"
        "    let _mounted = false, _refreshed = false, _tries = 0;\n"
        "    const _go = function(){\n"
        "      try {\n"
        "        if (!_mounted && window.__mountMatch) {\n"
        "          window.__mountMatch({});\n"
        "          _mounted = true;\n"
        "        }\n"
        "        if (_mounted && !_refreshed && window.__triRefresh) {\n"
        "          window.__triRefresh();\n"
        "          _refreshed = true;\n"
        "        }\n"
        "      } catch (e) {}\n"
        "    };\n"
        "    _go();\n"
        "    const _tk = setInterval(function(){\n"
        "      _tries++; _go();\n"
        "      if ((_mounted && _refreshed) || _tries > 60) clearInterval(_tk);\n"
        "    }, 100);\n"
        "    return;\n"
        "  }\n"), 1)
    edits.append("?detail= / ?report=1 直接渲染报告，不再重跑采集问卷")

    return js, edits


html = (HANDOFF / "fair.html").read_text(encoding="utf-8")
style = (HANDOFF / "style.css").read_text(encoding="utf-8")
fair_css = (HANDOFF / "fair.css").read_text(encoding="utf-8")
fair_js = (HANDOFF / "fair.js").read_text(encoding="utf-8")
fair_js, PATCH_LOG = patch_fair_js(fair_js)
questionnaire = json.loads((DEMO / "questionnaire.json").read_text(encoding="utf-8"))

# ⑤ 企业类型选项对齐匹配端的 nature 枚举
questionnaire["fair"]["company_types"] = ["央企", "国企", "民企", "外企", "合资", "事业单位"]
PATCH_LOG.append("企业类型改为 nature 枚举")

# ⑥ v6：偏好卡换成真正参与排序的五项 100（去掉六维价值观与双休）。
#    键名与 rules.DIMENSIONS / fair_bridge.weights_from 完全一致，
#    学生填的分就是匹配端权重，不再有两套语义错配的偏好。
questionnaire["value_dimensions"] = [
    ["city", "城市条件（一线 / 新一线 / 省会 / 其他）"],
    ["home", "离家近"],
    ["company_type", "企业类型（央企 / 国企 / 民企 / 外企）"],
    ["industry", "行业"],
    ["scale", "企业规模档"],
    ["industry_status", "行业地位（500强 / 行业龙头）"],
    ["growth", "成长性（高速成长企业）"],
]
questionnaire["value_dimensions_note"] = (
    "七项合计 100 分，就是排序权重；会前拿不到的薪酬 / 双休不在这里打分，"
    "会转成报告里的现场提问。")
PATCH_LOG.append("偏好卡改为五项 100（与匹配端同键）")

def _companies_from_db(limit_req=300):
    """采集端「开始匹配」用的内置企业数组（index.html 里那份是空的）。

    直接从活库 company_jobs.item_json 生成，字段对齐 match() 需要的：
    id / name / city / province / nature / industry / jobs[] / jobSource。
    岗位描述只截前 300 字（仅供关键词打分，避免页面体积暴涨）。
    """
    import sqlite3
    db = ROOT / "ent_tools" / "data" / "careersafari.db"
    if not db.exists():
        return "[]"
    out = []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute("SELECT name, item_json FROM company_jobs ORDER BY name"):
            try:
                it = json.loads(row["item_json"] or "{}")
            except Exception:
                continue
            jobs = []
            for j in (it.get("jobs") or []):
                title = (j.get("title") or "").strip()
                if not title:
                    continue
                jobs.append({
                    "title": title,
                    "requirements": (j.get("requirements") or "")[:limit_req],
                    "salary": j.get("salary") or "未知",
                    "urgency": j.get("urgency") or "未知",
                    "headcount": j.get("headcount") or "未知",
                })
            out.append({
                "id": it.get("id") or row["name"],
                "name": it.get("name") or row["name"],
                "city": it.get("city") or "未知",
                "province": it.get("province") or "",
                "nature": it.get("nature") or "",
                "industry": it.get("industry") or "",
                "jobs": jobs,
                "jobSource": it.get("jobSource") or "",
            })
    finally:
        con.close()
    return json.dumps(out, ensure_ascii=False)


# 从 index.html 里取真实企业数据；为空则直接用活库补齐（否则「开始匹配」是 0 家）
index_html = (DEMO / "index.html").read_text(encoding="utf-8")
m = re.search(r"const COMPANIES = (\[.*?\n\]);", index_html, re.S)
COMPANIES_JSON = m.group(1) if m else "[]"
if COMPANIES_JSON.count('"id"') == 0:
    COMPANIES_JSON = _companies_from_db()
    PATCH_LOG.append("采集端内置企业数组改为从活库 company_jobs 生成（原先为空，导致「开始匹配」0 家）")

names_file = ROOT / "ent_tools" / "data" / "company_names.json"
NAMES = json.loads(names_file.read_text(encoding="utf-8-sig")) if names_file.exists() else []
# 名单与库对齐，避免「内置名单 118 家」但报告 122 家对不上
try:
    _db_names = [c["name"] for c in json.loads(COMPANIES_JSON)] if COMPANIES_JSON != "[]" else []
    if _db_names and len(_db_names) != len(NAMES):
        NAMES = _db_names
        PATCH_LOG.append("内置名单与活库企业数对齐（%d 家）" % len(NAMES))
except Exception:
    pass

SHIM = _js("shim.js")
SHIM = SHIM.replace("__QUESTIONNAIRE__", json.dumps(questionnaire, ensure_ascii=False))

# 采集端「岗位推荐」：按专业 + 实习给几个具体岗位名，学生勾选后写进 intended_jobs[]。
# 输出不是岗位族、不是企业事实；联网时由 /api/fair/recommend-jobs 调模型，离线时规则兜底。
RECOMMEND_JS = _js("recommend.js")

MATCH = _js("match.js")

MATCH = MATCH.replace("__COMPANIES__", COMPANIES_JSON) \
             .replace("__NAMES__", json.dumps(NAMES, ensure_ascii=False))

# ── 组装 ───────────────────────────────────────────────
html = html.replace('<link rel="stylesheet" href="/static/style.css">', "<style>\n" + style + "\n</style>")
html = html.replace('<link rel="stylesheet" href="/static/fair.css">', "<style>\n" + fair_css + "\n</style>")
html = re.sub(r'<script src="/static/fair\.js"[^>]*></script>', "", html)
html = html.replace('<a class="toplink" href="/">完整版 →</a>', "")
html = html.replace(
    '<p class="muted small">点下面的选项，或者直接打字。只问 4 个问题，一分钟左右，最后给你一份可以下载的 JSON。</p>',
    '<p class="muted small">面向研三 / 大四：按校招画像生成。点选项或直接打字，一分钟左右，最后给你一份可以下载的 JSON。</p>')
html = html.replace(
    '<span>CareerSafari · 双选会采集端 · 只采集与导出画像，不做岗位推荐</span>',
    '<span>CareerSafari · 双选会采集端 · 采集画像 + 按专业/实习推荐候选岗位（由学生勾选）</span>')

# 三端协调报告：把采集端画像 + 企业端 118 家 + 匹配端模型结果内联进页面。
# 页面形式不变，只把 #mres 的内容换成完善报告（见 fair_report_js.py）。
TRI_PATH = Path(__file__).resolve().parent / "data" / "fair_payload.json"
TRI_JSON = TRI_PATH.read_text(encoding="utf-8") if TRI_PATH.exists() else "{}"
REPORT_BUNDLE = REPORT_JS.replace("__TRI_DATA__", TRI_JSON).replace("__RULES_JS__", _rules_js())

bundle = (SHIM + RECOMMEND_JS + "<script>\n" + fair_js + "\n</script>\n"
          + MATCH + REPORT_BUNDLE + "</body>")
html = html.replace("</body>", bundle, 1)

out = DEMO / "fair.html"
out.write_text(html, encoding="utf-8")

# 构建戳：health.py 靠它判断「demo/fair.html 有没有被人直接手改」。
# fair.html 是生成物，手改它下次构建就全丢，改的人却以为自己改好了。
# 只在正常构建时刷新；直接编辑 html 不会动这个文件，于是下次体检立刻露馅。
_stamp_p = HERE / "data" / ".page_stamp.json"
try:
    _stamp = {}
    if _stamp_p.exists():
        _stamp = json.loads(_stamp_p.read_text(encoding="utf-8"))
    _stamp.setdefault("_note", "build_fair_page.py 自动维护。手改 demo/fair.html 会让 md5 对不上。")
    _stamp[str(out.resolve())] = {
        "md5": hashlib.md5(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _stamp_p.write_text(json.dumps(_stamp, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
except Exception as _e:      # 构建戳写不进去不该阻断页面生成
    print("  [warn] 构建戳写入失败：%s" % _e)

print("生成", out)
print("  改动：" + "、".join(PATCH_LOG))
print("  大小 %.1f KB；内置企业 %d 家；内置名单 %d 家" %
      (out.stat().st_size / 1024, COMPANIES_JSON.count('"id"'), len(NAMES)))