#!/usr/bin/env python3
"""AI 行业新闻摘要生成器"""
import os, json, hashlib, datetime as dt, re
from pathlib import Path
import feedparser, yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.6"  # 每次有新功能时手动 +0.1
ARCHIVE_DIR = ROOT / "docs" / "archive"
CONFIG_PATH = ROOT / "config" / "feeds.yaml"
DATA_PATH   = ROOT / "data" / "articles.json"
OUTPUT_PATH = ROOT / "docs" / "index.html"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS") or 48)
MAX_PER_FEED   = int(os.environ.get("MAX_PER_FEED") or 15)
KEEP_DAYS      = int(os.environ.get("KEEP_DAYS") or 14)

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1",
)
MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"

PROMPT = """你是 AI 行业的主编。下面是今天抓取到的所有新闻列表，请你帮我把它们提炼成一份高质量的《AI 行业每日简报》。

请你按照以下要求输出严格的 JSON 格式，不要输出任何其他内容：
{
  "headline": "今日 AI 行业深度综述，300-400 字。必须按以下结构撰写：① 用一句话点明今日整体态势；② 选取 2-3 个最重要的 5 星事件，用 1-2 句话概括核心事实；③ **重点分析**这些事件背后的共同驱动因素（如资本流向、技术突破、监管压力等），并提炼出对行业未来 1-3 个月的可能影响；④ 最后用一句话给出你对今日动态的整体判断。要求有观点、有洞察，不能只是罗列事件。",
  "must_read": [
    {
      "cluster_id": 0,
      "title": "中文新闻标题。如果原始标题是英文，请翻译成准确、流畅的中文，专业术语保留英文原名（如 GPT、MoE、IPO 等）",
      "category": "主分类 · 子分类。主分类从以下 8 类里选一个：融资并购、产品发布、政策标准、开源动态、技术论文、大厂动态、产业观察、社区热议。子分类用一个 2-6 字的具体词概括事件的核心角度，例如：算力金融、模型发布、IPO、智能体生态、AI 安全、芯片竞争、开源框架、数据合规、算力基建、Agent 治理。格式必须是'主分类 · 子分类'，中间用中文间隔号 · 分隔，例如：'融资并购 · 算力金融'、'产品发布 · 智能体生态'、'政策标准 · AI 安全'。",
      "importance": 5,
      "summary": "80-120字的中文摘要，讲清主体、事实、影响",
      "why": "为何重要（30字以内）",
      "actionable_insight": "落地启发（30字以内，说明对业务/技术的潜在影响）"
    }
  ],
  "briefs": [
    {
      "cluster_id": 1,
      "title": "中文新闻标题。如果原始标题是英文，请翻译成准确、流畅的中文",
      "category": "分类",
      "summary": "一句话简要描述"
    }
  ],
  "trends": [
    {
      "heading": "趋势角度（如：行业大势 / 落地应用启发 / 对国内中小企业的影响）",
      "judgment": "核心判断（一句话，20-30字，用 <strong> 标注关键判断词）",
      "evidence": "证据（2-3句话，引用今日具体事件，用 <mark> 标注关键数据或事件名）",
      "action": "可执行建议（1-2句话，面向企业/开发者/投资者）"
    }
  ]
}

筛选与合并规则（最高优先级！请严格执行）：
1. 输入数据已经按“事件”做了预聚类。每个 cluster_id 代表一个新闻事件分组，分组内可能包含 1 条或多条来自不同来源的新闻。
2. **你只需要输出 cluster_id，不需要输出 sources 数组**。系统会根据 cluster_id 自动填充该事件的所有来源和链接。
3. **绝对禁止编造链接**：`sources` 数组中的 url 必须严格使用输入数据中提供的 `link` 字段，一字不差地复制。如果某条新闻没有链接，不要把它放进 sources。
4. must_read：从所有 cluster 中挑出 5 条最重要的事件（优先 importance 4-5），每条都要有深度分析。
5. briefs：从剩余 cluster 中挑出 5-8 条有代表性的新闻，只需要一句话摘要。
6. trends：根据今日所有新闻，提炼出 2-3 个核心趋势角度，进行深度点评。
7. 务必确保 JSON 格式合法，不要在 JSON 外面加任何解释文字。
8. headline 必须包含对今日事件背后驱动因素的分析。如果今日有多个 5 星事件，请比较它们的异同，指出它们共同指向的行业趋势，并给出你的判断（如“资本正从模型层转向基础设施层”）。
9. 标题一律输出中文。若原标题为英文，请翻译为通顺的中文，专业术语（如 GPT、AI、IPO、GPU）保留英文；若原标题为中文，保持原样。
10. trends 必须结构化输出为三个字段：judgment（核心判断）、evidence（证据）、action（建议）。不要写成大段散文，每一段必须言之有据，避免空话套话。

新闻列表如下：
"""


