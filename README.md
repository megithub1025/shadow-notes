# 影子笔记 · Shadow Notes

**源文件一律不动，只在 Obsidian（或任意文件夹）里生成一批同名 `.md` 分身。**
每篇分身回答四个问题：**在哪**（相对路径）、**是什么**（MD5 指纹）、**讲了什么**（本地摘录）、**跟谁重**（同内容副本 / 同族版本）。

零第三方依赖、不联网、不改源文件。Windows 和 macOS 都能跑。

---

## 它解决什么问题

当你的资料夹里同时躺着这些东西：

```
01_技改/网络安全改造合同.docx
01_技改/网络安全改造合同 - 副本.docx
01_技改/网络安全改造合同 v2.docx
01_技改/网络安全改造合同（盖章扫描件）.pdf
```

系统搜索（Spotlight / Everything / Windows 搜索）只会把它们全部列出来，**判断不了哪份是终稿**。
影子笔记把版本关系直接写进卡片：谁是主文件、谁是谁的副本、同族里按修改时间怎么排。

为什么不用系统搜索替代：

| | 影子笔记 | 系统搜索 |
|---|---|---|
| 跨平台 | Markdown + Obsidian，Windows / macOS / 手机都能开 | 各平台各一套，不通用 |
| 辨别版本 | 卡片里写明副本、版本标记、族关系 | 只能全文搜，结果平铺 |
| 目录层级 | 完整镜像源目录结构 | 结果是一张平铺清单 |

## 快速开始

前置：Python 3.8+（Windows 装 python.org 版并勾选 Add to PATH；macOS 自带）。

```bash
# 1) 初始化：指定源目录和 Vault 目录
python scripts/shadow_notes.py init --source "D:/工作资料" --vault "D:/Obsidian/工作资料索引"

# 2) 扫描：算 MD5、找同内容副本、分版本族（不写笔记）
python scripts/shadow_notes.py --config "D:/Obsidian/工作资料索引/shadow-notes.json" scan

# 3) 同步：生成/更新影子笔记（先加 --dry-run 看一眼）
python scripts/shadow_notes.py --config "..." sync --dry-run --limit 20
python scripts/shadow_notes.py --config "..." sync

# 4) 体检：缺笔记、孤儿笔记、未登记标签
python scripts/shadow_notes.py --config "..." verify

# 5) 统计：源文件数、副本数、版本族数
python scripts/shadow_notes.py --config "..." stats
```

`sync` 会自动先扫一遍（未变的文件复用上次的 MD5，只对改动过的文件重算），日常只跑 `sync` + `verify` 两条即可。

macOS 把路径换成 `/Users/你/...`，其余完全一样。

## 产出长什么样

```
<源目录>
  01_技改/网络安全改造合同.docx
  01_技改/网络安全改造合同 - 副本.docx

<Vault>/影子笔记/
  01_技改/网络安全改造合同.docx.md
  01_技改/网络安全改造合同 - 副本.docx.md
<Vault>/_影子索引.jsonl      每行一个源文件（给脚本和 AI 读）
<Vault>/_registry.json       扫描结果（含 MD5、族关系、内部时间戳）
<Vault>/_受控标签.md         标签唯一来源
```

单篇笔记的自动区：

```markdown
---
source_rel: "01_技改/网络安全改造合同.docx"
content_hash: "2c7d4747ec546fb1b4b0261aead6265f"
size: 477
mtime: "2026-10-26T09:47:51"
version_label: ""
dup_of: ""
family_id: "4b9a0804a642"
status: needs-review
---

<!-- SHADOW:AUTO -->
## 关键信息
- 大小：0.5 KB / 修改时间：2026-10-26
- 同内容副本 1 个：`01_技改/网络安全改造合同 - 副本.docx`

## 辨别
- 版本标记：无 / 推断状态：未知（需人工判断）
- 同族里谁最终有效，需人工确认后写进「人工补充」
<!-- /SHADOW:AUTO -->

## 人工补充
（重跑不会覆盖本段）
```

