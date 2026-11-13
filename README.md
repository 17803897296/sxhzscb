# 双选会助手 · CareerSafari Fair

把「招聘会现场几百家企业」变成「一张按你自己的偏好排好的清单」的本地演示系统。

学生用对话回答 6 个问题，系统就把双选会上的 **122 家企业、762 条真实校招岗位**按 7 个维度打分排序，每一家都给出**算分依据、缺口、现场要问的问题、证据链接**；同时用大模型判定「企业的真实岗位名」和「你填的意向岗位」是不是同一类。

> 核心立场：不做「黑箱大模型排序」。分数是**可复算的加权和**，大模型只负责两件它擅长的事——**读语义**（岗位名同类判定）和**写人话**（推荐岗位、提示缺口），不参与打分。

```
采集端（对话问卷）  →  服务端（画像落库 + 打分数据）  →  匹配端（排行 + 单家完整报告）
      6 个问题                  SQLite + JSON                  7 维加权 · 三态分级
```

---

## 目录

- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [用户视角的完整流程](#用户视角的完整流程)
- [一、采集端：6 个问题问出什么](#一采集端6-个问题问出什么)
- [二、匹配端：7 维打分怎么算](#二匹配端7-维打分怎么算)
- [三、岗位语义匹配：大模型在这里做什么](#三岗位语义匹配大模型在这里做什么)
- [四、岗位推荐与「换一批」](#四岗位推荐与换一批)
- [五、简历解析](#五简历解析)
- [六、企业搜索的三层取数](#六企业搜索的三层取数)
- [数据结构与 API](#数据结构与-api)
- [诚实性设计](#诚实性设计)
- [重建与维护](#重建与维护)
- [数据口径](#数据口径)
- [已知限制](#已知限制)
- [技术选型](#技术选型)

---

## 快速开始

**环境**：Python 3.10+。**零第三方依赖**（核心链路只用标准库：`http.server` / `sqlite3` / `json` / `urllib` / `zipfile`）。

```bash
cd ent_tools
python -X utf8 fair_server.py --port 8102 --host 127.0.0.1
```

浏览器打开：

```
http://127.0.0.1:8102/fair.html
```

第一次打开是采集问卷（AI 助手对话），答完 6 题会自动生成画像、落库、重建报告。之后：

| 地址 | 内容 |
|---|---|
| `/fair.html` | 采集问卷（新访客） |
| `/fair.html?report=1` | 直接看排行列表（跳过问卷） |
| `/fair.html?detail=企业全名` | 单家企业完整报告钻取页 |

**只想看界面、不起服务**：直接双击 `demo/fair.html`（单文件内联了数据与逻辑，`file://` 可跑，此时走内置垫片，不联网、不写库）。

**想接大模型**（岗位语义判定 / 岗位推荐 / 简历增强）在 `ent_tools/.env` 里配：

```ini
# ent_tools/.env
DEEPSEEK_API_KEY=你的key
DEEPSEEK_MODEL=glm-4-flash
DEEPSEEK_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

不配也能跑：语义匹配退回字面匹配，岗位推荐退回词表规则，简历解析退回正则。

---

## 目录结构

```
careersafari-fair/
├── ent_tools/                     # 后端与构建脚本（29 个 py，共 ~5.5k 行）
│   ├── fair_server.py             # 同源服务：页面 + 画像落库 + 报告数据（225 行）
│   ├── fair_bridge.py             # 业务核心：库 → payload（打分字段 + 三态 + 可信度，667 行）
│   ├── fair_ingest.py             # 画像写入 fair_profiles / fair_active
│   ├── fair_report_js.py          # 报告页前端逻辑（打分 + 渲染，680 行，注入 HTML）
│   ├── build_fair_page.py         # 把问卷页 + 报告页打包成单文件 fair.html（1682 行）
│   ├── demo_v2/rules.py           # 打分规则唯一来源：维度、城市分档、规模档、榜单分档（372 行）
│   ├── intent_semantics.py        # 大模型语义匹配（岗位 ↔ 意向），带缓存
│   ├── job_recommend.py           # 校招岗位推荐（大模型 + 词表兜底）
│   ├── resume_parse.py            # 简历解析（txt/md/html/docx，正则 + 可选大模型）
│   ├── portal.py                  # 企业搜索三层取数（库 → 离线库 → 联网补）
│   ├── job_store.py / job_lookup.py / company_module.py
│   └── data/
│       ├── careersafari.db        # 活库（8 表：企业/岗位/画像/v4.2/快照…）
│       ├── v42_company_match.db   # v4.2 企业档案（35 列 / 118 家，含榜单与成长性）
│       ├── fair_payload.json      # 报告数据缓存（每次 GET 校验画像 ID，变了就重建）
│       └── companies.json 等      # 企业名单与离线检索库
├── demo/
│   ├── fair.html                  # 构建产物：单文件应用（1.3 MB，可离线双击）
│   ├── index.html                 # 旧版搜索页（构建时提供名单与排序规则）
│   └── 架构图.svg
├── _handoff/shuangxuan/app/static/  # 采集端原始前端（fair.js / fair.css / style.css）
├── docs/                          # 口径文档：权重分配、问卷设计、可采性分析
└── README.md
```

---

## 用户视角的完整流程

```
① 扫码/打开页面
      ↓  AI 助手逐条提问（6 问）
② 填专业、意向岗位、家乡、目标城市、怎么看企业、最看重什么
      ↓  提交（POST /api/fair/profile）
③ 画像落库 + 报告数据重建（约 0.2s）
      ↓
④ 看到 122 家企业分三档：明确命中 / 按行业推断 / 不对口
      ↓  点某家「查看完整报告」
⑤ 单家页：7 维算分依据 · 缺口 · 现场该问什么 · 证据链接 · v4.2 企业档案
```

关键点：**第 ③ 步是自动的**。服务端每次返回报告数据都会比对「缓存里的画像 ID」和「库里生效画像 ID」，不一致就重建——现场换人重新采集，排名立刻跟着变，不会串到上一个人的结果。

---

## 一、采集端：6 个问题问出什么

对话流程在 `_handoff/shuangxuan/app/static/fair.js`，构建时被 `build_fair_page.py` 打补丁后内联进 `fair.html`。

| 问题 | 落到哪个字段 | 用在哪 |
|---|---|---|
| 你学什么专业 | `major` | 岗位推荐词表、语义匹配 |
| 你想做什么岗位（可多选/自由填） | `intended_jobs` | 语义匹配、命中判定 |
| 你家在哪个城市 | `home_city` | 「离家近」维度 |
| 你想去哪些城市（排序） | `city_rank` | 「城市条件」维度 |
| 你怎么看企业（排序：国企/民企/外企） | `company_type_rank` | 「企业类型」维度 |
| 你最看重什么（多选，不限数量） | `values` | 决定「排序追问」显示哪几组，并折算成 7 维权重 |

两个设计细节：

1. **「最看重什么」可多选、不限数量**，选了哪些维度就问哪些维度的排序追问（运行时 `guard` 判断），没选的不显示提问气泡。
2. **没填完整也能匹配**。`values` 为空时用 7 维默认权重，不会因为少答一题就出不来结果。

画像文档用 JSON 存，`fair_profiles.document_json`：

```json
{
  "identity": { "name": "…", "school": "…" },
  "fair": { "home_city": "山东枣庄", "target_company_types": [] },
  "answers": {
    "major": "管理科学与工程",
    "intended_jobs": ["管理培训生", "供应链管理专员", "项目管理助理"],
    "region": "南京",
    "values": { "city": true, "home": true, "industry_status": true },
    "tri_weights": { "city": 34, "home": 22, "industry_status": 22, "scale": 22 }
  }
}
```

---

## 二、匹配端：7 维打分怎么算

### 维度与权重

规则唯一来源是 `ent_tools/demo_v2/rules.py` 的 `DIMENSIONS`，Python 侧（`fair_bridge`）和 JS 侧（`fair_report_js`）都读它，构建时序列化成前端常量 `RULES`，**保证服务端和页面端用同一套数值**。

| 维度 | id | 含义 | 默认权重 |
|---|---|---|---|
| 城市条件 | `city` | 工作城市的经济/行政层级，按你的城市偏好排序取最优 | 18 |
| 离家近 | `home` | 同城 1.0 / 同省 0.8 / 邻省 0.6 / 其他 0.3 | 14 |
| 企业类型 | `company_type` | 国央企 / 民企 / 外企 三档，按你的排序 | 16 |
| 行业对口 | `industry` | 对口是硬约束；不在偏好排序内给 0.4 中性地板 | 14 |
| 企业规模档 | `scale` | `龙头特大 1.0 / 大型 0.85 / 中型 0.65 / 中小型 0.45 / 微型 0.30`，客观判定 | 14 |
| 行业地位 | `industry_status` | 世界500强 1.0 / 中国500强 0.95 / 省100强 0.9 / 全国行业龙头 0.85 / 细分领先 0.7，**无榜 0.6 中性** | 12 |
| 成长性 | `growth` | 高速成长 1.0 / 仅有弱证据 0.7，**无信号 0.55 中性** | 12 |

权重不是固定值：学生勾选的关注点会折算成每个维度的权重（合计归一 100），没勾的维度权重为 0，但**仍然算分并显示依据**，只是不影响排序。

### 计算公式

页面端 `calcOne()` 的核心（`fair_report_js.py`）：

```js
// 每个维度得到一个 0~1 的 value，乘权重，按「本次实际参与打分的权重和」归一
let pool = 0;
Object.keys(vals).forEach(function (k) { pool += (Number(w[k]) || 0); });
pool = pool || 1;

const total = Object.keys(w).reduce(function (n, k) { return n + (Number(w[k]) || 0); }, 0) || 100;

const parts = Object.keys(vals).map(function (k) {
  const ww = Number(w[k]) || 0;
  return {
    id: k,
    name: DIM_LABEL[k] || k,
    weight: ww,
    value: +vals[k].v.toFixed(2),
    contribution: +(ww / pool * vals[k].v * 100).toFixed(1),  // 归一后的得分贡献
    basis: vals[k].b                                        // 人话依据，直接显示给用户
  };
});

const score = +parts.reduce(function (n, p) { return n + p.contribution; }, 0).toFixed(1);
```

所以分数不是「原始加权和」，而是**在本次能拿到的维度上归一**：如果某学生只勾了 3 个维度，得分就是这 3 维的加权平均（满分仍是 100），并额外输出 `effective_weight`（本次参与打分的权重占比）告诉用户「这个分数只用了 100% 里的多少」。

### 「多工作地」怎么算

一家公司可能在上海、济南都有岗位，用户的家乡是济南、想去的城市是上海。做法是**遍历全部工作城市，每个维度各取最高分**：

```js
function workCities(r) {
  const w = (r.v42 && r.v42.work_cities) || r.work_cities || [];
  const list = (Array.isArray(w) ? w : String(w).split(/[、,，\/]/)).map(s => String(s).trim()).filter(Boolean);
  return list.length ? list : (r.city ? [r.city] : []);
}

// 城市条件：命中用户偏好排名最靠前（最高分）的那个工作城市
wcs.forEach(city => cityCategories(city).forEach(cat => {
  if (cr[cat] != null && (best === null || cr[cat] < best.rank)) best = { cat, rank: cr[cat], city };
}));

// 离家近：取离家最近（得分最高）的那个工作地
wcs.forEach(cc => {
  let v;
  if (cc === home) v = 1.0;
  else if (hp && hhp && hp === hhp) v = 0.8;                              // 同省
  else if (hhp && hp && RULES.adj[hhp] && RULES.adj[hhp].indexOf(hp) >= 0) v = 0.6;  // 邻省
  else v = 0.3;
  if (bestH === null || v > bestH.v) bestH = { v, city: cc };
});
```

这一改动很关键：原来只用主城市字段 `city`，会漏掉 18 家「主城市不在列表里、但实际有对口工作地」的企业。

### 三态分级（不是简单排名）

| state | 含义 | 判定依据 |
|---|---|---|
| `hit` | 有真实 JD 命中 | 岗位名/岗位族与意向匹配，且有 JD 记录 |
| `inferred` | 按行业推断，待现场核实 | 企业属对口行业、岗位没查到或没公开 JD |
| `miss` | 无对口岗位 | 行业与意向都不对口 |

排序永远 `hit → inferred → miss`，`inferred` 会带**显著提示**「这是按行业推断的，请到场核实」，并且把「现场该问什么」顶到最高优先级。

---

## 三、岗位语义匹配：大模型在这里做什么

字面匹配会漏掉大量同义岗位（「供应链管理专员」和「采购计划员」是一类，但字符串完全不重叠）。做法是**让大模型只做「是不是同一类岗位」的判定**，不做排序、不打分。

`ent_tools/intent_semantics.py`：

```python
# 每个意向岗位一次短调用（4 个意向合成一次大 JSON 会超时 → 拆成单次，总耗时约 22s）
result = ask_model(
    system="你是校园招聘岗位分类助手。只判断两个岗位名是否属于同一类求职方向。",
    user=f"意向岗位：{intent}\n候选岗位名（来自企业校招公告）：{titles}\n"
         f"只输出属于同一类的岗位名，JSON 数组，不要解释。"
)
```

- 结果缓存到 `data/intent_semantics_cache.json`，**失败时沿用上一次判定**，不会因为模型抖动让排名跳变。
- 只对「当前生效画像的意向岗位」做映射（不再给每个用户硬塞默认画像的意向）。
- 覆盖不到时退回字面/岗位族兜底，所以没配 key 也能用。
- 前端 `matchIntent()` 输出的是「你的意向 X ↔ 企业岗位 Y（判据）」，直接展示成大模型的判定结果，可核对。

效果：命中企业数从「字面匹配 87 家」收紧到「语义匹配 51 家」——**更少但更准**，避免把「运营优化」这种泛词当命中。

---

## 四、岗位推荐与「换一批」

采集问卷结束后会推荐校招岗位，走 `POST /api/fair/recommend-jobs`。

```bash
curl -X POST http://127.0.0.1:8102/api/fair/recommend-jobs \
  -H "Content-Type: application/json" \
  -d '{"major":"计算机科学与技术","exclude":["后端开发工程师"]}'
```

- 词表 `MAJOR_BASE` 覆盖 10 个专业，全部是**校招公告里的具体岗位名**（管培生 / 培训生 / 专员 / 分析师 / 校招工程师），不是「运营优化」这类岗位族。
- 大模型可用时按学生自己的专业动态生成；不可用时走词表。
- `exclude` 支持「不满意，换一批」：前端把已展示岗位名传进来，重新生成时排除，已勾选的用 title 集合保留不丢。
- 实测两批之间只重复 1 个岗位。

---

## 五、简历解析

`POST /api/fair/resume`，`multipart/form-data`，字段名 `file`：

```bash
curl -X POST http://127.0.0.1:8102/api/fair/resume \
  -F "file=@resume.docx;filename=resume.docx"
```

返回：

```json
{
  "ok": true,
  "method": "rules",
  "fields": { "name": "张三", "major": "计算机科学与技术", "school": "南京大学",
              "degree": "本科", "phone": "138…", "email": "…", "grade": "2027届" },
  "internships": ["某公司 后端开发实习生"],
  "skills": ["Python", "Java", "MySQL"]
}
```

- 支持 `txt / md / html / docx`；docx 直接读 zip 里的 `word/document.xml`（不依赖 python-docx）；**pdf 如实提示不支持**，不假装解析。
- 正则抽 8 个字段 + 实习 / 竞赛 / 论文 / 技能 / 证书；`?use_llm=true` 时再走一次大模型补充。
- 5 份不同专业简历（计算机 txt / 金融工程 md / 生物医学工程 docx / 临床医学 html / 环境工程 txt）实测 5/5 成功，字段各不相同。
- 解析结果只用来**预填问卷**，学生可以逐项改；不会把解析值直接当成画像答案。

---

## 六、企业搜索的三层取数

现场学生可能自己带一份名单（Excel/文本），点「搜索」把名单变成企业档案。顺序是：

```
① 本地活库 company_jobs（含 v4.2 全量岗位，122 家 / 762 条）
      ↓ 库里没有，或库里有档案但没岗位
② 离线检索库 CACHE / LIB（companies.json 等）
      ↓ 仍然没有
③ 联网补（抓校招公告 / 企业官网），成功后写回库
```

`portal.py` 里有个关键修复：**「库里有档案但 `jobs=[]`」不能短路成未命中**，否则这家企业永远不会去联网补岗位——这正是「小号岗位是全的、demo 里找不到」的根因。

```python
# 之前：库里有档案但没岗位 → continue，直接记 missed，永远不联网
# 现在：记入 db_partial，继续进 todo 联网补；元数据用 LIB 或 db_partial 兜底
base = LIB.get(n) or db_partial.get(n)
```

搜索进度用三段小字提示（不再有「快搜索」按钮）：

```
正在从本地数据库读取…
正在查找企业档案与校招公告（37/118）…
正在写回本地缓存…
```

---

## 数据结构与 API

### 报告数据（`GET /api/fair/payload`）

顶层 9 键，前端全部依赖：

```json
{
  "version": "...",
  "generated_at": "2026-11-13 15:08:17",
  "profile": { "major": "...", "intended_jobs": ["..."], "home_city": "山东枣庄" },
  "weights": { "city": 100.0, "home": 0.0, "industry_status": 0.0 },
  "active_fair_id": "FAIR-XXXX",
  "province_map": { "上海": "上海" },
  "sources": [...],
  "stats": { "companies": 122, "jobs": 762,
             "states": { "hit": 13, "inferred": 80, "miss": 29 },
             "trust": { "high": 40, "medium": 11, "low": 71 } },
  "intent_semantics": { "供应链管理专员": ["采购计划员", "..."] },
  "rows": [ /* 122 条，见下 */ ]
}
```

单行（`rows[i]`）主要字段：

| 字段 | 说明 |
|---|---|
| `name` / `booth` / `city` / `province` / `nature` / `industry` | 企业基础信息 |
| `state` | `hit` / `inferred` / `miss` |
| `trust` | `high` / `medium` / `low`，数据可信度 |
| `score` / `dims` / `effective_weight` | 总分与逐维贡献（前端算） |
| `gaps` / `questions` / `verify_question` | 缺口与现场提问 |
| `evidence` | 证据链接（122/122 都有） |
| `jobs` / `job_count` / `job_titles` / `matched_jobs` | 岗位、企业真实岗位名 |
| `v42` | v4.2 档案：`rank_tier` / `growth_tier` / `segment` / `work_cities` / 4 列岗位来源 |

### 接口一览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/fair.html` | 应用页（构建产物） |
| GET | `/api/fair/health` | 健康检查（含当前生效画像 ID） |
| GET | `/api/fair/payload` | 报告数据（**画像变了自动重建**） |
| POST | `/api/fair/profile` | 写画像 + 重建报告 |
| POST | `/api/fair/rebuild` | 手动重建报告缓存 |
| POST | `/api/fair/recommend-jobs` | 校招岗位推荐（支持 `exclude`） |
| POST | `/api/fair/resume` | 简历解析 |
| POST | `/api/names` | 名单解析（原始文本 → 企业名数组） |
| POST | `/api/filter` | 按城市/行业/性质/规模筛选 |
| POST | `/api/lookup` | 企业搜索（三层取数 + 写回） |

---

## 诚实性设计

这个项目在「不知道」和「猜的」之间划了很硬的线：

| 机制 | 做法 |
|---|---|
| **三态而非二分** | 没查到岗位不等于不招：`inferred` 明说「按行业推断，请现场核实」，绝不排进「命中」 |
| **0 岗 ≠ 不招** | 库里 0 岗的企业照样进列表，标「未查到岗位」，不排除、不臆造 |
| **可信度分级** | `trust` 分三档，报告可一键「只看高可信」 |
| **证据可点** | 每家都有证据 URL（122/122），拒绝靠名字猜 |
| **中性地板分** | 没榜单 / 没成长信号的企业给 0.6 / 0.55，而不是 0——避免系统性压低 62 家没查到榜单的公司 |
| **展示与打分分离** | v4.2 新增字段（行业地位、成长性、细分、多榜）只展示，改不动的老字段（规模档、行业）不被反向覆盖 |
| **现场反馈优先** | 学生现场确认「确实招这个岗」，`state` 从 `inferred` 升 `hit`、`trust` 升 `high`，覆盖模型推断 |
| **敏感岗位打码** | 部分岗位名脱敏（当前 180 条），避免公开页面出现不宜展示的信息 |

---

## 重建与维护

改完数据或规则后，报告不会自己变——**必须重建 `fair.html` / 让 payload 重新生成**。

```bash
# 1) 改了前端/规则/报告逻辑 → 重建单文件页面
python -X utf8 ent_tools/build_fair_page.py

# 2) 改了库里数据或画像 → 重建报告数据（也可直接 GET，服务端会检测画像变化）
curl -X POST http://127.0.0.1:8102/api/fair/rebuild

# 3) 改了 fair_server.py / fair_bridge.py / rules.py → 必须重启服务
#    （Python 进程不会热加载，重启前一直在用内存里的旧规则）
```

三个踩过的坑，写在这里省下下次：

1. **`/api/fair/payload` 是缓存文件，不是实时算的**。服务端原来只在文件缺失时重建，所以改了逻辑却一直看到旧数值；现在每次 GET 都会比对 `active_fair_id`，不一致自动重建。
2. **改了 `rules.py` 只 rebuild 不重启 = 白改**。进程内存里还是旧维度，写出来的缓存会缺维度。
3. **`fair.html` 是构建产物，不是源码**。改 `fair_report_js.py` 或 `build_fair_page.py` 之后不重新 build，浏览器怎么刷新都不会变。

校验脚本（构建后自检）：

```bash
python -X utf8 ent_tools/build_fair_page.py    # 末尾打印：内置企业 122 家 / 名单 122 家
node --check <(sed -n '/<script>/,/<\/script>/p' demo/fair.html)   # 或直接看构建是否报错
```

---

## 数据口径

| 项 | 口径 | 说明 |
|---|---|---|
| `industry` | **业务/招聘口径** | 按企业主营与招聘方向标，不抄工商登记门类（否则华为会被标成「制造」） |
| `city` | 主工作城市（单值） | 仅用于展示 |
| `work_cities` | 全部工作城市（列表） | 打分用这个，取每维最优 |
| `scale_tier` | 客观分档 | 由性质 + 是否上市/集团 + 行业判定，不问用户 |
| `rank_tier` | 第三方榜单 | 世界500强 / 中国500强 / 省100强 / 全国行业龙头 / 细分领先 |
| `growth_tier` | 成长证据 | `rising_star`（有明确证据）/ `note_only`（弱证据）/ 无 |
| `nature` | 企业性质 | 值域归一：民办非企业/合伙制/合资 → 民企，党政机关 → 事业单位；原值保留在 `v42.raw` |

企业名以 `company_jobs.name` **全称**为准，搜索与匹配都先做去空格/简称归一。

---

## 已知限制

- **演示数据规模**：122 家企业、762 条岗位，来自一次真实双选会名单；不是全量招聘市场数据。
- **5 家企业 0 岗**：山西省人民医院、中共黔西南州委组织部、上海杉泰私募、新航道（北京）、上海芒啾网络。v4.2 里也没查到岗位，属「未查到」，没有臆造。
- **榜单覆盖不完整**：62 家没有公开榜单记录，走中性地板分；这不是「它们不优秀」，只是没查到。
- **大模型是可选件**：不配 key 时语义匹配退化为字面匹配，命中率会变低但仍可用。
- **`fair.html` 体积 1.3 MB**：因为内联了全部数据与样式，换来「双击即可离线演示」。
- **PDF 简历不支持**：解析器如实报错，不静默返回空字段。

---

## 技术选型

| 选择 | 理由 |
|---|---|
| **纯标准库起服务**（`http.server`） | 现场演示零安装：Python 装好就能跑，不怕 `pip install` 失败 / 断网 |
| **单文件 `fair.html`** | 双击即用，不依赖服务；构建时把数据、样式、逻辑全内联 |
| **打分规则只有一份**（`rules.py` → 序列化成前端 `RULES`） | 避免「服务端算的和页面显示的不一样」这类最恶心的一致性 bug |
| **分数可复算**（加权和 + 逐维 `basis`） | 用户能看懂为什么这家排第一，不服可以自己算 |
| **大模型只做语义与措辞** | 排序要可解释、可复现；模型抖动不能让排名跳 |
| **SQLite 单文件库** | 现场拷一个文件就能换数据，不需要起数据库服务 |
| **中性地板分** | 数据缺失是「不知道」，不能惩罚成「差」 |

---

## 许可与致谢

演示项目，数据来自公开校招公告与企业官网，仅用于产品演示。企业档案中的判断（是否对口、是否推荐）均为辅助参考，**现场以企业实际招聘信息为准**。
