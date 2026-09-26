#!/usr/bin/env python3
"""AI 行业新闻摘要生成器"""
import os, json, hashlib, datetime as dt, re
from pathlib import Path
import feedparser, yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
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
  "headline": "今日 AI 行业的一句话核心总结，不超过 80 字",
  "must_read": [
    {
      "cluster_id": 0,
      "title": "新闻标题",
      "category": "分类（如融资并购/产品发布/政策标准等）",
      "importance": 5,
      "summary": "80-120字的中文摘要，讲清主体、事实、影响",
      "why": "为何重要（30字以内）",
      "actionable_insight": "落地启发（30字以内，说明对业务/技术的潜在影响）"
    }
  ],
  "briefs": [
    {
      "cluster_id": 1,
      "title": "新闻标题",
      "category": "分类",
      "summary": "一句话简要描述"
    }
  ],
  "trends": [
    {
      "heading": "趋势角度（如：行业大势 / 落地应用启发 / 对国内中小企业的影响）",
      "content": "200字左右的深度点评，分析今日事件与近期动态的关联，用 <strong> 和 <mark> 标签标注重点句子。"
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
                    continue
                cluster = clusters[cid]
                item["sources"] = [
                    {"name": a.get("source", ""), "url": a.get("link", "")}
                    for a in cluster["articles"]
                ]
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
main{{padding:30px 0;}}
.strip{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:24px;}}
.strip .cell{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:18px;text-align:center;}}
.strip .cell .n{{font-size:1.8rem;font-weight:800;color:var(--accent);}}
.strip .cell .n.red{{color:var(--accent2);}}
.strip .cell .n.gray{{color:var(--muted);}}
.sec-head{{display:flex;align-items:baseline;gap:12px;margin:34px 0 18px;}}
.sec-head .num{{font-size:0.8rem;font-weight:700;color:#fff;background:var(--accent);padding:3px 10px;border-radius:3px;}}
.sec-head h2{{font-size:1.4rem;margin:0;font-weight:800;}}
.card{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:20px;margin-bottom:16px;display:grid;grid-template-columns:46px 1fr;gap:18px;}}
.rank{{font-size:1.6rem;font-weight:800;color:var(--rule);text-align:center;}}
.card h3{{font-size:1.15rem;margin:0 0 8px;}}
.card .land{{margin:10px 0;font-size:0.9rem;color:var(--accent);background:#f0f3ff;border-left:3px solid var(--accent);padding:8px 12px;}}
.card .src{{font-size:0.8rem;color:var(--muted);border-top:1px dashed var(--rule);padding-top:8px;margin-top:10px;}}
.stars{{color:#f5a623 !important;font-size:1.1rem;font-weight:bold;letter-spacing:2px;margin-left:auto;white-space:nowrap;}}
.brief{{background:var(--bg2);border:1px solid var(--rule);border-radius:10px;padding:14px;margin-bottom:10px;display:flex;gap:12px;}}
.brief .num{{font-weight:800;color:var(--accent);width:26px;}}
.trend{{background:linear-gradient(135deg,#1a2040 0%,#222a52 100%);color:#eef0fa;border-radius:12px;padding:26px;margin-top:20px;}}
.trend h3{{color:#fff;margin:0 0 14px;}}
.trend .angle{{margin-bottom:16px;}}
.trend .angle .h{{font-size:0.8rem;color:#8ea0e8;font-weight:700;margin-bottom:6px;}}
@media(max-width:640px){{.strip{{grid-template-columns:repeat(2,1fr);}}.card{{grid-template-columns:1fr;}}.rank{{text-align:left;}}}}
</style></head><body>
<header class="masthead"><div class="wrap">
<h1>AI 行业每日简报 · {now.strftime('%Y-%m-%d')}</h1>
<div class="meta"><span>{now.strftime('%Y年%m月%d日')}</span><span>·</span><span>{['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]}</span></div>
<p class="lede">{headline}</p>
</div></header>
<main class="wrap">
<div class="strip">
<div class="cell"><div class="n red">{star5}</div><div>5 星事件</div></div>
<div class="cell"><div class="n">{star4}</div><div>4 星事件</div></div>
<div class="cell"><div class="n">{star3}</div><div>3 星事件</div></div>
<div class="cell"><div class="n gray">{len(must_read)+len(briefs)}</div><div>今日简报条数</div></div>
</div>
<div class="sec-head"><span class="num">01</span><h2>今日必读</h2></div>
"""
    for i, item in enumerate(must_read):
        try:
            imp = int(item.get("importance", 3))
        except:
            imp = 3
        stars = "★" * max(1, min(5, imp))
        
        html += f"""<article class="card"><div class="rank">{i+1:02d}</div><div>
        <h3>{item.get('title','')}</h3>
        <p>{item.get('summary','')}</p>
        <p><strong>为何重要：</strong>{item.get('why','')}</p>
        <p class="land"><strong>落地启发：</strong>{item.get('actionable_insight','')}</p>
        <div class="src">来源：{' '.join([f'<a href="{s.get("url","#")}" target="_blank">{s.get("name","")}</a>' for s in item.get("sources", [])]) if isinstance(item.get("sources"), list) else f'<a href="{item.get("link","#")}" target="_blank">{item.get("source","")}</a>'} | 影响：<span class="stars">{stars}</span></div>
        </div></article>"""

    html += '<div class="sec-head"><span class="num">02</span><h2>今日简报</h2></div>'
    for i, item in enumerate(briefs):
        html += f"""<div class="brief"><div class="num">{i+1:02d}</div><div>
        <strong>{item.get('title','')}</strong><br>
        {item.get('summary','')} {' '.join([f'<a href="{s.get("url","#")}" target="_blank">[{s.get("name","")}]</a>' for s in item.get("sources", [])]) if isinstance(item.get("sources"), list) else f'<a href="{item.get("link","#")}" target="_blank">[{item.get("source","")}]</a>'}
        </div></div>"""

    html += '<div class="sec-head"><span class="num">03</span><h2>今日趋势点评</h2></div><div class="trend">'
    for t in trends:
        content = t.get('content', '').replace('&lt;strong&gt;', '<strong>').replace('&lt;/strong&gt;', '</strong>').replace('&lt;mark&gt;', '<mark>').replace('&lt;/mark&gt;', '</mark>')
        html += f"""<div class="angle"><div class="h">{t.get('heading','')}</div><p>{content}</p></div>"""
    html += '</div></main></body></html>'
    return html


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

    # 3. 直接生成 HTML 并写入 docs 文件夹
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(summary_data, now), encoding="utf-8")
    print(f"[done] 生成完毕，写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
