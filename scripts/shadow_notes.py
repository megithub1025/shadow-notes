#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shadow_notes.py — 影子笔记管线（零第三方依赖，Windows / macOS 通用）

源文件不动，只在 Vault 里生成镜像目录结构的 .md 影子笔记。

用法：
  python shadow_notes.py init   --source <源目录> --vault <Vault目录>
  python shadow_notes.py --config <shadow-notes.json> scan
  python shadow_notes.py --config <shadow-notes.json> sync [--force] [--dry-run] [--limit N]
  python shadow_notes.py --config <shadow-notes.json> verify
  python shadow_notes.py --config <shadow-notes.json> stats

设计约束：
  - 只用标准库；不联网；不改动任何源文件（只读）。
  - 笔记正文分「自动区」和「人工补充」两段，重跑只替换自动区。
  - 没有标记的旧笔记不覆盖，改名备份后重写。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

SCHEMA = 1
MARK_START = "<!-- SHADOW:AUTO -->"
MARK_END = "<!-- /SHADOW:AUTO -->"
DEFAULT_CONFIG = "shadow-notes.json"

DEFAULT_INCLUDE = [
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pdf", ".txt", ".md", ".csv", ".wps", ".et", ".dps",
    ".zip", ".rar", ".7z",
]
DEFAULT_SKIP = [
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".webp",
    ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav",
]
DEFAULT_EXCLUDE_DIRS = [
    ".git", ".obsidian", ".trash", "node_modules", "__pycache__",
    "$RECYCLE.BIN", "System Volume Information", ".DS_Store",
]

# 版本/副本标记：按优先级匹配文件名词尾
VERSION_PATTERNS = [
    (r"[（(]\s*\d+\s*[)）]\s*$", "副本"),
    (r"[-_ ]?副本\s*$", "副本"),
    (r"[-_ ]?copy\s*$", "副本"),
    (r"[-_ ]?v(?:er)?\.?\s*\d+(?:\.\d+)*\s*$", "版本号"),
    (r"[-_ ]?第\s*\d+\s*版\s*$", "版本号"),
    (r"[-_ ]?(?:终稿|最终版?|定稿)\s*$", "终稿"),
    (r"[-_ ]?(?:草稿|初稿|draft)\s*$", "草稿"),
    (r"[-_ ]?(?:用印|盖章|签章)\s*$", "用印"),
    (r"[-_ ]?(?:扫描件?|扫描)\s*$", "扫描件"),
    (r"[-_ ]?(?:已修订|修订版?|修改版?)\s*$", "修订"),
    (r"[-_ ]?\d{8}\s*$", "日期"),
    (r"[-_ ]?\d{4}[-_.]\d{1,2}[-_.]\d{1,2}\s*$", "日期"),
]

STATUS_BY_LABEL = {
    "终稿": "终稿",
    "用印": "用印",
    "扫描件": "扫描件",
    "副本": "副本",
    "草稿": "草稿",
    "修订": "修订稿",
    "版本号": "版本稿",
    "日期": "按日期存档",
}


try:  # Windows 控制台常是 GBK，先尽量切 UTF-8，失败也不影响文件
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def say(*args):
    line = " ".join(str(a) for a in args)
    line = line.replace("✓", "[OK]").replace("✗", "[X]").replace("⚠", "[!]").replace("…", "...")
    try:
        print(line)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"))


def die(msg, code=2):
    say("错误：" + msg)
    sys.exit(code)


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def find_config(explicit=None):
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            die("配置文件不存在：%s" % p)
        return p
    env = os.environ.get("SHADOW_NOTES_CONFIG")
    if env and Path(env).is_file():
        return Path(env)
    for cand in (Path.cwd() / DEFAULT_CONFIG, Path(__file__).resolve().parent / DEFAULT_CONFIG):
        if cand.is_file():
            return cand
    die("找不到 %s。先运行 init，或用 --config 指定。" % DEFAULT_CONFIG)