def load_json(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_json(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_id(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def parse_time(entry):
    for k in ("published_parsed", "updated_parsed"):
        t = entry.get(k)
        if t:
            try:
                return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return None


def fetch_articles(feeds):
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    cutoff = now - dt.timedelta(hours=LOOKBACK_HOURS)
    items = []
    for f in feeds:
        name, url = f["name"], f["url"]
        cat = f.get("category", "综合")
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            print(f"[warn] {name}: {e}"); continue
        if not parsed.entries:
            print(f"[warn] {name}: 0 条"); continue
        n = 0
        for e in parsed.entries:
            if n >= MAX_PER_FEED: break
            link = e.get("link")
            if not link: continue
            pub = parse_time(e)
            if pub and pub < cutoff: continue
            items.append({
                "id": make_id(link),
                "title": (e.get("title") or "").strip(),
                "link": link,
                "source": name,
                "category": cat,
                "raw": (e.get("summary") or e.get("description") or "")[:1200],
                "published": (pub or now).isoformat(),
            })
            n += 1
        print(f"[ok] {name}: {n}")
    return items

def balance_sources(items, per_source=6):
    """对新闻按来源分组，每个源最多保留 per_source 条，避免高频源淹没低频源"""
    from collections import defaultdict
    grouped = defaultdict(list)
    for item in items:
        grouped[item.get("source", "未知")].append(item)
    
    balanced = []
    for source, articles in grouped.items():
        # 按发布时间倒序（如果时间可用），保证取的是最新
        articles.sort(key=lambda x: x.get("published", ""), reverse=True)
        balanced.extend(articles[:per_source])
    
    print(f"[balance] 原始 {len(items)} 条 -> 均衡后 {len(balanced)} 条")
    for source, articles in sorted(grouped.items()):
        print(f"  {source}: {len(articles)} 条 -> 取 {min(len(articles), per_source)} 条")
    return balanced

def extract_keywords(title, body=""):
    """分别提取标题和正文关键词，标题权重更高"""
    def tokenize(text):
        text = text.lower()
        en = re.findall(r'[a-z][a-z0-9]{1,}', text)  # 英文单词
        cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # 中文用二元组（bigram），比单字匹配准得多
        cn = [cn_chars[i] + cn_chars[i+1] for i in range(len(cn_chars)-1)] if len(cn_chars) > 1 else cn_chars
        return set(en + cn)

    stopwords = {'the','and','for','with','that','this','from','are','was','has',
                 '的','了','在','是','和','与','及','等','对','为','从','到','中','以','并','将'}
    title_kw = {w for w in tokenize(title) if w not in stopwords and len(w) >= 2}
    body_kw = {w for w in tokenize(body) if w not in stopwords and len(w) >= 3}
    return title_kw, body_kw


def cluster_articles(articles, threshold=0.08):
    """基于标题为主、正文为辅做聚类"""
    clusters = []
    for article in articles:
        t_kw, b_kw = extract_keywords(
            article.get('title', ''),
            article.get('raw', '')[:300]
        )
        placed = False
        for cluster in clusters:
            # 标题关键词加权（权重 3），正文关键词权重 1
            overlap = 3 * len(t_kw & cluster['title_kw']) + len(b_kw & cluster['body_kw'])
            union = 3 * len(t_kw | cluster['title_kw']) + len(b_kw | cluster['body_kw'])
            if union > 0 and overlap / union > threshold:
                cluster['articles'].append(article)
                cluster['title_kw'] |= t_kw
                cluster['body_kw'] |= b_kw
                placed = True
                break
        if not placed:
            clusters.append({
                'title_kw': t_kw,
                'body_kw': b_kw,
                'articles': [article]
            })
    return clusters

def summarize(items):
    if not items:
        return {}

    # 0. 来源配额：每个源最多贡献 6 条
    items = balance_sources(items, per_source=6)
    
    # 1. 先做预聚类（取前150条，避免过多）
    clusters = cluster_articles(items[:150])
    
    # 2. 把聚类结果打包发给 AI
    payload = []
    for idx, cluster in enumerate(clusters):
        group = []
        for x in cluster['articles']:
            group.append({
                "id": x["id"],
                "title": x["title"],
                "source": x["source"],
                "link": x["link"],
                "content": x["raw"][:200]
            })
        payload.append({"cluster_id": idx, "articles": group})
    
    # === 新增：打印聚类统计，方便排查 ===
    multi = sum(1 for c in clusters if len(c['articles']) > 1)
    print(f"[cluster] 总共 {len(clusters)} 个簇，其中 {multi} 个簇包含多条新闻")
    for idx, c in enumerate(clusters):
        if len(c['articles']) > 1:
            print(f"  cluster {idx}: {len(c['articles'])} 条 -> {[a['source'] for a in c['articles']]}")
    
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是严谨的行业分析师，只输出合法 JSON。"},
                {"role": "user", "content": PROMPT + json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        data = json.loads(r.choices[0].message.content)
        
        # === 后处理：根据 cluster_id 强制填充真实的 sources ===
        for key in ("must_read", "briefs"):
            for item in data.get(key, []):
                cid = item.get("cluster_id")
                if cid is None or not isinstance(cid, int) or cid >= len(clusters):
                    print(f"[debug] 跳过 cluster_id={cid} (type={type(cid).__name__})")
                    continue
                cluster = clusters[cid]
                seen = set()
                sources = []
                for a in cluster["articles"]:
                    src = a.get("source", "")
                    if src in seen:
                        continue
                    seen.add(src)
                    sources.append({"name": src, "url": a.get("link", "")})
                item["sources"] = sources
        return data
    except Exception as e:
        print(f"[warn] LLM summarize error: {e}")
        return {}


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


HEAD = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 行业日报</title>
<style>
:root{--bg:#f5f6f8;--bg2:#ffffff;--ink:#14182b;--muted:#5f6478;--rule:#e4e7ee;--accent:#1d39c4;--accent2:#cf1322;}
*{box-sizing:border-box;}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",sans-serif;font-size:16px;line-height:1.75;-webkit-font-smoothing:antialiased;}
.wrap{max-width:880px;margin:0 auto;padding:0 20px;}
header.masthead{background:linear-gradient(180deg,#101426 0%,#1a2040 100%);color:#fff;padding:42px 0 36px;position:relative;overflow:hidden;}
header.masthead::after{content:"";position:absolute;left:0;bottom:0;height:4px;width:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));}
.masthead .kicker{font-size:0.78rem;letter-spacing:0.28em;text-transform:uppercase;color:#9aa3c7;font-weight:600;margin-bottom:12px;}
.masthead h1{font-size:2rem;line-height:1.25;margin:0 0 10px;font-weight:800;}
.masthead .meta{font-size:0.95rem;color:#c7cde6;display:flex;flex-wrap:wrap;gap:12px;align-items:center;}
.masthead .meta .dot{width:5px;height:5px;border-radius:50%;background:#5b6488;}
main{padding:30px 0;}
.sec-head{display:flex;align-items:baseline;gap:12px;margin:38px 0 20px;}
.sec-head .num{font-size:0.8rem;font-weight:700;color:#fff;background:var(--accent);padding:3px 10px;border-radius:3px;}
.sec-head h2{font-size:1.4rem;margin:0;font-weight:800;}
.sec-head .rule{flex:1;height:1px;background:var(--rule);}
.card{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:22px 24px;margin-bottom:16px;display:grid;grid-template-columns:46px 1fr;gap:18px;transition:box-shadow .2s,transform .2s;}
.card:hover{box-shadow:0 8px 24px rgba(20,24,43,0.08);transform:translateY(-2px);}
.card .rank{font-size:1.6rem;font-weight:800;color:var(--rule);line-height:1;text-align:center;}
.card .topline{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:8px;}
.badge{font-size:0.72rem;font-weight:700;padding:2px 9px;border-radius:20px;color:#fff;white-space:nowrap;background:var(--accent);}
.stars{font-size:0.85rem;color:#f5a623;letter-spacing:1px;margin-left:auto;white-space:nowrap;}
.stars .lbl{color:var(--muted);font-size:0.72rem;margin-right:4px;}
.card h3{font-size:1.15rem;margin:0 0 8px;font-weight:700;line-height:1.5;}
.card p.sum{margin:0 0 10px;color:#2b3147;font-size:0.95rem;}
.card p.sum .why{color:var(--muted);display:block;margin-top:6px;}
.card .land{margin:0 0 12px;font-size:0.9rem;color:var(--accent);background:#f0f3ff;border-left:3px solid var(--accent);padding:8px 12px;border-radius:0 4px 4px 0;line-height:1.65;}
.card .land .land-lbl{font-weight:800;margin-right:6px;}
.card .src{font-size:0.8rem;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;border-top:1px dashed var(--rule);padding-top:10px;}
.card .src a{color:var(--accent);text-decoration:none;font-weight:600;}
.tag{display:inline-block;background:#eef1f8;color:#5f6478;border-radius:4px;padding:2px 8px;font-size:12px;margin-right:6px;margin-top:6px;}
footer{border-top:1px solid var(--rule);padding:24px 0 40px;margin-top:30px;font-size:0.82rem;color:var(--muted);}
</style></head><body>"""

SCRIPT = """<script>
document.querySelectorAll('.filters button').forEach(b=>{
  b.onclick=()=>{
    document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    const c=b.dataset.cat;
    document.querySelectorAll('.card').forEach(el=>{
      el.style.display=(c==='all'||el.dataset.cat===c)?'':'none';
    });
    document.querySelectorAll('.day').forEach(s=>{
      const v=[...s.querySelectorAll('.card')].some(x=>x.style.display!=='none');
      s.style.display=v?'':'none';
    });
  };
});
</script></body></html>"""

def get_badge_class(category):
    """根据分类返回对应的颜色徽章 class"""
    cat = str(category)
    if '融资' in cat or '并购' in cat or 'IPO' in cat or '投资' in cat:
        return 'b-fund'
    if '政策' in cat or '标准' in cat or '监管' in cat or '安全' in cat:
        return 'b-policy'
    if '开源' in cat:
        return 'b-open'
    if '论文' in cat or '学术' in cat or '研究' in cat:
        return 'b-paper'
    if '大厂' in cat:
        return 'b-bigtech'
    if '产业' in cat:
        return 'b-industry'
    if '社区' in cat:
        return 'b-community'
    if '中文' in cat:
        return 'b-cn'
    if '产品' in cat or '发布' in cat:
        return 'b-product'
    return 'b-product'  # 默认蓝色

import math

def get_category_color(cat):
    """根据分类返回颜色，与徽章颜色保持一致"""
    colors = {
        '融资并购': '#0e7d6b',
        '产品发布': '#1d39c4',
        '技术论文': '#6d28d9',
        '开源动态': '#c2410c',
        '政策标准': '#b42318',
        '大厂动态': '#1a2040',
        '产业观察': '#0369a1',
        '社区热议': '#525252',
        '中文': '#a21caf',
    }
    return colors.get(cat, '#6b7280')

def build_donut_svg(top_cats, total_events):
    """生成环形图 SVG"""
    size = 160
    center = size / 2
    radius = 55
    stroke = 22
    C = 2 * math.pi * radius
    
    svg = f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" style="flex-shrink:0;">'
    svg += f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="#eef1f8" stroke-width="{stroke}"/>'
    
    offset = 0
    for cat, count in top_cats:
        pct = count / total_events
        arc = pct * C
        color = get_category_color(cat)
        svg += f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-dasharray="{arc:.2f} {C-arc:.2f}" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {center} {center})"/>'
        offset += arc
    
    svg += f'<text x="{center}" y="{center - 2}" text-anchor="middle" font-size="24" font-weight="800" fill="#14182b">{total_events}</text>'
    svg += f'<text x="{center}" y="{center + 16}" text-anchor="middle" font-size="11" fill="#5f6478">条动态</text>'
    svg += '</svg>'
    return svg

def render(summary_data, now):
    if not isinstance(summary_data, dict):
        return "<h1>今天没有抓取到足够的信息，请稍后重试。</h1>"
    
    must_read = summary_data.get("must_read", [])
    if not isinstance(must_read, list): must_read = []
    
    briefs = summary_data.get("briefs", [])
    if not isinstance(briefs, list): briefs = []
    
    trends = summary_data.get("trends", [])
    if not isinstance(trends, list): trends = []
    
    headline = summary_data.get("headline", "今日无重要动态")

    # 统计星级数量（从这里继续往下，不要出现重复的 must_read = ... 赋值）
    star5 = sum(1 for x in must_read if x.get("importance") == 5)
    star4 = sum(1 for x in must_read if x.get("importance") == 4)
    star3 = len(briefs)
    # 统计各分类的事件数量
    from collections import Counter
    cat_counter = Counter()
    for item in must_read + briefs:
        cat = item.get("category", "综合").split("·")[0].strip()
        cat_counter[cat] += 1
    total_events = sum(cat_counter.values()) or 1
    top_cats = cat_counter.most_common(6)  # 最多显示 6 个分类
    donut_svg = build_donut_svg(top_cats, total_events)
    legend_html = ""
    for cat, count in top_cats:
        pct = count / total_events * 100
        color = get_category_color(cat)
        legend_html += f'<div class="legend-item"><span class="legend-dot" style="background:{color}"></span><span class="legend-name">{cat}</span><span class="legend-pct">{pct:.0f}%</span></div>'

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 行业每日简报 · {now.strftime('%Y-%m-%d')}</title>
<style>
:root{{--bg:#f5f6f8;--bg2:#ffffff;--ink:#14182b;--muted:#5f6478;--rule:#e4e7ee;--accent:#1d39c4;--accent2:#cf1322;}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75;}}
.wrap{{max-width:880px;margin:0 auto;padding:0 20px;}}
header.masthead{{background:linear-gradient(180deg,#101426 0%,#1a2040 100%);color:#fff;padding:40px 0;position:relative;}}
header.masthead::after{{content:"";position:absolute;left:0;bottom:0;height:4px;width:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));}}
.masthead h1{{font-size:2rem;margin:0 0 10px;font-weight:800;}}
.masthead .meta{{font-size:0.95rem;color:#c7cde6;display:flex;gap:12px;flex-wrap:wrap;}}
.masthead .lede{{margin-top:15px;font-size:0.98rem;color:#d7dcf0;border-left:3px solid var(--accent2);padding-left:14px;}}
.version{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:0.78rem;color:#8a92b2;background:rgba(255,255,255,0.06);padding:1px 8px;border-radius:4px;letter-spacing:0.05em;}}
.toolbar{{display:flex;gap:12px;margin-top:16px;align-items:center;}}
.toolbar input{{flex:1;padding:10px 16px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:rgba(255,255,255,0.08);color:#fff;font-size:0.9rem;outline:none;transition:border-color .2s;}}
.toolbar input::placeholder{{color:#8a92b2;}}
.toolbar input:focus{{border-color:var(--accent);}}
.archive-link{{color:#9aa3c7;text-decoration:none;font-size:0.85rem;white-space:nowrap;padding:10px 14px;border:1px solid rgba(255,255,255,0.15);border-radius:8px;transition:all .2s;}}
.archive-link:hover{{color:#fff;border-color:var(--accent);}}
.weekly-link{{color:#9aa3c7;text-decoration:none;font-size:0.85rem;white-space:nowrap;padding:10px 14px;border:1px solid rgba(255,255,255,0.15);border-radius:8px;transition:all .2s;}}
.weekly-link:hover{{color:#fff;border-color:var(--accent);}}
main{{padding:30px 0;}}
.strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px;}}
.strip .cell{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:18px;text-align:center;}}
.strip .cell .n{{font-size:1.8rem;font-weight:800;color:var(--accent);}}
.strip .cell .n.red{{color:var(--accent2);}}
.strip .cell .n.gray{{color:var(--muted);}}
.cat-chart{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:20px 22px;margin-bottom:24px;}}
.chart-title{{font-size:0.85rem;color:var(--muted);font-weight:700;margin-bottom:16px;letter-spacing:0.05em;}}
.chart-body{{display:flex;align-items:center;gap:28px;flex-wrap:wrap;}}
.legend{{flex:1;display:grid;grid-template-columns:repeat(2,1fr);gap:8px 20px;min-width:220px;}}
.legend-item{{display:flex;align-items:center;gap:8px;font-size:0.85rem;}}
.legend-dot{{width:10px;height:10px;border-radius:3px;flex-shrink:0;}}
.legend-name{{color:var(--ink);flex:1;}}
.legend-pct{{color:var(--muted);font-family:ui-monospace,monospace;font-weight:600;}}
.sec-head{{display:flex;align-items:baseline;gap:12px;margin:34px 0 18px;}}
.sec-head .num{{font-size:0.8rem;font-weight:700;color:#fff;background:var(--accent);padding:3px 10px;border-radius:3px;}}
.sec-head h2{{font-size:1.4rem;margin:0;font-weight:800;}}
.card{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:20px;margin-bottom:16px;display:grid;grid-template-columns:46px 1fr;gap:18px;}}
.rank{{font-size:1.6rem;font-weight:800;color:var(--rule);text-align:center;}}
.card h3{{font-size:1.15rem;margin:0 0 8px;}}
.card .land{{margin:10px 0;font-size:0.9rem;color:var(--accent);background:#f0f3ff;border-left:3px solid var(--accent);padding:8px 12px;}}
.card .src{{font-size:0.8rem;color:var(--muted);border-top:1px dashed var(--rule);padding-top:8px;margin-top:10px;}}
.badge{{font-size:0.72rem;font-weight:700;padding:2px 10px;border-radius:20px;letter-spacing:0.02em;color:#fff;white-space:nowrap;}}
.b-fund{{background:#0e7d6b;}}
.b-product{{background:#1d39c4;}}
.b-paper{{background:#6d28d9;}}
.b-open{{background:#c2410c;}}
.b-policy{{background:#b42318;}}
.b-bigtech{{background:#1a2040;}}
.b-industry{{background:#0369a1;}}
.b-community{{background:#525252;}}
.b-cn{{background:#a21caf;}}
.stars{{color:#f5a623 !important;font-size:1.1rem;font-weight:bold;letter-spacing:2px;margin-left:auto;white-space:nowrap;}}
.stars .lbl{{color:var(--muted);font-size:0.72rem;margin-right:4px;font-weight:normal;letter-spacing:normal;}}
.topline{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:8px;}}
.brief{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:14px;margin-bottom:10px;display:flex;gap:12px;}}
.brief .num{{font-weight:800;color:var(--accent);width:26px;}}
.trend{{background:linear-gradient(135deg,#1a2040 0%,#222a52 100%);color:#eef0fa;border-radius:12px;padding:26px;margin-top:20px;}}
.trend h3{{color:#fff;margin:0 0 14px;}}
.trend .angle{{margin-bottom:16px;}}
.trend .angle .h{{font-size:0.8rem;color:#8ea0e8;font-weight:700;margin-bottom:6px;}}
.trend .angle .judgment{{color:#fff;font-size:1.05rem;font-weight:600;margin:8px 0;}}
.trend .angle .evidence{{color:#d7dcf0;font-size:0.95rem;margin:6px 0;}}
.trend .angle .action{{color:#a7f3d0;font-size:0.9rem;margin:6px 0;padding:8px 12px;background:rgba(16,185,129,0.08);border-radius:6px;}}
@media(max-width:640px){{.strip{{grid-template-columns:repeat(2,1fr);}}.card{{grid-template-columns:1fr;}}.rank{{text-align:left;}}}}
</style></head><body>
<header class="masthead"><div class="wrap">
<h1>AI 行业每日简报 · {now.strftime('%Y-%m-%d')}</h1>
<div class="meta"><span>{now.strftime('%Y年%m月%d日')}</span><span>·</span><span>{['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]}</span><span>·</span><span class="version">V{VERSION}</span></div>
<p class="lede">{headline}</p>
<div class="toolbar">
<input id="search" type="text" placeholder="🔍 搜索标题、摘要或来源..." />
<a href="archive/index.html" class="archive-link">📁 历史归档</a>
<a href="weekly/index.html" class="weekly-link">📅 本周回顾</a>
</div>
</div></header>
<main class="wrap">
<div class="strip">
<div class="cell"><div class="n red">{star5}</div><div>5 星事件</div></div>
<div class="cell"><div class="n">{star4}</div><div>4 星事件</div></div>
<div class="cell"><div class="n">{star3}</div><div>3 星事件</div></div>
<div class="cell"><div class="n gray">{len(must_read)+len(briefs)}</div><div>今日简报条数</div></div>
</div>
<div class="cat-chart">
<div class="chart-title">📊 今日分类分布</div>
<div class="chart-body">
{donut_svg}
<div class="legend">{legend_html}</div>
</div>
</div>
<div class="sec-head"><span class="num">01</span><h2>今日必读</h2></div>
"""
    for i, item in enumerate(must_read):
        try:
            imp = int(item.get("importance", 3))
        except:
            imp = 3
        stars = "★" * max(1, min(5, imp))
        
        badge_cls = get_badge_class(item.get('category', '综合'))
        category = item.get('category', '综合')
        
        html += f"""<article class="card"><div class="rank">{i+1:02d}</div><div>
        <div class="topline"><span class="badge {badge_cls}">{category}</span><span class="stars"><span class="lbl">影响</span>{stars}</span></div>
        <h3>{item.get('title','')}</h3>
        <p>{item.get('summary','')}</p>
        <p><strong>为何重要：</strong>{item.get('why','')}</p>
        <p class="land"><strong>落地启发：</strong>{item.get('actionable_insight','')}</p>
        <div class="src">来源：{' / '.join([f'<a href="{s.get("url","#")}" target="_blank">{s.get("name","")}</a>' for s in item.get("sources", [])]) if isinstance(item.get("sources"), list) else f'<a href="{item.get("link","#")}" target="_blank">{item.get("source","")}</a>'}</div>
        </div></article>"""

    html += '<div class="sec-head"><span class="num">02</span><h2>今日简报</h2></div>'
    for i, item in enumerate(briefs):
        html += f"""<div class="brief"><div class="num">{i+1:02d}</div><div>
        <strong>{item.get('title','')}</strong><br>
        {item.get('summary','')} {' / '.join([f'<a href="{s.get("url","#")}" target="_blank">[{s.get("name","")}]</a>' for s in item.get("sources", [])]) if isinstance(item.get("sources"), list) else f'<a href="{item.get("link","#")}" target="_blank">[{item.get("source","")}]</a>'}
        </div></div>"""

    html += '<div class="sec-head"><span class="num">03</span><h2>今日趋势点评</h2></div><div class="trend">'
    for t in trends:
        judgment = t.get('judgment', '').replace('&lt;strong&gt;', '<strong>').replace('&lt;/strong&gt;', '</strong>').replace('&lt;mark&gt;', '<mark>').replace('&lt;/mark&gt;', '</mark>')
        evidence = t.get('evidence', '').replace('&lt;strong&gt;', '<strong>').replace('&lt;/strong&gt;', '</strong>').replace('&lt;mark&gt;', '<mark>').replace('&lt;/mark&gt;', '</mark>')
        action = t.get('action', '').replace('&lt;strong&gt;', '<strong>').replace('&lt;/strong&gt;', '</strong>').replace('&lt;mark&gt;', '<mark>').replace('&lt;/mark&gt;', '</mark>')
        html += f"""<div class="angle">
        <div class="h">{t.get('heading','')}</div>
        <p class="judgment">{judgment}</p>
        <p class="evidence">{evidence}</p>
        <p class="action"><strong>💡 建议：</strong>{action}</p>
        </div>"""
    html += '''</div></main>
<script>
(function(){
  // 搜索过滤
  const input = document.getElementById('search');
  if (input) {
    input.addEventListener('input', function(e) {
      const q = e.target.value.toLowerCase().trim();
      document.querySelectorAll('.card, .brief').forEach(function(el) {
        const match = !q || el.innerText.toLowerCase().includes(q);
        el.style.display = match ? '' : 'none';
      });
    });
  }
  
  // 归档页面动态调整返回链接
  document.querySelectorAll('.archive-link').forEach(function(el) {
    if (window.location.pathname.indexOf('/archive/') !== -1) {
      el.href = 'index.html';
      el.textContent = '📁 归档目录';
    }
  });
})();
</script>
</body></html>'''
    return html

def render_archive_index():
    """生成历史归档索引页"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dates = sorted([f.stem for f in ARCHIVE_DIR.glob("*.html") if f.stem != "index"], reverse=True)
    
    if dates:
        items = "\n".join(
            f'<li><a href="{d}.html"><span class="date">{d}</span><span class="arrow">→</span></a></li>'
            for d in dates
        )
    else:
        items = '<li class="empty">暂无归档</li>'
    
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 行业日报 · 历史归档</title>
<style>
body{{margin:0;background:#f5f6f8;color:#14182b;font-family:"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75;}}
.wrap{{max-width:880px;margin:0 auto;padding:40px 20px;}}
h1{{color:#1a2040;font-size:1.8rem;border-bottom:3px solid #1d39c4;padding-bottom:14px;margin-bottom:24px;}}
.back{{display:inline-block;margin-bottom:20px;color:#1d39c4;text-decoration:none;font-weight:600;}}
.back:hover{{text-decoration:underline;}}
ul{{list-style:none;padding:0;margin:0;}}
li{{background:#fff;border:1px solid #e4e7ee;border-radius:10px;margin-bottom:10px;transition:all .2s;}}
li:hover{{box-shadow:0 6px 20px rgba(20,24,43,0.08);transform:translateY(-2px);}}
li a{{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;color:#1d39c4;text-decoration:none;font-weight:700;font-size:1.05rem;}}
li a:hover{{color:#cf1322;}}
.date{{font-family:ui-monospace,SFMono-Regular,monospace;}}
.arrow{{color:#8a90a6;font-weight:normal;}}
li.empty{{padding:16px 20px;color:#8a90a6;text-align:center;}}
</style></head><body><div class="wrap">
<a class="back" href="../index.html">← 返回今日日报</a>
<h1>📁 历史归档</h1>
<ul>{items}</ul>
</div></body></html>"""
    
    (ARCHIVE_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"[archive] 索引页已更新，共 {len(dates)} 期")

def render_weekly_index():
    """生成最近 7 天的周报回顾页面"""
    WEEKLY_DIR = ROOT / "docs" / "weekly"
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
    
    # 扫描归档目录，取最近 7 天
    archive_dir = ROOT / "docs" / "archive"
    if not archive_dir.exists():
        return
    
    date_files = sorted([f for f in archive_dir.glob("*.html") if f.stem != "index"], reverse=True)[:7]
    
    if not date_files:
        return
    
    # 从每天的 HTML 里提取 headline
    daily_items = []
    for f in date_files:
        try:
            content = f.read_text(encoding="utf-8")
            # 提取 lede 段落里的 headline
            m = re.search(r'class="lede">(.+?)</p>', content, re.DOTALL)
            headline = m.group(1).strip() if m else "（无摘要）"
            # 提取标题里的日期
            date_str = f.stem
            daily_items.append({"date": date_str, "headline": headline})
        except Exception as e:
            print(f"[warn] weekly: {f.name} 解析失败 {e}")
    
    if not daily_items:
        return
    
    items_html = "\n".join(
        f'<div class="day-item"><div class="day-date">{item["date"]}</div><p>{item["headline"]}</p></div>'
        for item in daily_items
    )
    
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 行业周报 · 最近 7 天回顾</title>
<style>
body{{margin:0;background:#f5f6f8;color:#14182b;font-family:"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75;}}
.wrap{{max-width:880px;margin:0 auto;padding:40px 20px;}}
h1{{color:#1a2040;font-size:1.8rem;border-bottom:3px solid #1d39c4;padding-bottom:14px;margin-bottom:24px;}}
.back{{display:inline-block;margin-bottom:20px;color:#1d39c4;text-decoration:none;font-weight:600;}}
.back:hover{{text-decoration:underline;}}
.day-item{{background:#fff;border:1px solid #e4e7ee;border-radius:10px;padding:18px 22px;margin-bottom:14px;transition:all .2s;}}
.day-item:hover{{box-shadow:0 6px 20px rgba(20,24,43,0.08);transform:translateY(-2px);}}
.day-date{{font-family:ui-monospace,SFMono-Regular,monospace;color:#1d39c4;font-weight:800;font-size:1rem;margin-bottom:6px;}}
.day-item p{{margin:0;color:#2b3147;font-size:0.95rem;}}
.note{{color:#8a90a6;font-size:0.82rem;margin-top:20px;text-align:center;}}
</style></head><body><div class="wrap">
<a class="back" href="../index.html">← 返回今日日报</a>
<h1>📅 最近 7 天回顾</h1>
{items_html}
<p class="note">共回顾 {len(daily_items)} 天 · 当前模板版本 V{VERSION}</p>
</div></body></html>"""
    
    (WEEKLY_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"[weekly] 周报已更新，共 {len(daily_items)} 天")
import urllib.request

import smtplib
from email.mime.text import MIMEText
from email.header import Header

def send_email(summary_data, now):
    """通过 QQ 邮箱 SMTP 发送日报摘要邮件"""
    user = os.environ.get("QQ_EMAIL_USER")
    auth_code = os.environ.get("QQ_EMAIL_AUTH_CODE")
    mail_to = os.environ.get("MAIL_TO")
    
    if not user or not auth_code or not mail_to:
        print("[mail] 未配置 QQ_EMAIL_USER / QQ_EMAIL_AUTH_CODE / MAIL_TO，跳过邮件发送")
        return
    
    print(f"[mail] 发件人: {user} 收件人: {mail_to}")
    
    headline = summary_data.get("headline", "今日无重要动态")
    must_read = summary_data.get("must_read", [])
    
    # 组装邮件 HTML
    items_html = ""
    for i, item in enumerate(must_read[:5]):
        items_html += f"""
        <div style="margin-bottom:20px;padding:16px;background:#f8f9fc;border-radius:8px;border-left:3px solid #1d39c4;">
          <h3 style="margin:0 0 8px;font-size:16px;color:#14182b;">{i+1:02d}. {item.get('title','')}</h3>
          <p style="margin:0 0 8px;color:#2b3147;font-size:14px;line-height:1.6;">{item.get('summary','')}</p>
          <p style="margin:0;color:#1d39c4;font-size:13px;"><strong>落地启发：</strong>{item.get('actionable_insight','')}</p>
        </div>
        """
    
    html_body = f"""
    <div style="max-width:680px;margin:0 auto;font-family:'PingFang SC','Microsoft YaHei',sans-serif;">
      <div style="background:linear-gradient(180deg,#101426 0%,#1a2040 100%);color:#fff;padding:32px 24px;border-radius:12px 12px 0 0;">
        <h1 style="margin:0 0 10px;font-size:22px;">AI 行业每日简报 · {now.strftime('%Y-%m-%d')}</h1>
        <p style="margin:0;color:#c7cde6;font-size:14px;">{now.strftime('%Y年%m月%d日')} · {['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]}</p>
      </div>
      <div style="background:#fff;padding:24px;border:1px solid #e4e7ee;border-top:none;">
        <p style="color:#2b3147;font-size:14px;line-height:1.8;margin:0 0 24px;border-left:3px solid #cf1322;padding-left:14px;">{headline}</p>
        <h2 style="font-size:16px;color:#1a2040;border-bottom:2px solid #e4e7ee;padding-bottom:8px;">📌 今日必读</h2>
        {items_html}
        <div style="text-align:center;margin-top:24px;">
          <a href="https://waizai-0237.github.io/ai-daily/" style="display:inline-block;padding:12px 28px;background:#1d39c4;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">查看完整日报 →</a>
        </div>
      </div>
      <div style="text-align:center;padding:20px;color:#8a90a6;font-size:12px;">AI Industry Daily Briefing · V{VERSION}</div>
    </div>
    """
    
    # 构造邮件
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(f"AI 日报 · {now.strftime('%m-%d')} · {headline[:30]}...", "utf-8")
    msg["From"] = user
    msg["To"] = mail_to
    
    try:
        # QQ 邮箱 SMTP 服务器
        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30)
        server.login(user, auth_code)
        server.sendmail(user, [mail_to], msg.as_string())
        server.quit()
        print(f"[mail] 发送成功 → {mail_to}")
    except Exception as e:
        print(f"[mail] 发送失败 {e}")

def main():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    feeds = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["feeds"]

    # 1. 获取当前 RSS 源里所有的新闻（不再过滤历史数据）
    all_articles = fetch_articles(feeds)
    print(f"[info] 总共抓取 {len(all_articles)} 条")

    if not all_articles:
        print("[warn] 今天没有抓取到新闻，跳过生成。")
        return

    # 2. 调用 AI 进行主编级筛选与深度总结（返回的是 must_read / briefs / trends）
    summary_data = summarize(all_articles)

    # 3. 生成今日日报 HTML
    html_content = render(summary_data, now)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html_content, encoding="utf-8")
    
    # 4. 归档副本
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"{now.strftime('%Y-%m-%d')}.html"
    archive_path.write_text(html_content, encoding="utf-8")
    print(f"[archive] 已归档 {archive_path.name}")
    
    # 5. 更新归档索引
    render_archive_index()
    render_weekly_index()
    send_email(summary_data, now)
    print(f"[done] 生成完毕")


if __name__ == "__main__":
    main()
