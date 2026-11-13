# 企业资料采集 · 大模型版

企业名进，`companies.json` 出。企业端离线模块（`company_module.py`、测试和
Windows x64 Python 运行时）已经并入本目录，不再需要复制到另一个交付包。

```
data/company_names.json          企业名单
        ↓  enrich_by_llm.py      搜页面 + 大模型抽取
data/research_cache.json         企业端输入（含 text，用于证据核对）
        ↓  build_cache.py        归一枚举、补缺失、校验证据
data/companies.json              企业端锁定契约（10 个企业字段 / 6 个岗位字段）
        ↓  portal.py             有岗位的企业离线直接命中
demo/fair.html                   双选会匹配页
```

## 怎么跑

**1. 填密钥**

在 `ent_tools\.env` 里写一行（从 `.env.example` 复制改名）：

```
DEEPSEEK_API_KEY=sk-你的密钥
DEEPSEEK_MODEL=deepseek-chat
```

**2. 双击 `跑全流程.cmd`**

它做四件事：
- `enrich_by_llm.py`：搜页面、调模型、出 `research_cache.json`
- `build_cache.py`：归一枚举、补缺失、校验证据
- `company_module.py`：按锁定契约生成 `data/companies.json`
- `sync_lookup_to_companies.py`：把在线 `lookup_cache.json` 里已抓到的岗位合并进 `companies.json`

**3. 启动演示服务**

直接双击 `启动演示服务.cmd`，它会启动 8099 并打开 `fair.html`。  
关闭那个黑窗口就会停止服务。

也可以命令行启动：

```powershell
cd ent_tools
python -X utf8 portal.py --port 8099
```

`portal.py` 启动时会读取 `data/companies.json`；有岗位的企业直接离线命中，
没有岗位的企业仍走联网补全。`companies.json` 更新后无需重启，按文件修改时间自动重载。

每次联网 lookup 的结果还会写入 `data/lookup_cache.json`。重启服务后，已经抓到的岗位和
“没抓到”的状态都会直接复用，不会因为重启又变回 0 个岗位。

没抓到岗位的企业不会再从匹配结果里消失：快速搜索和全量搜索都会把它们保留在匹配池，
标为“岗位未查到”，单独放在「资料不全」分组，不占「优先去 / 可以看 / 建议放弃」的名额。
「资料不全」的企业可以点“补岗位”，粘贴招聘页链接或岗位文本，系统会抽岗位并写回缓存。

匹配结果不再只显示分数：`fair.html` 和 `index.html` 的每家都会列出城市、性质、行业、岗位数和来源，
并写明「命中了什么 / 没中什么」（城市、方向、紧急度、六项偏好、企业类型、家乡），
再展开「分数怎么算」（基础分、偏好系数、加分、每条匹配度的依据）和岗位原文。

## 单独重跑企业端

```powershell
# 双击，或命令行
启动企业端.cmd --no-pause

# 用附带的运行时跑契约测试
.runtime\python\python.exe -X utf8 -m unittest discover -s tests -v

# 只校验已有结果
.runtime\python\python.exe -X utf8 company_module.py --validate data/companies.json
```

企业端的完整字段、枚举和证据规则见 `企业端_README.md`。

## 这个脚本保证什么

| 保证 | 做法 |
|---|---|
| **不编造** | `evidence` 必须是原文逐字片段，对不上就整条降级成「未知」 |
| **原文可信** | `text` 由脚本从网页截取，**不由模型生成** |
| **匹配端能读** | 岗位要求自动拼成 `职责：…；工作地点：…；招聘阶段：…；要求：…` |
| **缺值合规** | 一律写 `"未知"`，不写 null |

「职责 / 工作地点 / 招聘阶段」这三项是**匹配端算分的必要条件**——没有它们，`score` 永远是「未知」。

## 抓取策略

每家企业两条查询：

```
"企业全称" 校园招聘   → 招聘页（要岗位、职责、地点、阶段）
"企业全称"            → 官网/百科（要性质、城市、行业）
```

招聘页有准入检查：正文里至少命中 2 个 `职责/岗位名称/专业/学历/招聘人数/工作地点/任职要求` 才算数，否则换下一个候选。

`portal.py` 的在线补全还会做三件事：