def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    for key in ("source_root", "notes_dir", "index_file", "registry_file", "vocabulary"):
        if not data.get(key):
            die("配置缺字段：%s" % key)
    data["_base"] = base
    data["_source"] = Path(data["source_root"]).expanduser().resolve()
    data["_notes"] = (base / data["notes_dir"]).resolve() if not Path(data["notes_dir"]).is_absolute() \
        else Path(data["notes_dir"])
    data["_index"] = (base / data["index_file"]).resolve() if not Path(data["index_file"]).is_absolute() \
        else Path(data["index_file"])
    data["_registry"] = (base / data["registry_file"]).resolve() if not Path(data["registry_file"]).is_absolute() \
        else Path(data["registry_file"])
    data["_vocab"] = (base / data["vocabulary"]).resolve() if not Path(data["vocabulary"]).is_absolute() \
        else Path(data["vocabulary"])
    data.setdefault("include_ext", DEFAULT_INCLUDE)
    data.setdefault("skip_ext", DEFAULT_SKIP)
    data.setdefault("exclude_dirs", DEFAULT_EXCLUDE_DIRS)
    data.setdefault("extract_chars", 8000)
    data.setdefault("max_mb", 200)
    return data


def cmd_init(args):
    source = Path(args.source).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    if not source.is_dir():
        die("源目录不存在：%s" % source)
    vault.mkdir(parents=True, exist_ok=True)

    cfg_path = vault / DEFAULT_CONFIG
    cfg = {
        "schema": SCHEMA,
        "source_root": str(source),
        "notes_dir": "影子笔记",
        "index_file": "_影子索引.jsonl",
        "registry_file": "_registry.json",
        "vocabulary": "_受控标签.md",
        "include_ext": DEFAULT_INCLUDE,
        "skip_ext": DEFAULT_SKIP,
        "exclude_dirs": DEFAULT_EXCLUDE_DIRS,
        "extract_chars": 8000,
        "max_mb": 200,
    }
    if cfg_path.exists() and not args.force:
        die("配置已存在：%s（要覆盖加 --force）" % cfg_path)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (vault / "影子笔记").mkdir(exist_ok=True)
    vocab = vault / "_受控标签.md"
    if not vocab.exists():
        vocab.write_text(VOCAB_TEMPLATE, encoding="utf-8")

    say("已初始化：%s" % cfg_path)
    say("源目录：%s" % source)
    say("笔记目录：%s" % (vault / "影子笔记"))
    say("")
    say("下一步：")
    say("  python %s --config \"%s\" scan" % (Path(__file__).name, cfg_path))
    say("  python %s --config \"%s\" sync" % (Path(__file__).name, cfg_path))


VOCAB_TEMPLATE = """# 受控标签清单（唯一来源）

规则：标签必须先写进本清单，才能用在影子笔记里。**实体优先于主题**。

## 实体 entity/（公司、单位、系统、人物）
- entity/示例公司
- entity/示例系统

## 文档类型 doctype/
- doctype/合同
- doctype/技术规范书
- doctype/验收报告
- doctype/台账
- doctype/会议纪要
- doctype/制度

## 主题 topic/（少用，能落到实体就不建主题）
- topic/示例主题

## 年份 year/
- year/2025
- year/2026

## 状态 status/（系统自动）
- status/needs-review
- status/verified
- status/low-confidence
"""


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

def file_md5(path, max_mb):
    try:
        if max_mb and path.stat().st_size > max_mb * 1024 * 1024:
            return ""
    except OSError:
        return ""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def split_version(stem):
    """把文件名拆成 (基名, 版本标记, 状态)。"""
    base = stem.strip()
    label = ""
    for pat, kind in VERSION_PATTERNS:
        m = re.search(pat, base, flags=re.IGNORECASE)
        if m:
            label = m.group(0).strip(" -_")
            base = base[: m.start()].strip(" -_") or base
            return base, label, STATUS_BY_LABEL.get(kind, kind)
    return base, "", ""


def walk_sources(cfg):
    root = cfg["_source"]
    inc = {e.lower() for e in cfg["include_ext"]}
    skip = {e.lower() for e in cfg["skip_ext"]}
    excl = {d.lower() for d in cfg["exclude_dirs"]}
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in excl and not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name.startswith("~$"):
                continue  # 隐藏文件 / Office 锁文件
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext in skip:
                continue
            if inc and ext not in inc:
                continue
            out.append(p)
    out.sort(key=lambda x: str(x).lower())
    return out


def rel_posix(path, root):
    return Path(path).resolve().relative_to(root).as_posix()


