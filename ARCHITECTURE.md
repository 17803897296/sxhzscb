# 架构与部署约定

> 这份文档管的是「**哪份文件是准据、改完怎么发布、能起几个服务**」。
> 业务口径（打分、语义、数据）见 README；踩坑记录见 README 的「经验教训」。
> 2026-11-15 定，起因是同一天三次事故：测试记录写进另一个实例的库、
> 同端口双进程导致「改了代码不生效」、克隆验收页是旧副本。

---

## 1. 三端架构（不变）

```
采集端 demo/fair.html        问卷 / 简历解析 → 画像 JSON
        │  POST /api/fair/profile
        ▼
服务端 ent_tools/fair_server.py   画像落 SQLite + 重建 fair_payload.json
        │  GET /api/fair/payload（服务端每次重读磁盘，不缓存）
        ▼
匹配端 demo/fair.html?report=1    7 维打分 → 排行 + 单家完整报告
```

`ent_tools/data/fair_payload.json` 是**唯一数据总线**：企业、岗位、画像、语义映射、
证据全在里面。改数据后只需重建 payload，页面不用重新内联。

前端同源取数（`SEM_BASE`）：http(s) 页面一律请求**自己所在端口**，只有 file://
离线打开才回退 8102。这是 8103 上「点结束使用，记录却写进 8102」的修复。

---

## 2. 唯一正本与三种目录（最容易乱的地方）

| 目录 | 角色 | 能不能直接改 | 要不要常驻服务 |
| --- | --- | --- | --- |
| `C:/Users/Lenovo/Documents/deepseek-harness/default-workspace` | **唯一开发正本**：改代码、跑采集、存活库 | ✅ 只改这里 | ✅ **8102**（开发正本服务） |
| 同目录下的 `careersafari-fair/` | **发布仓库工作副本**：GitHub 上的内容 | ❌ 由 `sync_repo.py` 写入 | ❌ |
| `C:/Users/Lenovo/Documents/deepseek-harness/sxhzscb-deploy` | **克隆验收实例**：验证「clone 下来能不能跑」 | ❌ 只读，靠 `git pull` 更新，**绝不复制文件进去** | ✅ 可常驻 8103，但内容必须等于远端 main |

**规矩**：
1. 只改开发正本；发布仓库和克隆目录里的文件都是**产出物**，手改必被覆盖。
2. 两个实例可以并行（8102 开发正本 / 8103 干净克隆），但必须满足三条：
   **端口不同**（`fair_server.py` 会拒绝同端口第二实例）、**内容同源**（8103 只靠
   `git pull` 更新，绝不 `cp`）、**记录各看各的**（页面已同源取数，在哪个端口测
   就去哪个端口的「④ 测试数据」看）。
3. 发布节奏：`sync_repo.py --apply` → 提交推送 → `git -C sxhzscb-deploy pull`，
   三步做完两端内容一致，测哪个都一样。推送前两端暂不一致是正常的，别在那时对比。

---

## 3. 哪些是源文件，哪些是构建产物

| 你以为要改的 | 实际要改的 |
| --- | --- |
| `demo/fair.html`（30k+ 行，内联 JS） | `ent_tools/fair_report_js.py`（报告 JS）、`ent_tools/build_fair_page.py`（采集 JS / 页面骨架 / 垫片）、`ent_tools/job_recommend.py`（岗位推荐词表）、`ent_tools/intent_semantics.py`（语义规则） |
| `demo/questionnaire.json` | `ent_tools/extract_questionnaire.py`（问卷常量） |
| `demo/test_users.html` | `ent_tools/build_test_users_page.py` |
| `demo/portal.html` | `ent_tools/build_portal.py` |

`demo/fair.html` 是**生成物，禁止直接编辑**。重建链路：

```bash
cd default-workspace
python -X utf8 ent_tools/extract_questionnaire.py   # 问卷常量 → demo/questionnaire.json
python -X utf8 ent_tools/build_fair_page.py        # → demo/fair.html
python -X utf8 ent_tools/refresh_all.py            # 改了规则/词表必跑：体检+payload+页面+JS校验+三套测试
```

### 规则双实现（当前的技术债，改动时务必同步）

判定逻辑在 Python（服务端/重建）和 JS（页面）各有一份镜像，
`refresh_all.py` 的 parity 测试会抓不一致：

| Python | JS 镜像 |
| --- | --- |
| `intent_semantics.py` 的 `DIR_WORDS` / `DIR_PARENTS` / `pair_verdict` | `fair_report_js.py` 的 `MI_DIRWORDS` / `MI_PARENTS` / `miPairVerdict` |
| `job_level.py` 五档层级 | `fair_report_js.py` 的 `miLevel*` |
| `job_recommend.py` 的 `MAJOR_BASE` 专业词表 | `build_fair_page.py` 的 `RECOMMEND_JS.MAJOR` |
| `job_recommend.py` 的 `CATEGORY_JOBS_MAP` 大类分表 | 前端无镜像（走接口，不用同步） |

长期方向：Python 生成 JSON 规则表、JS 直接读，消掉镜像税。**在此之前，
任何一边改词表都要跑 `refresh_all.py`。**

---

## 4. 服务：单实例纪律

`fair_server.py` 启动时会主动 `connect` 探测端口：已有服务在响应就直接退出并打印
处理办法。原因是 Windows 的 `SO_REUSEADDR` 允许两个进程同时 LISTENING 同一端口，
光靠 bind 不报错发现不了——请求会随机打到旧进程，表现成「改了代码不生效」，
而 `code_fp()` 每次现读磁盘又会给出新指纹，连缓存体检都查不出来。