- 用「全称 / 短名 / 去掉行业词的品牌名」分别搜 `校园招聘 / 招聘 / 人才招聘`；
- 优先学校就业网、招聘站点，并把正文窗口挪到「招聘岗位 / 人才招聘」附近；
- 页面有真实岗位但没写“校招/社招”时，不再直接丢掉岗位，而是按「社招信息」保留。

仍抓不到的（腾讯、京东方这类招聘页是 JS 渲染），在页面「资料不全」分组点“补岗位”，
粘贴招聘页链接或岗位文本即可补录，结果会写进 `data/lookup_cache.json`。

**JS 页面能抓。** 补岗位贴 URL 时，普通 HTTP 拿不到正文会自动调用本机 Edge/Chrome
无头模式（`--headless=new --dump-dom`）渲染后再抽岗位。实测：

- 腾讯 `join.qq.com/post.html`：能渲染出“共 1001 个岗位”和岗位类别
- 京东方 `career.boe.com/professionals`：5 个社招岗位
- 国元证券 `gyzq.zhiye.com/jobs`：85 个岗位的列表页

全量搜索默认不开无头浏览器（43 家会跑很久）；要自动渲染可以设环境变量
`FAIR_JS_RENDER=1` 再启动 `portal.py`。

BOSS 直聘试点：用本机 Edge 开 `--remote-debugging-port=9222`，复用已登录会话，
低频查企业岗位。已补东方财富 15 条、腾讯 15 条；京东方北京搜到的主要是车间/质检蓝领岗，未写入。
BOSS 薪资数字有字体混淆，当前 `salary` 写“未知”。

## 无登录公司岗位 MCP

`company_jobs_mcp.py` 是一个不登录、不带 Cookie 的本地 MCP：

```text
search_company_jobs(company, city, limit)
```

来源：企业官网、学校就业网、公开招聘页；JS 页面用无头 Edge 渲染。
项目根目录的 `opencode.json` 已配置为 MCP server `company-jobs`。

实测：

```text
国元证券股份有限公司
→ 1 个岗位：财富管理管培生
→ source: https://gyzq.zhiye.com/jobs
```

它不碰 BOSS 账号，也不适合替代 BOSS 全量数据；适合作为官网/学校就业网的统一入口。

## 数据库优先链条

用户搜索企业时现在是：

```text
JobStore.lookup(company)
→ 命中：直接返回，data_source=db
→ 未命中：portal.build(company) 在线搜索
→ JobStore.save(company, item) 写回（可空实现）
```

接入点：

```text
ent_tools/job_store.py     JobStore.lookup / save 接口
ent_tools/job_lookup.py    数据库优先、在线兜底的查询链
```

默认是 `NullJobStore`：不读不写，全部走在线。组员实现自己的 `JobStore`
后调用 `job_store.set_store(MyStore())` 即可接 SQLite/Postgres。

完整说明见 `数据库接入说明.md`；链条测试：

```powershell
cd ent_tools
.runtime\python\python.exe -X utf8 -m unittest tests.test_job_lookup_chain -v
```

## 实测

| 企业 | 抓到 | 结果 |
|---|---|---|
| 亿嘉和 | 校招表单页 5518 字 | ✅ 岗位职责完整 |
| 郑州宇通 | 国家大学生就业平台 2937 字 | ✅ 有岗位 |
| 招商证券 | 官网首页 1325 字 | ⚠️ 无岗位，降级为「未知」 |
| 东方电机 | 官网首页 1509 字 | ⚠️ 无岗位，降级为「未知」 |

**覆盖率大约五成。** 抓不到的不会编，直接在 `data/research_report.md` 里列出来，可以手工补。

## 报告

跑完看 `data/research_report.md`：

```
- 企业数：118
- 拿到岗位：61
- 有来源：98
- 失败：5
- 明细：某某公司：无岗位（未知）
```

「无岗位」的那些，是搜不到校招公告的，可以人工补一条进 `raw_research.json` 再重跑。

## 常用参数

```bash
python -X utf8 enrich_by_llm.py --limit 10      # 先试 10 家
python -X utf8 enrich_by_llm.py --sleep 1        # 放慢，避免被搜索限流
```

## 注意

- 需要联网，**只在这一步**。生成出来的 `companies.json` 是离线可用的。
- 密钥不要发给别人，`.env` 不要进压缩包。
- 搜索有频率限制，跑 118 家大约 5–8 分钟。