def cmd_scan(args):
    cfg = load_config(args.config)
    reg = do_scan(cfg)
    by_family = {}
    for e in reg["entries"]:
        by_family.setdefault(e["family_key"], []).append(e)
    dups = sum(1 for e in reg["entries"] if e.get("dup_of"))
    fams = len([g for g in by_family.values() if len(g) > 1])
    say("完成：%d 个文件" % reg["count"])
    say("  同内容副本 %d 个；近名版本族 %d 组" % (dups, fams))
    say("  清单：%s" % cfg["_registry"])


def do_scan(cfg, quiet=False):
    """扫描并写 registry；size+mtime 未变的文件复用上次的 MD5。"""
    root = cfg["_source"]
    if not root.is_dir():
        die("源目录不存在：%s" % root)
    files = walk_sources(cfg)
    prev = {}
    if cfg["_registry"].is_file():
        try:
            old = json.loads(cfg["_registry"].read_text(encoding="utf-8"))
            for e in old.get("entries", []):
                prev[e.get("source_rel")] = e
        except Exception:
            prev = {}
    if not quiet:
        say("扫描 %s" % root)
    entries = []
    t0 = time.time()
    reused = 0
    for i, p in enumerate(files, 1):
        st = p.stat()
        rel = rel_posix(p, root)
        base, label, status = split_version(p.stem)
        parent = Path(rel).parent.as_posix()
        fam_src = (parent + "/" + base).lower()
        old = prev.get(rel)
        if old and old.get("size") == st.st_size and old.get("_mtime_ns") == st.st_mtime_ns and old.get("md5"):
            md5 = old["md5"]
            reused += 1
        else:
            md5 = file_md5(p, cfg["max_mb"])
        entries.append({
            "source_rel": rel,
            "source_path": str(p),
            "file_type": p.suffix.lower(),
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).replace(microsecond=0).isoformat(),
            "_mtime_ns": st.st_mtime_ns,
            "md5": md5,
            "version_label": label,
            "doc_status": status,
            "family_key": hashlib.md5(fam_src.encode("utf-8")).hexdigest()[:12],
            "base_name": base,
        })
        if not quiet and i % 200 == 0:
            say("  … %d / %d" % (i, len(files)))

    # 同内容副本：同 md5 归一组，第一条为主，其余 dup_of
    by_hash = {}
    for e in entries:
        if e["md5"]:
            by_hash.setdefault(e["md5"], []).append(e)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: (len(x["source_rel"]), x["source_rel"]))
        primary = group[0]["source_rel"]
        for e in group[1:]:
            e["dup_of"] = primary

    # 同族：同目录 + 同基名，≥2 个才算
    by_family = {}
    for e in entries:
        by_family.setdefault(e["family_key"], []).append(e)
    for group in by_family.values():
        group.sort(key=lambda x: (x["mtime"], x["source_rel"]))
        for idx, e in enumerate(group):
            e["family_size"] = len(group)
            e["family_index"] = idx + 1
        if len(group) < 2:
            for e in group:
                e["family_size"] = 1

    registry = {
        "schema": SCHEMA,
        "source_root": str(root),
        "scanned_at": now_iso(),
        "count": len(entries),
        "entries": entries,
    }
    cfg["_registry"].parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg["_registry"].with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(cfg["_registry"])

    dups = sum(1 for e in entries if e.get("dup_of"))
    fams = len([g for g in by_family.values() if len(g) > 1])
    if not quiet:
        say("扫描完成：%d 个文件，%.1fs（复用哈希 %d 个）" % (len(entries), time.time() - t0, reused))
    return registry


