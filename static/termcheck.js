"use strict";

// 双语术语核对：粘贴每块恰好两行的双语 SRT 与逐行
// 「英文术语<TAB>指定中文译法」词表，复用同一解析器与双语拆轨；
// 英文轨仅跨相邻且间隔 0～500ms 的块拼接，按词边界在归一化文本中找术语，
// 逐块核对中文轨是否采用指定译法，页面预览合格/缺译并下载 UTF-8 CSV。
(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    srt: $("tmSrt"),
    glossary: $("tmGlossary"),
    runBtn: $("tmRunBtn"),
    clearBtn: $("tmClearBtn"),
    status: $("tmStatus"),
    summary: $("tmSummary"),
    resultWrap: $("tmResultWrap"),
    body: $("tmBody"),
    downloadBtn: $("tmDownloadBtn"),
  };

  const SAMPLE_SRT = [
    "1",
    "00:00:01,000 --> 00:00:02,500",
    "今天我们来聊聊人工智能。",
    "Today we talk about artificial",
    "",
    "2",
    "00:00:03,000 --> 00:00:05,000",
    "人工智能正在改变字幕行业。",
    "intelligence in subtitles.",
    "",
    "3",
    "00:00:05,600 --> 00:00:07,500",
    "图形处理器负责加速渲染。",
    "The GPU accelerates rendering.",
    "",
    "4",
    "00:00:07,800 --> 00:00:09,500",
    "算法也被广泛使用。",
    "Machine learning is widely used.",
    "",
    "5",
    "00:00:10,200 --> 00:00:12,000",
    "术语核对就是这么简单。",
    "TERM CHECK IS EASY.",
    "",
  ].join("\n");

  const SAMPLE_GLOSSARY = [
    "artificial intelligence\t人工智能",
    "GPU\t图形处理器",
    "machine learning\t机器学习",
    "term check\t术语核对",
  ].join("\n");

  $("tmSample").addEventListener("click", () => {
    els.srt.value = SAMPLE_SRT;
  });
  $("tmSampleGlossary").addEventListener("click", () => {
    els.glossary.value = SAMPLE_GLOSSARY;
  });

  function setStatus(msg, isError) {
    els.status.textContent = msg;
    els.status.classList.toggle("error", !!isError);
  }

  function clearResult() {
    els.summary.classList.add("hidden");
    els.resultWrap.classList.add("hidden");
    els.body.replaceChildren();
  }

  els.clearBtn.addEventListener("click", () => {
    els.srt.value = "";
    els.glossary.value = "";
    clearResult();
    setStatus("", false);
  });

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  const STATUS_META = {
    ok: { label: "合格", badge: "badge-match" },
    missing: { label: "缺译", badge: "badge-missing_script" },
  };

  function render(data) {
    const s = data.summary;
    $("tmCntOk").textContent = s.ok;
    $("tmCntMissing").textContent = s.missing;
    $("tmCntTotal").textContent = s.total;
    $("tmCntTerms").textContent = s.terms;

    const frag = document.createDocumentFragment();
    for (const r of data.rows) {
      const meta = STATUS_META[r.status];
      const tr = document.createElement("tr");
      tr.className = r.status === "ok" ? "row-term-ok" : "row-term-missing";
      tr.innerHTML = `
        <td><span class="badge ${meta.badge}">${meta.label}</span></td>
        <td class="cell-idx">${r.block}</td>
        <td class="cell-tc">${escapeHtml(r.start_tc)} → ${escapeHtml(r.end_tc)}</td>
        <td class="cell-text">${escapeHtml(r.term)}</td>
        <td class="cell-text">${escapeHtml(r.translation)}</td>
        <td class="cell-text">${escapeHtml(r.en_line)}</td>
        <td class="cell-text">${escapeHtml(r.zh_line)}</td>`;
      frag.appendChild(tr);
    }
    els.body.replaceChildren(frag);
    els.summary.classList.remove("hidden");
    els.resultWrap.classList.remove("hidden");
  }

  let lastRequest = null;

  async function run() {
    lastRequest = { srt: els.srt.value, glossary: els.glossary.value };

    els.runBtn.disabled = true;
    setStatus("核对中……", false);
    try {
      const resp = await fetch("/api/termcheck", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `请求失败（${resp.status}）` }));
        throw new Error(err.detail || `请求失败（${resp.status}）`);
      }
      const data = await resp.json();
      render(data);
      const s = data.summary;
      setStatus(
        `完成：命中核对 ${s.total} 条（合格 ${s.ok}、缺译 ${s.missing}），命中术语 ${s.terms} 个`,
        s.missing > 0
      );
    } catch (e) {
      lastRequest = null;
      clearResult();
      setStatus(e.message, true);
    } finally {
      els.runBtn.disabled = false;
    }
  }

  async function downloadCsv() {
    if (!lastRequest) {
      setStatus("请先执行核对", true);
      return;
    }
    els.downloadBtn.disabled = true;
    try {
      const resp = await fetch("/api/termcheck.csv", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastRequest),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "下载失败" }));
        throw new Error(err.detail || "下载失败");
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "termcheck.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      els.downloadBtn.disabled = false;
    }
  }

  els.runBtn.addEventListener("click", run);
  els.downloadBtn.addEventListener("click", downloadCsv);
})();
