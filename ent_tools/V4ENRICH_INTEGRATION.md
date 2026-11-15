# 145 家 v4 增补档案接入说明（2026-11-15 02:0x）

数据来源：`C:\Users\Lenovo\Desktop\给主模型的交付_20261114\深夜交接包\`
（`fair2026_enrich.db` 145 行 / 51 列 / evidence 1699 条，生成 `v4-enrich-20261115`）

本文只记录**我怎么接的、改了哪些口径、影响多大、怎么回滚**。采集侧的取舍看对方交接说明与验收报告。

---

## 1. 接入位置

| 环节 | 文件 | 改动 |
|---|---|---|
| 数据副本 | `ent_tools/data/fair2026_enrich.db` | 从交接包原样复制，未改一行 |
| 读取 | `fair_bridge.load_enrich()` | 新函数；JSON 列反序列化、`segment_leader_url/conf` 对齐既有字段名、性质归一三桶 |
| 合并 | `fair_bridge.build()` | **增补档案优先于 v4.2**（同名单 145/145 逐字同名；v4.2 只与参会名单重叠 22 家），缺失才回退 |
| 打分 | `fair_bridge.score_parts()` | 两维读增补档位；非企业主体跳过两维（pool 归一） |
| 规则 | `demo_v2/rules.py` | `INDUSTRY_STATUS_TIERS` 增加合并档 `global_leader = 1.0` |
| 前端 | `fair_report_js.py` | `calcOne` 同口径；报告块改成「145 家增补档案」并标明两维参与打分 |

## 2. 口径决定（重要）

**① `industry` 不整表覆盖。**
增补档案走工商门类口径（风电/光伏 → 制造、地产 → 建筑），本项目 `industry` 是招聘/业务口径。
整表覆盖会重演 11-13 那次的「华为 IT 被判成制造」「能源企业全变制造」的系统性错判。
处理：只补原值为空/未知的 17 家，其余保留原值；档案口径存 `industry_raw`，差异写进
`enrich_diff["industry(工商口径,仅展示)"]`，页面上标「仅展示」。

**② 其余打分字段照单覆盖**：`city` / `nature` / `scale_tier` / `work_cities`。
差异家数：nature 40、scale_tier 68、city 21 —— 绝大多数是把「未知」补成实测值，
少数是真修正（国泰海通 民企→国企、金田铜业 国企→民企、海纳川 民企→国企、
天山铝业 石河子→上海总部、爱旭 杭州→义乌基地）。被覆盖的旧值逐条留在 `enrich_diff`。

**③ 非企业主体 14 家（本页）不参与榜单两维。**
学校/党政/事业单位 `industry_rank_label=不适用`、`rank_checked=0`。按交接说明：
既不能当「普通公司」压低分，也不能当 0 分参与行业地位排序 → 直接跳过该维，
`pool` 不含其权重，按其余维度归一（这些行 `effective_weight` 落在 55~85）。

**④ 成长性 `unknown`（本页 16 家）记 0，文案写「未查到，不臆造」**，与 `none` 区分但不给分。

## 3. 覆盖变化（本页 86 行）

| 指标 | 接入前 | 接入后 |
|---|---|---|
| 数据源 | v4.2（22 家重叠，榜上 6 家有档案） | 增补档案（86/86 有档案） |
| 行业地位有档 | 1 家 | **35 家**（global_leader 1 / national_leader 9 / niche 25） |
| 成长性有信号 | 0 家 | **29 家**（rising_star 6 / note_only 23） |
| 成长性未查到 | — | 16 家（记 0） |
| 非企业主体 | 未区分 | 14 家（两维不参与） |
| 分数区间 | 21.2 ~ 77.5 | **34.0 ~ 89.4** |
| 名次变动 | — | 85/86 有变动（唯一没变的是本来就满分的） |

名次大幅变动的主因不是榜单，而是 `scale_tier`：68 家原来「未知」→ 该维不参与、pool 变小、
其余维度被放大；现在补齐后按 100 分池计算，分数可比性反而更强。

## 4. 已知问题（未擅自改，先标出来）

**成长档位与证据自相矛盾 4 家**（本页 3 家）：

| 企业 | 名次/分数 | 问题 |
|---|---|---|
| 南京牧镭激光 | #3 / 82.8 | `rising_star`，`growth_hits=[]`，备注「未检索到第三方增速或行业榜单名次」 |
| 上海非凸智能 | #5 / 79.1 | `rising_star`，`growth_hits=[]`，备注为空 |
| 银河航天（南通） | #69 / 59.3 | `rising_star`，`growth_hits=[]`，备注「未检索到符合白名单的成长类榜单」 |
| 上海柏楚（未在榜） | — | `rising_star`，备注「仅见…未采」 |

处置：**未改档**，payload 打 `growth_flag`，报告里用红字标「证据不足，待核」。
要不要降 `note_only`(0.7) 或 `unknown`(0)，等用户拍板。
牧镭、非凸都在前五，5 分权重直接影响名次，不能默认。

其他：
- `segment_leader=third_party_top` 49 家里 19 家 confidence=low（来源只是企查查/招聘站索引），
  页面按交接说明只作描述，不当硬标签。
- 榜单年份有 2021 的老榜（`rank_hits[].year`），展示时按年份自行降权，本次不额外折算。

## 5. 验证

- `refresh_all.py` 全过：缓存体检 / payload / 页面 / 内联 JS 5 块 / match 0 失败 /
  parity 10248 对 100% / rule_regress 0 退化
- 前后端分数：用页面 `calcOne`（`_rank_run.js`）复算 86 行，与 payload **0 处不一致**
  （顺带修了 Python `round()` 银行家舍入与 JS `toFixed()` 差 0.1 的老问题，新增 `_round()`）
- 8102 在线 payload 与页面 200

## 6. 回滚

```bash
cp _bak_20261115_020003/{fair_bridge.py,fair_report_js.py,build_fair_page.py} ./
cp _bak_20261115_020003/rules.py demo_v2/
cp _bak_20261115_020003/fair_payload.json.bak data/fair_payload.json
# 再彻底一点：删掉 data/fair2026_enrich.db 即自动回退到 v4.2 链路（load_enrich 缺失时静默跳过）
python refresh_all.py
```
