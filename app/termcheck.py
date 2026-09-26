"""双语术语核对：在双语 SRT 的英文轨中按英文词边界查找术语，
并核对命中块的中文轨是否采用指定译法。

规则：

- 复用 SRT 解析与双语拆轨（``split.split_subtitles``）：每块正文须恰好两行，
  一行中文、一行英文；非双行块由拆轨环节指出块号并拒绝核对。
- 英文轨仅在**相邻且间隔 0～500 毫秒**（下一块入点 − 上一块出点）的块之间
  拼接为同一条待查文本链；间隔超过 500 毫秒或时间重叠（间隔为负）处断链，
  术语不跨链匹配。
- 匹配前对英文做归一化：NFKC（全角→半角等）+ 小写折叠（casefold）+
  连续空白折叠为单个空格，再按**英文词边界**（\\w 边界）查找术语。
- 重叠候选的取舍：先取**覆盖字符最多**者，覆盖字符数相同先取**出现位置
  更靠前**者，再相同按**词表顺序**取先者；同一英文字符只归属一次（贪心
  选取后与已归属区间重叠的候选丢弃）。
- 一次命中可能跨多个拼接块，命中区间覆盖到的每个块逐一核对：该块中文轨
  **去除全部空白后**包含指定译法（同样去空白）即为「合格」，否则记为
  「缺译」，标出块号、英文原文与应译词。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .parsers import SrtItem
from .split import SplitRow, split_subtitles

MAX_CHAIN_GAP_MS = 500      # 英文轨拼接的最大相邻块间隔（毫秒）
MAX_TERMS = 2000            # 词表条目数上限


@dataclass
class GlossaryEntry:
    line_no: int            # 词表文本中的行号（从 1 开始）
    order: int              # 有效条目的词表序位（从 1 开始）
    term: str               # 英文术语原文（去首尾空白）
    norm_term: str          # 归一化英文术语
    translation: str        # 指定中文译法原文（去首尾空白）
    pattern: re.Pattern = field(repr=False, default=None)  # 词边界匹配正则


@dataclass
class TermRow:
    status: str             # ok（合格）/ missing（缺译）
    block: int              # 命中覆盖块的解析序位（从 1 开始）
    index: int              # 该块 SRT 声明序号
    start_tc: str
    end_tc: str
    term: str               # 英文术语原文
    translation: str        # 指定译法原文
    en_line: str            # 该块英文轨原文
    zh_line: str            # 该块中文轨原文
    term_order: int = 0     # 词表序位（结果排序用）
    hit_start: int = 0      # 命中在链文本中的起点（同块同序时排序用）


def normalize_en(text: str) -> str:
    """英文归一化：NFKC + 小写折叠 + 连续空白折叠为单个空格并去首尾空白。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _strip_ws(text: str) -> str:
    """去除全部 Unicode 空白（中文轨包含性核对前使用）。"""
    return re.sub(r"\s+", "", text)


def _build_term_pattern(norm_term: str) -> re.Pattern:
    """为归一化术语构造带英文词边界的正则。

    术语首/尾为 \\w 字符（字母/数字/下划线）时分别加左/右词边界断言；
    首/尾本身是非单词字符（如标点）时无法在该侧设界，不加断言。
    """
    pat = re.escape(norm_term)
    if re.match(r"^\w", norm_term):
        pat = r"(?<!\w)" + pat
    if re.search(r"\w$", norm_term):
        pat = pat + r"(?!\w)"
    return re.compile(pat)


def parse_glossary(text: str) -> list[GlossaryEntry]:
    """解析逐行「英文术语<TAB>指定中文译法」词表。

    完全空行忽略；任何一项缺少 TAB、术语或译法为空、归一化后术语为空、
    英文术语（归一化后）重复，均收集为错误，最终一次性抛出 ValueError，
    信息中指出全部问题所在的行号（重复时给出首次出现行）。
    """
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    entries: list[GlossaryEntry] = []
    seen: dict[str, int] = {}
    errors: list[str] = []
    for line_no, raw in enumerate(text.split("\n"), start=1):
        if raw == "":
            continue
        if not raw.strip():
            errors.append(
                f"第 {line_no} 行为空项：英文术语与指定中文译法均不能为空"
            )
            continue
        if "\t" not in raw:
            errors.append(
                f"第 {line_no} 行缺少 TAB 分隔：每项须为"
                f"「英文术语<TAB>指定中文译法」"
            )
            continue
        term_raw, trans_raw = raw.split("\t", 1)
        term = term_raw.strip()
        translation = trans_raw.strip()
        if not term or not translation:
            errors.append(
                f"第 {line_no} 行为空项：英文术语与指定中文译法均不能为空"
            )
            continue
        norm = normalize_en(term)
        if not norm:
            errors.append(f"第 {line_no} 行英文术语「{term}」归一化后为空")
            continue
        if norm in seen:
            errors.append(
                f"第 {line_no} 行英文术语「{term}」与第 {seen[norm]} 行重复"
            )
            continue
        seen[norm] = line_no
        entries.append(
            GlossaryEntry(
                line_no=line_no,
                order=len(entries) + 1,
                term=term,
                norm_term=norm,
                translation=translation,
                pattern=_build_term_pattern(norm),
            )
        )
    if errors:
        raise ValueError("；".join(errors))
    return entries


