#!/usr/bin/env python3
"""AI 行业新闻摘要生成器"""
import os, json, hashlib, datetime as dt
from pathlib import Path
import feedparser, yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "feeds.yaml"
DATA_PATH   = ROOT / "data" / "articles.json"
OUTPUT_PATH = ROOT / "docs" / "index.html"

LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS") or 36)
MAX_PER_FEED   = int(os.environ.get("MAX_PER_FEED") or 8)
KEEP_DAYS      = int(os.environ.get("KEEP_DAYS") or 14)

client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1",
)
MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"

PROMPT = """你是 AI 行业分析师。下面是若干条新闻，请为每条生成中文摘要。

严格按以下 JSON 格式输出，不要输出任何其他内容：
{"articles":[{"id":"...","summary":"...","importance":3,"tags":["..."],"why":"..."}]}

字段说明：
- id：原样返回输入 id
- summary：80-120 字中文摘要，讲清主体、事实、影响
- importance：1-5 整数，1=一般，3=值得关注，5=行业重大
- tags：2-4 个中文关键词
- why：30 字以内，说明为什么值得关注

新闻列表：
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


def summarize(items):
    out = {}
    for i in range(0, len(items), 10):
        batch = items[i:i+10]
        payload = [{"id": x["id"], "title": x["title"],
                    "source": x["source"], "content": x["raw"]} for x in batch]
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
            for a in data.get("articles", []):
                if isinstance(a, dict) and "id" in a:
                    out[a["id"]] = a
        except Exception as e:
            print(f"[warn] LLM batch {i}: {e}")
    return out


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


HEAD = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 行业日报</title>
<style>
body{margin:0;background:#f5f6f8;color:#14182b;font-family:"Microsoft YaHei",sans-serif;line-height:1.7}
header{background:#1a2040;color:white;padding:40px 20px}
header .content{max-width:850px;margin:0 auto}
header h1{margin:0 0 10px;font-size:24px}
header p{margin:8px 0;color:#d7dcf0;font-size:14px}
main{max-width:850px;margin:30px auto;padding:0 20px}
h2{font-size:18px;color:#1a2040;margin:24px 0 16px;border-bottom:2px solid #e4e7ee;padding-bottom:8px}
.card{background:white;border:1px solid #e4e7ee;border-radius:10px;padding:22px;margin-bottom:18px;transition:transform .2s ease, box-shadow .2s ease}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(26,32,64,0.08)}
.meta{color:#5f6478;font-size:14px;margin-bottom:8px}
.stars{color:#f5a623;letter-spacing:2px}
.card h3{margin:8px 0 12px;font-size:17px;color:#14182b}
.card h3 a{color:#14182b;text-decoration:none}
.card h3 a:hover{color:#1d39c4}
.card p{margin:8px 0}
.important{color:#5f6478;background:#f9f9fb;padding:8px 12px;border-radius:6px;font-size:14px}
.insight{padding:10px 14px;background:#f0f3ff;border-left:3px solid #1d39c4;font-size:14px;color:#1a2040}
.src{font-size:13px;color:#5f6478}
.src a{color:#1d39c4;text-decoration:none}
.tag{display:inline-block;background:#eef1f8;color:#5f6478;border-radius:4px;padding:2px 8px;font-size:12px;margin-right:6px;margin-top:8px}
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


def render(articles, now):
    articles = sorted(articles, key=lambda x: x.get("published",""), reverse=True)
    groups, cats = {}, sorted({a.get("category","综合") for a in articles})
    for a in articles:
        try:
            d = dt.datetime.fromisoformat(a["published"]).astimezone()
        except Exception:
            d = now
        groups.setdefault(d.strftime("%Y-%m-%d"), []).append(a)

    weekday = ['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]
    
    p = [HEAD, f"""<header>
<div class="content">
<h1>AI 行业每日简报 · {now.strftime('%Y-%m-%d')}</h1>
<p>{weekday}</p>
<p>今日共收录 {len(articles)} 条行业动态，涵盖 {', '.join(cats)} 等领域。</p>
</div>
</header><main>"""]

    for day, items in sorted(groups.items(), reverse=True):
        p.append(f'<h2>{day} 必读</h2>')
        for a in items:
            imp = max(1, min(5, int(a.get("importance") or 3)))
            stars = "★" * imp
            tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in (a.get("tags") or []))
            p.append(f"""<article class="card">
<div class="meta">{esc(a.get('category','综合'))} · <span class="stars">{stars}</span></div>
<h3><a href="{esc(a['link'])}" target="_blank" rel="noopener">{esc(a.get('title',''))}</a></h3>
<p>{esc(a.get('summary',''))}</p>
<p class="important"><strong>为何重要：</strong>{esc(a.get('why',''))}</p>
<p class="insight"><strong>落地启发：</strong>关注该动态对业务或技术栈的潜在影响。</p>
<div>{tags}</div>
<p class="src" style="margin-top:12px;">来源：<a href="{esc(a['link'])}" target="_blank" rel="noopener">{esc(a.get('source',''))}</a></p>
</article>""")
    p.append("</main>")
    return "\n".join(p)


def main():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    feeds = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["feeds"]
    store = load_json(DATA_PATH, {})

    fresh = [x for x in fetch_articles(feeds) if x["id"] not in store]
    print(f"[info] 新增 {len(fresh)} 条")

    if fresh:
        for aid, s in summarize(fresh).items():
            for x in fresh:
                if x["id"] == aid:
                    x.update({k: s.get(k) for k in ("summary","importance","tags","why")})
                    break

    for x in fresh:
        store[x["id"]] = x

    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
    store = {k: v for k, v in store.items() if v.get("published","") >= cutoff}

    save_json(DATA_PATH, store)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(list(store.values()), now), encoding="utf-8")
    print(f"[done] 输出 {len(store)} 条")


if __name__ == "__main__":
    main()
