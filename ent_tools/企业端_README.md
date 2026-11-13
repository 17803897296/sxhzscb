# 双选会优先级助手：企业端离线模块

> 已并入 `ent_tools`。直接双击 `跑全流程.cmd` 就会生成 `data/companies.json`；
> 也可以单独双击 `启动企业端.cmd`。下文是模块自己的完整说明。

本模块读取企业名字和预先保存的公开资料，生成 `data/companies.json`。字段以本次锁定契约为准，替代此前粗筛文档中的三字段输出。优先级打分由匹配模块负责。

## 双击运行

双击 `启动企业端.cmd`。无需联网，无需安装第三方库，也无需系统安装 Python；项目已附带 Windows x64 Python 3.12 标准运行时。

分享时请一起复制 `company_module.py`、`启动企业端.cmd`、`data/` 和 `.runtime/`。不要只发送启动文件。带点的 `.runtime` 文件夹也必须包含。

初始名单包含前文提供的三家企业名称，缓存初始为空。因此示例生成的资料字段均为“未知”、`jobs` 为 `[]`。这是缺失数据的演示，没有把示例岗位冒充真实招聘信息。

## 输入文件

- `data/company_names.json`：企业名称字符串数组，只接收企业名。
- `data/research_cache.json`：外部查询已预先保存的 JSON 数组；运行程序时只读此文件，不查询外部服务。

缓存按 `name` 与名单做精确匹配。未匹配到的企业仍会生成完整对象，资料填“未知”。名单中的完全相同名称合并一次；名称前后空白会清理，不做模糊合并。企业 `id` 由名称稳定生成，调整顺序不会改变 ID。

本模块只处理企业名；如后续需要保留一企多展位，展位信息在上游名单中另行维护，不向锁定企业对象添加字段。

### 缓存格式

缓存项支持以下字段：`name nature city industry jobs jobSource jobEvidence source date text`。缓存不是新增对外数据契约，`text` 仅用于本地核对来源原文，输出时会移除；未查到的文本请填“未知”。资料字段也可省略，由程序补全；`jobs` 无资料时写 `[]`。

最小缓存项示例（“企业全称”为占位文字，使用前替换）：

```json
[
  {
    "name": "企业全称",
    "nature": "未知",
    "city": "未知",
    "industry": "未知",
    "jobs": [],
    "jobSource": "未知",
    "jobEvidence": "未知",
    "source": "未知",
    "date": "未知",
    "text": "未知"
  }
]
```

- 公开招聘岗位：按材料选择“校招公告”或“社招信息”，岗位名称与要求按原文整理。不要因为学生的自述意向修改招聘要求。
- 推测岗位：明确选择“主营业务推测”。`text` 保存公开业务材料原文，`jobEvidence` 必须是其中逐字存在的非空片段，`source` 填对应公开材料链接或可定位标识。程序只验证引用关系，不自动编造岗位，也不能证明业务材料足以支持某岗位；推测内容仍需整理者检查。
- 每家企业只有一个 `jobSource`，同一缓存项不要混合真实招聘与推测岗位。优先保留明确的校招信息；其余来源单独留存在上游资料中。
- 一份缓存中的同名记录或未约定字段会报错，避免静默覆盖和字段拼写错误。
- `date` 指资料核对日期，格式为 `YYYY-MM-DD`；未知时写“未知”，不伪造资料的新鲜程度。

## 输出契约

`data/companies.json` 是数组，每项严格只有以下 10 个字段：

`id name nature city industry jobs jobSource jobEvidence source date`

`jobs` 每项严格只有以下 6 个字段：

`title requirements headcount urgency group salary`

类型约定：

- `jobs` 为数组；其他企业字段均为字符串。
- 岗位中 `title requirements urgency group salary` 为字符串；`headcount` 为非负整数或字符串“未知”。
- 原契约未指定 `group` 的类型，本模块统一保留原材料中的岗位分组文字，未知时写“未知”。
- 无 `null`、空字符串或缺失 key。没有岗位名时不创建一条 title 为“未知”的占位岗位。
- `jobSource` 为“未知”时 `jobs` 必须为 `[]`。缓存存在矛盾时直接报错，不悄悄丢弃岗位。
- 只有全部企业通过校验后才原子替换输出文件；出现错误不会覆盖已有结果。

## 枚举

| 字段 | 允许值 |
| --- | --- |
| nature | 央企、国企、民企、外企、合资、事业单位；资料缺失时为未知 |
| industry | 制造、医药、IT、金融、建筑、能源、消费、物流、教育、政府事业、其他；资料缺失时为未知 |
| urgency | 高、中、低、未知 |
| jobSource | 校招公告、社招信息、主营业务推测、未知 |

`nature`、`industry` 的缺失哨兵“未知”已获用户确认。程序不擅自将“民营企业”改成“民企”等值；请在缓存整理时使用规定枚举，错误值会被报告。

## 与其他模块的边界

本模块不修改或生成 `profile.json`、`results.json`。不读取学生偏好来排除企业，不计算距离，不生成优先级分数。名单中所有不同企业名都会得到输出。

匹配模块接入时仍必须遵守：

1. `values` 缺失或六项全零时按等权处理，不因未收集而降分；已收集时六项为整数且合计 100。
2. `target_company_types` 命中加分，未命中不排除。
3. `direction_interest` 是学生自述意向，只作加分参考，不是岗位要求或过滤条件。
4. 距离只作加分，不据此排除企业。

不得在下游把企业端的“未知”转换成“不适合”或“建议放弃”。

## 命令行与校验

在项目目录下执行（全部路径相对项目目录）：

```text
.runtime\python\python.exe -X utf8 company_module.py
.runtime\python\python.exe -X utf8 company_module.py --validate data/companies.json
.runtime\python\python.exe -X utf8 -m unittest discover -s tests -v
```

可用 `--names`、`--cache`、`--output` 指定不同文件，但都必须位于相对 `data/` 目录内。拒绝绝对路径、目录穿越以及输出覆盖输入。

测试数据只在测试中构造，不作为真实企业事实写入演示结果；测试覆盖缺失值、枚举、精确字段、推测证据、稳定 ID、失败时保留旧结果，并禁止 socket 创建验证生成链路。