```bash
# 启动开发正本（8102）
cd default-workspace/ent_tools
python -X utf8 fair_server.py --port 8102 --host 127.0.0.1

# 查有没有残留旧进程
netstat -ano | findstr :8102          # Windows
# 停掉：taskkill /F /PID <PID>

# 验收实例（8103）：先 pull 到与远端一致，再启动；不要往这个目录复制文件
cd sxhzscb-deploy && git pull
cd ent_tools
python -X utf8 fair_server.py --port 8103 --host 127.0.0.1
```

启动被拒绝时**不要加参数绕过**，先杀旧进程。要并行请换端口，页面会自动同源取数。

---

## 5. 缓存卫生

所有缓存 key 必须含依赖模块指纹，统一登记在 `ent_tools/cache_guard.py` 的
`REGISTRY`。新缓存没登记会被 `cache_guard.py status` 报成旧格式；
失配缓存自动归档到 `data/_stale_cache/`，不静默沿用。
细节见 `ent_tools/CACHE_HYGIENE.md`。

改了逻辑就跑 `refresh_all.py`（体检 → 重建 payload → 重建页面 → JS 校验 → 三套测试），
失败即停。**不再靠人记得删缓存。**

---

## 6. 发布流程（取代手写 cp）

`ent_tools/sync_repo.py` 是唯一同步入口，带白名单，**默认只写发布仓库**：

```bash
cd default-workspace
python -X utf8 ent_tools/sync_repo.py --check     # 干跑：列出所有不一致，不写文件
python -X utf8 ent_tools/sync_repo.py --apply     # 重建问卷+页面 → 同步到发布仓库
```

克隆验收目录**不参与文件同步**——它必须是 GitHub 内容的干净副本，
否则「clone 下来能不能跑」就验了个假的。要更新它只能：

```bash
cd sxhzscb-deploy && git pull        # 或干脆删掉重新 git clone
```

> **运行时文件一律不入库，仓库只放只读种子（`*.seed`）**：活库 `careersafari.db`
> 和 `data/` 下的缓存共用同一套做法——服务/构建启动时缺哪个就从对应 `.seed`
> 复制出活文件，活文件本身被 `.gitignore` 排除。所以跑完服务 `git status`
> 仍然是干净的，不需要再靠 `git checkout -- ent_tools/data` 手工撤掉刚算出的结论。
>
> 种子必须与代码同版本：里面的 `_cg.fp` 是「生成这份结论的代码」的指纹，
> 代码一变就不符，clone 后第一次构建会自动判失效重算（这是对的，不沿用旧结论）。
> 改了判定逻辑想让种子跟上，发布前冻结一次：
>
> ```bash
> cd ent_tools
> python -X utf8 cache_guard.py seeds     # 看活文件/种子是否配对
> python -X utf8 cache_guard.py freeze    # 把当前结论冻结成 *.seed
> ```

- 自动同步：`ent_tools/*.py`（排除 `_*.py` 调试脚本）、`ent_tools/*.md`、
  `.env.example`、`data/test_users/*.json`、`demo/*.js`、`questionnaire.json`、
  **7 个白名单 HTML**、**8 个测试资产**（`_test_match.js` / `_test_parity.js` /
  `_rule_regress.py` / `_schema_consts.json` / `_js_matchintent.js` /
  `data/_parity.json` / `_is_old_tmp.py` / `data/_v5_verify.json`；
  夹具和旧规则基线缺任一，clone 直接跑门禁就会报 FileNotFoundError）、
  **`data/fair_payload.json`**（默认带上，发布后两端报告数据同源）。
- payload **发布前必过隐私自检**：画像里出现姓名 / 学校 / 电话 / 邮箱（或疑似
  手机号、邮箱串）就跳过该文件并明确提示，绝不静默推到公开仓库。
  确需不带它用 `--no-payload`。
- **永不自动同步**：`.env`（密钥）、`careersafari.db`（运行时活库，
  仓库只放 `.seed` 只读种子）、一次性 `_*` 调试脚本、含真实姓名的个人报告页
  （`排行_*` / `ranking_*` / `采集数据_*`）。
- 文本比对按 LF 归一（git 的 CRLF/LF 转换不算差异），二进制按原哈希；
  验证「两端同源」时也要先归一化行尾再比哈希，否则会误报。
- 脚本**不自动 commit / push**。同步完人工确认 `git status` 再提交推送。
- 仓库里的 `careersafari.db.seed` 是**只读初始种子**（已清空测试记录），
  `fair_server.py` 首次启动时复制成 `data/careersafari.db`。活库不入库，
  git pull / stash / checkout 不会再替换一个正在被 SQLite 写入的文件——
  2026-11-15 实测那样做会得到 `database disk image is malformed`。
  `sync_repo.py --apply` 每次发布都会把**正本当前生效画像**写进种子库
  （identity 脱敏、历史画像清空）：因为 `/api/fair/payload` 有安全网，
  payload 的 `active_fair_id` 与本地库生效画像不一致就会立刻重建并覆盖发布快照。
  两端数据要同源，payload 与种子的生效画像必须配套。

改完一轮的推荐顺序：

```
改源文件 → refresh_all.py（若改了规则） → sync_repo.py --check → --apply
→ git commit / push → 克隆实例 git pull → 两端比对页面与 payload 哈希
```

---

## 7. 一句话记住

**一个正本、一份构建产物、一个同步脚本、一次 parity 门禁；活库不入库；
多实例可以，但必须端口不同且内容同源（副本只靠 git 更新）。**
其余目录都是副本，改动只发生在正本，发布只走脚本。
