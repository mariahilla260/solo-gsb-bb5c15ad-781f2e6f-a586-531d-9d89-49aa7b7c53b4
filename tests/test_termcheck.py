"""双语术语核对：词表解析、英文归一化、拼接链与命中核对规则单元/接口测试。

运行：python -m pytest tests/ -q
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parsers import parse_srt
from app.termcheck import (
    MAX_CHAIN_GAP_MS,
    _normalize_chain,
    build_chains,
    check_subtitles,
    normalize_en,
    parse_glossary,
)

client = TestClient(app)


def _tc(ms):
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _srt(blocks, declared=None):
    """blocks: [(start_ms, end_ms, [正文行...]), ...]。"""
    out = []
    for i, (s, e, lines) in enumerate(blocks, start=1):
        idx = declared[i - 1] if declared else i
        out.append(f"{idx}\n{_tc(s)} --> {_tc(e)}\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n"


def _check(blocks, glossary):
    subs = parse_srt(_srt(blocks))
    return check_subtitles(subs, glossary)


# ---------- 英文归一化 ----------

def test_normalize_en_case_width_whitespace():
    assert normalize_en("  GPU  ＡＩ\nLearning ") == "gpu ai learning"
    assert normalize_en("Artificial　Intelligence") == "artificial intelligence"


def test_normalize_chain_owners_and_join_space():
    segs = [(1, "Hello"), (-1, " "), (2, " World ")]
    text, owners = _normalize_chain(segs)
    assert text == "hello world"
    # 拼接空格 owner 为 -1，其余字符归属对应块
    assert owners[5] == -1
    assert owners[:5] == [1] * 5
    assert owners[6:] == [2] * 5


# ---------- 词表解析 ----------

def test_parse_glossary_ok():
    entries = parse_glossary("GPU\t图形处理器\nmachine learning\t机器学习\n")
    assert len(entries) == 2
    assert entries[0].term == "GPU"
    assert entries[0].norm_term == "gpu"
    assert entries[0].translation == "图形处理器"
    assert [e.line_no for e in entries] == [1, 2]
    assert [e.order for e in entries] == [1, 2]


def test_parse_glossary_missing_tab_points_line():
    with pytest.raises(ValueError, match=r"第 2 行缺少 TAB 分隔"):
        parse_glossary("GPU\t图形处理器\nbroken line")


def test_parse_glossary_empty_term_and_translation():
    with pytest.raises(ValueError, match=r"第 1 行为空项"):
        parse_glossary("\t译法")
    with pytest.raises(ValueError, match=r"第 1 行为空项"):
        parse_glossary("term\t")
    with pytest.raises(ValueError, match=r"第 1 行为空项"):
        parse_glossary("   \t  ")
    with pytest.raises(ValueError, match=r"第 1 行为空项"):
        parse_glossary("   ")


def test_parse_glossary_duplicate_term_points_first_line():
    with pytest.raises(ValueError, match=r"第 2 行英文术语「gpu」与第 1 行重复"):
        parse_glossary("GPU\t图形处理器\ngpu\t显卡")


def test_parse_glossary_duplicate_term_fullwidth_and_casefold():
    with pytest.raises(ValueError, match="与第 1 行重复"):
        parse_glossary("ＡＩ\t人工智能\nai\t爱")


def test_parse_glossary_collects_all_errors():
    text = "bad\nGPU\t显卡\n   \ngpu\t图形处理器"
    with pytest.raises(ValueError) as exc:
        parse_glossary(text)
    msg = str(exc.value)
    assert "第 1 行缺少 TAB 分隔" in msg
    assert "第 3 行为空项" in msg
    assert "第 4 行英文术语" in msg
    assert msg.count("；") == 2


def test_parse_glossary_extra_tab_kept_in_translation():
    entries = parse_glossary("a\t译\t法")
    assert entries[0].term == "a"
    assert entries[0].translation == "译\t法"


# ---------- 拼接链 ----------

def test_build_chains_gap_boundaries():
    blocks = [
        (0, 1000, ["中", "a"]),       # 间隔 0
        (1000, 2000, ["中", "b"]),    # 间隔 500
        (2501, 3000, ["中", "c"]),    # 间隔 501，断链
        (3700, 4000, ["中", "d"]),    # 间隔 700，断链
    ]
    subs = parse_srt(_srt(blocks))
    chains = build_chains(subs)
    assert chains == [[1, 2], [3], [4]]


def test_build_chains_gap_exactly_500_joins():
    blocks = [
        (0, 1000, ["中", "a"]),
        (1500, 2000, ["中", "b"]),
        (2000, 2500, ["中", "c"]),
    ]
    subs = parse_srt(_srt(blocks))
    assert build_chains(subs) == [[1, 2, 3]]


def test_build_chains_overlap_breaks():
    blocks = [
        (0, 1500, ["中", "a"]),
        (1200, 2000, ["中", "b"]),    # 间隔 -300，断链
        (2000, 2500, ["中", "c"]),
    ]
    subs = parse_srt(_srt(blocks))
    assert build_chains(subs) == [[1], [2, 3]]


def test_max_chain_gap_constant():
    assert MAX_CHAIN_GAP_MS == 500


# ---------- 命中与核对 ----------

BILINGUAL_CHAIN = [
    (1000, 3000, ["今天聊人工智能。", "Today we talk about artificial"]),
    (3100, 5000, ["人工智能改变行业。", "intelligence in the industry."]),
]


def test_hit_across_blocks_both_qualified():
    _, rows = _check(BILINGUAL_CHAIN, "artificial intelligence\t人工智能")
    assert [r.block for r in rows] == [1, 2]
    assert all(r.status == "ok" for r in rows)
    assert {r.term for r in rows} == {"artificial intelligence"}


def test_missing_translation_reports_block_en_and_wanted():
    blocks = [
        (1000, 3000, ["今天聊相关技术。", "Today we talk about artificial"]),
        (3100, 5000, ["这门技术改变行业。", "intelligence in the industry."]),
    ]
    _, rows = _check(blocks, "artificial intelligence\t人工智能")
    assert [r.status for r in rows] == ["missing", "missing"]
    r = rows[0]
    assert r.block == 1
    assert r.en_line == "Today we talk about artificial"
    assert r.translation == "人工智能"
    assert r.zh_line == "今天聊相关技术。"


def test_second_block_missing_only():
    blocks = [
        (1000, 3000, ["今天聊人工智能。", "Today we talk about artificial"]),
        (3100, 5000, ["这门技术改变行业。", "intelligence in the industry."]),
    ]
    _, rows = _check(blocks, "artificial intelligence\t人工智能")
    assert [r.status for r in rows] == ["ok", "missing"]
    assert rows[1].block == 2


def test_case_insensitive_and_fullwidth_match():
    blocks = [(1000, 2000, ["图形处理器很快。", "ＴＨＥ  ｇｐｕ ｉｓ fast."])]
    _, rows = _check(blocks, "GPU\t图形处理器")
    assert len(rows) == 1
    assert rows[0].status == "ok"


def test_word_boundary_no_partial_match():
    blocks = [(1000, 2000, ["GPU 数量很多。", "Many GPUs are installed."])]
    _, rows = _check(blocks, "GPU\t图形处理器")
    assert rows == []
    # 单词形式可命中
    blocks2 = [(1000, 2000, ["图形处理器很多。", "Many GPU units are installed."])]
    _, rows2 = _check(blocks2, "GPU\t图形处理器")
    assert len(rows2) == 1 and rows2[0].status == "ok"


def test_boundary_against_digits_and_letters():
    # xGPU、GPU2 均不算词边界命中
    blocks = [(1000, 2000, ["不命中", "xGPU and GPU2 here."])]
    _, rows = _check(blocks, "GPU\t图形处理器")
    assert rows == []


def test_term_ending_with_punctuation():
    blocks = [(1000, 2000, ["好的。", "Yes, it is."])]
    _, rows = _check(blocks, "Yes,\t好的")
    assert len(rows) == 1 and rows[0].status == "ok"


def test_whitespace_normalized_in_zh_check():
    blocks = [(1000, 2000, ["人工  智能。", "Artificial intelligence."])]
    _, rows = _check(blocks, "artificial intelligence\t人工智能")
    assert len(rows) == 1 and rows[0].status == "ok"


def test_overlapping_terms_longest_wins():
    blocks = [(1000, 2000, ["机器学习很好。", "Machine learning is great."])]
    _, rows = _check(blocks, "learning\t学习\nmachine learning\t机器学习")
    terms = {r.term for r in rows}
    assert terms == {"machine learning"}
    assert all(r.status == "ok" for r in rows)


def test_same_term_twice_in_chain_each_block_once():
    # 同一术语在一条拼接链内出现两次、分属两块：每块各核对一次
    blocks = [
        (1000, 2000, ["图形处理器渲染。", "GPU renders."]),
        (2000, 3000, ["另一块图形处理器。", "Another GPU here."]),
    ]
    _, rows = _check(blocks, "GPU\t图形处理器")
    assert [r.block for r in rows] == [1, 2]
    assert all(r.status == "ok" for r in rows)


def test_each_char_claimed_once():
    # 术语 "GPU GPU" 覆盖两个 GPU；短术语 GPU 与长候选共享字符，长候选胜出，
    # 短候选两次出现均与其重叠而被丢弃
    blocks = [(1000, 2000, ["双卡图形处理器。", "GPU GPU rendering."])]
    _, rows = _check(blocks, "GPU\t显卡\nGPU GPU\t双卡图形处理器")
    terms = {r.term for r in rows}
    assert terms == {"GPU GPU"}


def test_no_match_across_broken_chain():
    blocks = [
        (1000, 3000, ["今天聊 artificial。", "Today we talk about artificial"]),
        (3600, 5000, ["intelligence 改变行业。", "intelligence in the industry."]),
    ]
    _, rows = _check(blocks, "artificial intelligence\t人工智能")
    assert rows == []


def test_multi_word_with_extra_spaces_in_subtitle():
    blocks = [(1000, 2000, ["机器学习很好。", "Machine    learning is great."])]
    _, rows = _check(blocks, "machine learning\t机器学习")
    assert len(rows) == 1 and rows[0].status == "ok"


def test_non_two_line_block_rejected():
    srt = _srt([
        (1000, 2000, ["你好。", "Hello."]),
        (2500, 3500, ["只有一行"]),
    ])
    subs = parse_srt(srt)
    with pytest.raises(ValueError, match=r"第 2 块.*恰好两行"):
        check_subtitles(subs, "hello\t你好")


def test_glossary_order_break_on_tie():
    # 同长度同位置只能是同一匹配；验证词表顺序键至少不报错且两条独立术语正常
    blocks = [(1000, 2000, ["中央处理器与图形处理器。", "CPU and GPU together."])]
    entries, rows = _check(
        blocks, "GPU\t图形处理器\nCPU\t中央处理器"
    )
    assert len(entries) == 2
    assert [r.block for r in rows] == [1, 1]
    assert [r.term_order for r in rows] == [1, 2]


# ---------- 接口 ----------

def _api(blocks, glossary):
    return client.post(
        "/api/termcheck",
        json={"srt": _srt(blocks), "glossary": glossary},
    )


def test_api_termcheck_preview():
    resp = _api(
        BILINGUAL_CHAIN
        + [(5600, 7000, ["这一技术广泛使用。", "Machine learning is widely used."])],
        "artificial intelligence\t人工智能\nmachine learning\t机器学习",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == {
        "total": 3, "ok": 2, "missing": 1, "terms": 2,
    }
    statuses = [(r["block"], r["status"]) for r in data["rows"]]
    assert statuses == [(1, "ok"), (2, "ok"), (3, "missing")]
    miss = data["rows"][2]
    assert miss["en_line"] == "Machine learning is widely used."
    assert miss["translation"] == "机器学习"
    assert miss["term"] == "machine learning"
    assert miss["start_tc"] == "00:00:05,600"


def test_api_termcheck_csv_is_utf8_with_bom():
    resp = client.post(
        "/api/termcheck.csv",
        json={
            "srt": _srt([(1000, 2000, ["图形处理器快。", "GPU is fast."])]),
            "glossary": "GPU\t图形处理器",
        },
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith("﻿".encode("utf-8"))
    text = resp.content.decode("utf-8")
    assert "状态,块号,声明序号" in text
    assert "合格,1,1" in text
    assert "图形处理器" in text


def test_api_termcheck_empty_inputs_400():
    resp = client.post("/api/termcheck", json={"srt": "", "glossary": "a\t甲"})
    assert resp.status_code == 400
    assert "SRT" in resp.json()["detail"]
    resp = client.post("/api/termcheck", json={"srt": "1\n00:00:01,000 --> 00:00:02,000\n甲\na\n", "glossary": ""})
    assert resp.status_code == 400
    assert "词表" in resp.json()["detail"]


def test_api_termcheck_glossary_error_400_points_line():
    srt = _srt([(1000, 2000, ["甲", "a"])])
    for path in ("/api/termcheck", "/api/termcheck.csv"):
        resp = client.post(path, json={"srt": srt, "glossary": "no-tab"})
        assert resp.status_code == 400
        assert "第 1 行缺少 TAB 分隔" in resp.json()["detail"]


def test_api_termcheck_duplicate_term_400():
    srt = _srt([(1000, 2000, ["甲", "a"])])
    resp = client.post(
        "/api/termcheck",
        json={"srt": srt, "glossary": "GPU\t显卡\ngpu\t图形处理器"},
    )
    assert resp.status_code == 400
    assert "第 2 行" in resp.json()["detail"]
    assert "重复" in resp.json()["detail"]


def test_api_termcheck_non_two_line_block_400():
    srt = _srt([(1000, 2000, ["甲", "a"]), (2500, 3500, ["只有一行"])])
    resp = client.post(
        "/api/termcheck",
        json={"srt": srt, "glossary": "a\t甲"},
    )
    assert resp.status_code == 400
    assert "第 2 块" in resp.json()["detail"]