def _normalize_chain(
    segments: list[tuple[int, str]],
) -> tuple[str, list[int]]:
    """把若干块的英文行归一化拼接。

    ``segments`` 为 ``(owner, text)``：owner 为块解析序位（从 1 开始），
    块之间插入的单个拼接空格 owner 为 -1。逐字符做 NFKC + 小写折叠后再
    折叠空白，返回归一化文本与逐字符的 owner 列表（拼接空格为 -1）。
    """
    expanded: list[tuple[str, int]] = []
    for owner, text in segments:
        folded = unicodedata.normalize("NFKC", text).casefold()
        expanded.extend((ch, owner) for ch in folded)

    chars: list[str] = []
    owners: list[int] = []
    prev_is_space = True       # 视同首部为空白，自动去掉前导空白
    for ch, owner in expanded:
        if ch.isspace():
            if not prev_is_space:
                chars.append(" ")
                owners.append(owner)
                prev_is_space = True
        else:
            chars.append(ch)
            owners.append(owner)
            prev_is_space = False
    while chars and chars[-1] == " ":     # 去尾部空白
        chars.pop()
        owners.pop()
    return "".join(chars), owners


def build_chains(
    items: list[SrtItem], max_gap_ms: int = MAX_CHAIN_GAP_MS
) -> list[list[int]]:
    """按相邻块间隔（下一块入点 − 上一块出点，0～max_gap_ms）切分拼接链。

    返回各链包含的块解析序位（从 1 开始）；间隔超过上限或为负（重叠）处断链。
    """
    if not items:
        return []
    chains: list[list[int]] = []
    start = 1
    for pos in range(2, len(items) + 1):
        gap = items[pos - 1].start_ms - items[pos - 2].end_ms
        if 0 <= gap <= max_gap_ms:
            continue
        chains.append(list(range(start, pos)))
        start = pos
    chains.append(list(range(start, len(items) + 1)))
    return chains


def check_terms(
    items: list[SrtItem],
    rows: list[SplitRow],
    entries: list[GlossaryEntry],
    max_gap_ms: int = MAX_CHAIN_GAP_MS,
) -> list[TermRow]:
    """对拆轨后的双语字幕逐条核对术语译法。"""
    if not items or not entries:
        return []

    out: list[TermRow] = []
    for chain_pos in build_chains(items, max_gap_ms):
        segments: list[tuple[int, str]] = []
        for pos in chain_pos:
            if segments:
                segments.append((-1, " "))   # 块间拼接空格，不属于任何块
            segments.append((pos, rows[pos - 1].en_line))
        text, owners = _normalize_chain(segments)
        if not text:
            continue

        # 收集全部候选：(起点, 终点, 词表序位)
        candidates: list[tuple[int, int, int]] = []
        for entry in entries:
            for m in entry.pattern.finditer(text):
                candidates.append((m.start(), m.end(), entry.order))
        # 覆盖字符最多优先；再按出现位置、词表顺序
        candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0], c[2]))

        claimed = bytearray(len(text))
        for s, e, order in candidates:
            if any(claimed[s:e]):
                continue                    # 与已归属字符重叠，丢弃
            claimed[s:e] = b"\x01" * (e - s)
            entry = entries[order - 1]
            covered = sorted({
                owners[k]
                for k in range(s, e)
                if owners[k] >= 0 and not text[k].isspace()
            })
            wanted = _strip_ws(entry.translation)
            for pos in covered:
                row = rows[pos - 1]
                ok = wanted in _strip_ws(row.zh_line)
                out.append(
                    TermRow(
                        status="ok" if ok else "missing",
                        block=pos,
                        index=row.index,
                        start_tc=row.start_tc,
                        end_tc=row.end_tc,
                        term=entry.term,
                        translation=entry.translation,
                        en_line=row.en_line,
                        zh_line=row.zh_line,
                        term_order=order,
                        hit_start=s,
                    )
                )

    out.sort(key=lambda r: (r.block, r.term_order, r.hit_start))
    return out


def check_subtitles(
    items: list[SrtItem], glossary_text: str
) -> tuple[list[GlossaryEntry], list[TermRow]]:
    """解析词表并复用双语拆轨后完成术语核对；任一词表/拆轨错误抛 ValueError。"""
    entries = parse_glossary(glossary_text)
    if len(entries) > MAX_TERMS:
        raise ValueError(f"词表条目数超过 {MAX_TERMS} 条的上限，请拆分后再核对")
    rows = split_subtitles(items)
    return entries, check_terms(items, rows, entries)
