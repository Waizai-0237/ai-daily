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
:root{--bg:#0b0f19;--card-bg:rgba(22,28,45,0.7);--fg:#f8fafc;--muted:#94a3b8;--line:rgba(255,255,255,0.08);--acc:#38bdf8;--acc-purple:#8b5cf6}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(135deg, #0b0f19 0%, #111827 100%);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}
header{padding:24px 16px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(11,15,25,0.8);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);z-index:9}
h1{margin:0 0 6px;font-size:22px;background:linear-gradient(90deg, #38bdf8, #8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-weight:700;letter-spacing:0.5px}
.meta{margin:0 0 16px;color:var(--muted);font-size:12px;font-family:ui-monospace,SFMono-Regular,monospace}
.filters{display:flex;gap:8px;overflow-x:auto;padding-bottom:8px;scrollbar-width:none}
.filters::-webkit-scrollbar{display:none}
.filters button{flex:0 0 auto;background:rgba(30,41,59,0.6);color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:6px 16px;font-size:13px;transition:all .3s ease}
.filters button.active{background:linear-gradient(90deg, #3b82f6, #8b5cf6);color:#fff;border-color:transparent;box-shadow:0 2px 12px rgba(139,92,246,0.3)}
main{padding:16px 12px 80px;max-width:760px;margin:0 auto}
.day h2{font-size:13px;color:var(--acc);font-weight:600;margin:24px 4px 12px;letter-spacing:1px;text-transform:uppercase}
.card{position:relative;background:var(--card-bg);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);transition:transform .2s ease, box-shadow .2s ease;overflow:hidden}
.card:hover{transform:translateY(-3px);box-shadow:0 10px 30px -10px rgba(56,189,248,0.15);border-color:rgba(56,189,248,0.3)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg, transparent, rgba(56,189,248,0.5), transparent);opacity:0;transition:opacity .3s ease}
.card:hover::before{opacity:1}
.card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.cat{font-size:11px;color:var(--acc);background:rgba(56,189,248,0.1);border:1px solid rgba(56,189,248,0.2);border-radius:4px;padding:2px 8px;font-weight:500}
.imp{color:#fbbf24;font-size:12px;letter-spacing:2px}
.card h3{margin:0 0 10px;font-size:16px;line-height:1.45;font-weight:600}
.card h3 a{color:var(--fg);text-decoration:none;transition:color .2s ease}
.card h3 a:hover{color:var(--acc)}
.summary{margin:0 0 12px;color:#cbd5e1;font-size:14px;line-height:1.65}
.why{margin:0 0 14px;font-size:13px;color:#a7f3d0;background:rgba(16,185,129,0.08);padding:8px 12px;border-radius:8px;border-left:2px solid #10b981}
.card-foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:11px}
.src{color:var(--muted);margin-right:auto;font-family:ui-monospace,SFMono-Regular,monospace}
.tag{background:rgba(139,92,246,0.1);color:#c4b5fd;border:1px solid rgba(139,92,246,0.2);border-radius:4px;padding:2px 8px}
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

    p = [HEAD, f"""<header>
<h1>AI 行业日报</h1>
<p class="meta">更新于 {now.strftime('%Y-%m-%d %H:%M')} · 共 {len(articles)} 条 · 保留 {KEEP_DAYS} 天</p>
<div class="filters"><button class="active" data-cat="all">全部</button>
{''.join(f'<button data-cat="{esc(c)}">{esc(c)}</button>' for c in cats)}</div>
</header><main>"""]

    for day, items in sorted(groups.items(), reverse=True):
        p.append(f'<section class="day"><h2>{day}</h2>')
        for a in items:
            imp = max(1, min(5, int(a.get("importance") or 3)))
            stars = "★"*imp + "☆"*(5-imp)
            tags = "".join(f'<span class="tag">{esc(t)}</span>' for t in (a.get("tags") or []))
            p.append(f"""<article class="card" data-cat="{esc(a.get('category','综合'))}">
<div class="card-head"><span class="cat">{esc(a.get('category','综合'))}</span><span class="imp">{stars}</span></div>
<h3><a href="{esc(a['link'])}" target="_blank" rel="noopener">{esc(a.get('title',''))}</a></h3>
<p class="summary">{esc(a.get('summary',''))}</p>
<p class="why">💡 {esc(a.get('why',''))}</p>
<div class="card-foot"><span class="src">{esc(a.get('source',''))}</span>{tags}</div>
</article>""")
        p.append("</section>")
    p.append("</main>"); p.append(SCRIPT)
    return "\n".join(p)


def main():
    now = dt.datetime.now(dt.timezone.utc).astimezone()
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