## 三条红线

1. **只读源文件**。脚本不写、不改、不删、不移动任何源文件。删掉整个索引重跑即可，源文件零风险。
2. **不编内容**。摘要只放本机抽到的原文片段（前 8000 字）；抽不到就写"未抽取到正文，以原件为准"。不用模型脑补金额、日期、甲乙方。
3. **人工判断单独成段**。每篇末尾的「## 人工补充」重跑永远保留，只替换 `<!-- SHADOW:AUTO -->…<!-- /SHADOW:AUTO -->` 之间。

## 版本族与副本规则

- **同内容副本**：MD5 完全相同。自动挑路径最短的一条当主文件，其余 `dup_of` 指向它。
- **近名版本族**：**同一目录 + 去掉版本标记后同名**才算一族。跨目录的同名文件不强行认亲——"2026年1月/2月"各一份同名文件是常态。
- 版本标记识别：`(1)`、`副本`、`copy`、`v2`、`第三版`、`终稿/定稿`、`草稿`、`用印/盖章`、`扫描件`、`修订`、`YYYYMMDD`、`YYYY-MM-DD`。
- 族内按修改时间排序，笔记里列前 8 个并标出"← 本文件"。

## 受控标签

`_受控标签.md` 是唯一来源，分五类：`entity/`（公司、系统、人）、`doctype/`（合同、规范书、验收报告…）、`topic/`（少用）、`year/`、`status/`。

- **实体优先于主题**：能标"哪个公司/系统"就不标"什么话题"。
- AI 建议的新标签**先不落盘**，收集成清单让人确认后再用。
- `verify` 会把用到了但没登记的标签列出来。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| PDF 摘要为空 | 本机没有 `pdftotext`。装 poppler（macOS `brew install poppler`，Windows 用 poppler-windows 并把 bin 加进 PATH）；不装也能用 |
| `.doc` / `.xls` 摘不到正文 | 老二进制格式，标准库解不开。另存为 `.docx` / `.xlsx` |
| 中文文件名乱码 | 脚本全程 UTF-8；Windows 控制台若是 GBK，命令前 `set PYTHONUTF8=1` |
| 扫描很慢 | 首次要算全部 MD5。之后只对 size/mtime 变过的文件重算 |
| OneDrive 占位文件 | 先让 OneDrive"始终保留在此设备上" |
| 路径太长报错 | Windows 260 字符限制：开启长路径支持，或把源目录层级压浅 |
| 源文件删了笔记还在 | `verify` 会列为孤儿笔记，确认后手动删除 |
| 旧笔记没有 AUTO 标记 | 脚本不覆盖，改名成 `.md.bak-时间戳` 再写新笔记 |

## 目录结构

```
shadow-notes/
  SKILL.md                     给 AI 读的技能说明（触发词、红线、执行约定）
  scripts/shadow_notes.py      零依赖管线：init / scan / sync / verify / stats
  templates/影子笔记模板.md     手工建笔记时的格式
  templates/受控标签清单.md     标签清单起点
  README.md                    本文件
```

## 给 AI 用的执行约定

1. 接到"整理这个文件夹"时：`init` → `scan` → `sync --dry-run --limit 20` 给人看样例 → 确认后 `sync` → `verify`。
2. 汇报时说清三个数：源文件数、同内容副本数、近名版本族数。
3. **不要**替用户改 `status: verified`，也不要删孤儿笔记。
4. **不要**把源文件复制进 Vault，**不要**把凭据、身份证号等敏感正文写进摘要。
5. 需要 AI 摘要时只对合同、技术规范书、验收报告、制度四类做，并标注"AI 生成，待核"。

## 许可

MIT License，可自由使用、修改、分发，保留版权声明即可。见 [LICENSE](LICENSE)。
