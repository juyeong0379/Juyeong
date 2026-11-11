/* Daily IB Briefing: archive, research materials, and current data integration. */
let REPORTS = [];

function briefKSTDate() {
  return new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10);
}
function briefDayShift(day, offset) {
  const d = new Date(day + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}
function briefCategory(key, item) {
  if (["ecm","corp","ma","re","infra","hana","peer","sto","productive"].includes(key)) return key;
  const text = [item.headline, item.title, ...(Array.isArray(item.summary) ? item.summary : [])].join(" ");
  if (key === "ipo_ecm") return "ecm";
  if (key === "corp_fin") return /M&A|인수금융|인수합병|사모펀드|경영권|매각|바이아웃|PEF/i.test(text) ? "ma" : "corp";
  if (key === "re_infra") return /데이터센터|해상풍력|신재생|발전소|인프라|전력|민자사업|GPU|교통시설|신디케이트/i.test(text) ? "infra" : "re";
  if (key === "sto_new") return /STO|토큰증권|조각투자|증권형 토큰|디지털자산/i.test(text) ? "sto" : null;
  if (key === "ib_overall") {
    if (/하나증권|하나금융|하나은행|하나캐피탈/i.test(text)) return "hana";
    if (/모험자본|생산적금융|국민성장펀드|정책금융|성장금융/i.test(text)) return "productive";
    if (/증권사|경쟁사|종투사|발행어음|IMA/i.test(text)) return "peer";
  }
  return null;
}
function normalizeBriefing(payload) {
  if (!payload || typeof payload !== "object") return [];
  const date = String(payload.generated_at || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
  const result = [];
  Object.entries(payload.categories || {}).forEach(([section,items]) => {
    if (!Array.isArray(items)) return;
    items.forEach((item,index) => {
      if (!item || typeof item !== "object") return;
      const cat = briefCategory(section,item);
      if (!cat) return;
      const title = String(item.headline || item.title || "").trim();
      const url = String(item.url || "");
      if (!title || !/^https?:\/\//i.test(url)) return;
      let summary = Array.isArray(item.summary) ? item.summary : [item.summary || ""];
      summary = summary.filter(s => typeof s === "string" && s.trim()).slice(0,3);
      result.push({
        id: date + "-" + section + "-" + index, date, cat, title,
        source: String(item.source || "언론사 미상"), url, summary,
        insight: String(item.insight || ""),
        summary_mode: String(item.summary_mode || "ai_summary"),
        pubDate: String(item.pubDate || date),
        kind: String(item.kind || ""),
        pdf: String(item.pdf || "")
      });
    });
  });
  return result;
}
function normalizeReports(payload) {
  if (!payload || typeof payload !== "object") return [];
  const day = String(payload.generated_at || "").slice(0, 10);
  return (Array.isArray(payload.reports) ? payload.reports : [])
    .filter(r => r && r.title && r.url && /^https?:\/\//i.test(r.url))
    .slice(0, 2)
    .map((r, index) => ({
      id: day + "-" + index, briefDate: day,
      title: String(r.title), source: String(r.source || "연구기관"),
      date: String(r.date || ""), url: String(r.url),
      type: String(r.type || "보고서"), pdf: String(r.pdf || ""),
      related_article_urls: Array.isArray(r.related_article_urls) ? r.related_article_urls : []
    }));
}
async function loadBriefings() {
  const now = briefKSTDate();
  const dates = [];
  let currentPayload = null;
  try {
    const resp = await fetch("data.json", { cache: "no-store" });
    if (resp.ok) currentPayload = await resp.json();
  } catch(e) { /* archived data may remain available */ }
  try {
    const resp = await fetch("archive/index.json", { cache: "no-store" });
    if (resp.ok) {
      const list = await resp.json();
      if (Array.isArray(list)) dates.push(...list.filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d)));
    }
  } catch(e) { /* use current briefing if index is unavailable */ }
  const min = briefDayShift(now,-29);
  const valid = [...new Set(dates.filter(d => d >= min && d <= now))];
  let batches = [];
  if (valid.length) {
    batches = await Promise.all(valid.map(async day => {
      try {
        const r=await fetch("archive/" + day + ".json", { cache:"no-store" });
        return r.ok ? (await r.json()) : null;
      } catch(e) { return []; }
    }));
  }
  let merged = batches.filter(Boolean).flatMap(normalizeBriefing);
  let allReports = batches.filter(Boolean).flatMap(normalizeReports);
  if (currentPayload) {
    merged.push(...normalizeBriefing(currentPayload));
    allReports.push(...normalizeReports(currentPayload));
  }
  const reportById = new Map();
  for (const report of allReports) reportById.set(report.briefDate + "|" + report.url, report);
  REPORTS = [...reportById.values()].filter(report => report.briefDate >= min && report.briefDate <= now);
  const byUrl = new Map();
  for (const item of merged) byUrl.set(item.date + "|" + item.url, item);
  NEWS = [...byUrl.values()].filter(item => item.date >= min && item.date <= now);
  const lastDay = NEWS.map(item => item.date).sort().at(-1);
  selectedDate = lastDay || now;
  const dateInput = document.getElementById("dateInput");
  dateInput.min = min;
  dateInput.max = now;
  dateInput.value = selectedDate;
  const status = document.querySelector(".status");
  if (status) {
    const updated = currentPayload && currentPayload.generated_at;
    status.innerHTML = '<i></i>' + (updated ? "마지막 업데이트 <strong>" + escapeHTML(updated) + "</strong>" : "수집 데이터 없음");
  }
  if (!NEWS.length) {
    const target=document.getElementById("stories");
    if(target) target.innerHTML='<div class="empty">아직 등록된 뉴스가 없습니다. 다음 자동 수집 결과를 확인해 주세요.</div>';
  }
  render();
  renderOfficial();
}