def load_registry(cfg):
    if not cfg["_registry"].is_file():
        die("还没有扫描结果，先运行 scan：%s" % cfg["_registry"])
    return json.loads(cfg["_registry"].read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 取文本（本地、无依赖）
# --------------------------------------------------------------------------

def clean_text(s, limit):
    s = s.replace("\x00", " ")
    s = re.sub(r"[ \t ]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    s = s.strip()
    return s[:limit]


def xml_text(data):
    s = data.decode("utf-8", "ignore")
    s = re.sub(r"</w:p>|</a:p>|</text:p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
         .replace("&quot;", '"').replace("&apos;", "'")
    return s


def extract_text(path, limit):
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md", ".csv"):
            raw = path.read_bytes()
            for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
                try:
                    return clean_text(raw.decode(enc), limit)
                except UnicodeDecodeError:
                    continue
            return ""
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                return clean_text(xml_text(z.read("word/document.xml")), limit)
        if ext == ".pptx":
            with zipfile.ZipFile(path) as z:
                names = sorted(n for n in z.namelist()
                               if re.match(r"ppt/slides/slide\d+\.xml$", n))
                return clean_text("\n".join(xml_text(z.read(n)) for n in names), limit)
        if ext == ".xlsx":
            with zipfile.ZipFile(path) as z:
                if "xl/sharedStrings.xml" not in z.namelist():
                    return ""
                return clean_text(xml_text(z.read("xl/sharedStrings.xml")), limit)
        if ext == ".pdf":
            exe = shutil.which("pdftotext")
            if not exe:
                return ""
            out = subprocess.run([exe, "-l", "8", "-layout", str(path), "-"],
                                 capture_output=True, timeout=60)
            return clean_text(out.stdout.decode("utf-8", "ignore"), limit)
    except Exception:
        return ""
    return ""


# --------------------------------------------------------------------------
# 写笔记
# --------------------------------------------------------------------------

def yq(s):
    return json.dumps(s, ensure_ascii=False)


def note_path_for(cfg, entry):
    rel = Path(entry["source_rel"])
    return (cfg["_notes"] / rel.parent / (rel.name + ".md"))


def build_note(cfg, entry, siblings, existing_tail):
    p = Path(entry["source_path"])
    url = p.as_uri()
    excerpt = ""
    if cfg.get("extract_chars"):
        excerpt = extract_text(p, int(cfg["extract_chars"]))

    fm = [
        "---",
        "source_rel: %s" % yq(entry["source_rel"]),
        "source_path: %s" % yq(entry["source_path"]),
        "file_type: %s" % yq(entry["file_type"]),
        "content_hash: %s" % yq(entry["md5"]),
        "size: %d" % entry["size"],
        "mtime: %s" % yq(entry["mtime"]),
        "version_label: %s" % yq(entry.get("version_label", "")),
        "doc_status: %s" % yq(entry.get("doc_status", "")),
        "dup_of: %s" % yq(entry.get("dup_of", "")),
        "family_id: %s" % yq(entry.get("family_key", "")),
        "processed_date: %s" % yq(now_iso()),
        "status: needs-review",
        "tags: []",
        "---",
    ]

    facts = [
        "- 大小：%.1f KB" % (entry["size"] / 1024.0),
        "- 修改时间：%s" % entry["mtime"],
        "- 类型：%s" % (entry["file_type"] or "无扩展名"),
    ]
    if entry.get("dup_of"):
        facts.append("- **同内容副本**：与 `%s` 的 MD5 相同" % entry["dup_of"])
    else:
        same = [e for e in siblings if e.get("dup_of") == entry["source_rel"]]
        if same:
            facts.append("- 同内容副本 %d 个：%s" % (len(same), "、".join(
                "`%s`" % e["source_rel"] for e in same[:5])))

    fam = [e for e in siblings if e.get("family_key") == entry.get("family_key")]
    if len(fam) > 1:
        facts.append("- 同目录近名文件 %d 个（按修改时间排序）：" % len(fam))
        for e in fam[:8]:
            mark = "← 本文件" if e["source_rel"] == entry["source_rel"] else ""
            facts.append("    - %s｜%s｜%s %s" % (
                e["source_rel"].rsplit("/", 1)[-1], e["mtime"][:10],
                e.get("version_label") or "无标记", mark))
    else:
        facts.append("- 同目录无近名版本")

    disc = [
        "- 版本标记：%s" % (entry.get("version_label") or "无"),
        "- 推断状态：%s" % (entry.get("doc_status") or "未知（需人工判断）"),
    ]
    if entry.get("dup_of"):
        disc.append("- 本文件是副本，**优先用主文件**；确属不同用途再保留")
    if len(fam) > 1:
        disc.append("- 同族里谁最终有效，需人工确认后把本段写进「人工补充」")

    auto = [MARK_START, "## 摘要", ""]
    if excerpt:
        auto.append(excerpt)
    else:
        auto.append("_（本机未抽取到正文：旧版 .doc/.xls、加密文件，或 PDF 缺少 pdftotext。以原件为准。）_")
    auto += ["", "## 关键信息", ""] + facts
    auto += ["", "## 辨别", ""] + disc
    auto += ["", "## 打开原件", "", "[打开原件](%s)" % url, MARK_END]

    body = "\n".join(fm) + "\n\n# %s\n\n" % p.name + "\n".join(auto) + "\n"
    if existing_tail is None:
        body += "\n## 人工补充\n\n（重跑不会覆盖本段。可写：是不是终稿、和谁相关、重要结论。）\n"
    else:
        body += existing_tail
    return body


def split_existing(text):
    """返回 (是否有自动区, 人工补充段)。"""
    if MARK_END in text:
        return True, text.split(MARK_END, 1)[1]
    return False, None


def cmd_sync(args):
    cfg = load_config(args.config)
    if args.no_scan:
        reg = load_registry(cfg)
    else:
        reg = do_scan(cfg, quiet=False)
        say("  同内容副本 %d 个；近名版本族 %d 组" % (
            sum(1 for e in reg["entries"] if e.get("dup_of")),
            len({e["family_key"] for e in reg["entries"] if e.get("family_size", 0) > 1})))
    entries = reg["entries"]
    cfg["_notes"].mkdir(parents=True, exist_ok=True)

    by_family = {}
    for e in entries:
        by_family.setdefault(e["family_key"], []).append(e)

    written = skipped = backed = failed = 0
    limit = args.limit or 0
    for i, e in enumerate(entries, 1):
        if limit and written >= limit:
            break
        np_ = note_path_for(cfg, e)
        np_.parent.mkdir(parents=True, exist_ok=True)
        old = np_.read_text(encoding="utf-8") if np_.is_file() else None

        if old and not args.force:
            if ("content_hash: %s" % yq(e["md5"])) in old and MARK_START in old:
                skipped += 1
                continue

        has_marks, tail = (True, None)
        if old is not None:
            has_marks, tail = split_existing(old)
            if not has_marks:
                bak = np_.with_suffix(np_.suffix + ".bak-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"))
                np_.replace(bak)
                backed += 1
                say("  旧笔记无标记，已备份：%s" % bak.name)

        content = build_note(cfg, e, by_family.get(e["family_key"], []), tail)
        if args.dry_run:
            say("  [dry] %s" % np_.relative_to(cfg["_notes"]))
        else:
            tmp = np_.with_suffix(np_.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(np_)
        written += 1
        if i % 200 == 0:
            say("  … %d / %d" % (i, len(entries)))

    if not args.dry_run:
        with open(cfg["_index"], "w", encoding="utf-8", newline="\n") as f:
            for e in entries:          # 索引始终覆盖全部源文件，不受 --limit 影响
                f.write(json.dumps(index_row(e), ensure_ascii=False) + "\n")

    say("同步完成：写入/更新 %d，跳过未变 %d，旧笔记备份 %d，失败 %d" % (written, skipped, backed, failed))
    if not args.dry_run:
        say("索引：%s" % cfg["_index"])


def index_row(e):
    return {
        "source_rel": e["source_rel"],
        "file_type": e["file_type"],
        "size": e["size"],
        "mtime": e["mtime"],
        "md5": e["md5"],
        "version_label": e.get("version_label", ""),
        "doc_status": e.get("doc_status", ""),
        "dup_of": e.get("dup_of", ""),
        "family_id": e.get("family_key", ""),
    }


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def cmd_verify(args):
    cfg = load_config(args.config)
    reg = load_registry(cfg)
    entries = reg["entries"]
    problems = 0

    missing = []
    for e in entries:
        if not note_path_for(cfg, e).is_file():
            missing.append(e["source_rel"])
    if missing:
        problems += len(missing)
        say("✗ 缺笔记 %d 篇，前 10：" % len(missing))
        for m in missing[:10]:
            say("    " + m)
    else:
        say("✓ 每个源文件都有影子笔记（%d）" % len(entries))

    orphans = []
    for np_ in cfg["_notes"].rglob("*.md"):
        rel_note = np_.relative_to(cfg["_notes"]).as_posix()
        src_rel = rel_note[:-3]  # 去掉 .md
        src = cfg["_source"] / src_rel
        if not src.is_file():
            orphans.append(rel_note)
    if orphans:
        problems += len(orphans)
        say("✗ 源文件已不在的笔记 %d 篇（旧版/改名/移动），前 10：" % len(orphans))
        for o in orphans[:10]:
            say("    " + o)
    else:
        say("✓ 没有孤儿笔记")

    dups = [e for e in entries if e.get("dup_of")]
    fams = {}
    for e in entries:
        fams.setdefault(e["family_key"], []).append(e)
    fams = {k: v for k, v in fams.items() if len(v) > 1}
    say("· 同内容副本 %d 个，近名版本族 %d 组" % (len(dups), len(fams)))

    vocab = set()
    if cfg["_vocab"].is_file():
        for line in cfg["_vocab"].read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*[-*]\s+([a-z]+/[^\s]+)", line)
            if m:
                vocab.add(m.group(1))
    unknown = set()
    for np_ in cfg["_notes"].rglob("*.md"):
        txt = np_.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^tags:\s*\[(.*?)\]", txt, flags=re.M)
        if not m:
            continue
        for t in re.findall(r"[A-Za-z]+/[^\s,\]]+", m.group(1)):
            if vocab and t not in vocab:
                unknown.add(t)
    if unknown:
        say("⚠ 未登记的标签 %d 个：%s" % (len(unknown), "、".join(sorted(unknown)[:10])))
    else:
        say("✓ 标签都在受控清单内")

    say("")
    say("结论：%s" % ("健康" if problems == 0 else "有 %d 处待处理" % problems))
    sys.exit(0 if problems == 0 else 1)


def cmd_stats(args):
    cfg = load_config(args.config)
    reg = load_registry(cfg)
    entries = reg["entries"]
    total = sum(e["size"] for e in entries)
    by_ext = {}
    for e in entries:
        by_ext[e["file_type"]] = by_ext.get(e["file_type"], 0) + 1
    dup = len([e for e in entries if e.get("dup_of")])
    fams = {}
    for e in entries:
        fams.setdefault(e["family_key"], []).append(e)
    fams = {k: v for k, v in fams.items() if len(v) > 1}
    say("源文件：%d 个，%.1f GB" % (len(entries), total / 1024.0 / 1024 / 1024))
    say("类型分布：" + "，".join("%s %d" % (k or "无", v)
                              for k, v in sorted(by_ext.items(), key=lambda x: -x[1])[:10]))
    say("同内容副本：%d 个（%.0f%%）" % (dup, 100.0 * dup / max(1, len(entries))))
    say("近名版本族：%d 组，涉及 %d 个文件" % (len(fams), sum(len(v) for v in fams.values())))
    say("状态分布：" + "，".join(
        "%s %d" % (k or "未知", v) for k, v in sorted(
            {s: sum(1 for e in entries if (e.get("doc_status") or "") == s)
             for s in {e.get("doc_status") or "" for e in entries}}.items(),
            key=lambda x: -x[1])))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="影子笔记管线（零依赖）")
    ap.add_argument("--config", help="shadow-notes.json 路径")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("init", help="初始化配置与目录")
    p1.add_argument("--source", required=True, help="源文件根目录")
    p1.add_argument("--vault", required=True, help="Obsidian Vault 或索引目录")
    p1.add_argument("--force", action="store_true")
    p1.set_defaults(func=cmd_init)

    p2 = sub.add_parser("scan", help="扫描源目录，算 MD5、查重、分族")
    p2.set_defaults(func=cmd_scan)

    p3 = sub.add_parser("sync", help="生成/更新影子笔记与索引")
    p3.add_argument("--force", action="store_true", help="忽略哈希，全部重写")
    p3.add_argument("--dry-run", action="store_true")
    p3.add_argument("--limit", type=int, default=0, help="只处理前 N 个（试跑用）")
    p3.add_argument("--no-scan", action="store_true", help="不重新扫描，直接用上次 registry")
    p3.set_defaults(func=cmd_sync)

    p4 = sub.add_parser("verify", help="体检：缺笔记、孤儿、标签")
    p4.set_defaults(func=cmd_verify)

    p5 = sub.add_parser("stats", help="统计")
    p5.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
