#!/usr/bin/env python3
"""闲鱼瑞幸礼品卡监控 - 云端扫描脚本(增强版)
运行环境: GitHub Actions (Python 3.11 + Playwright Chromium)
Secrets:
  SCT_SENDKEY  - Server酱 SendKey (必需)
  XY_COOKIE    - 闲鱼 Cookie 字符串 (可选, 提供后使用登录态)

增强点:
  1. 多页抓取: 默认排序前 4 页 + 价格升序前 2 页, 覆盖面比单页大一个数量级
  2. 结构化解析: 标题/售价/面值/卖家, 计算折扣
  3. state.json 跨轮对比: 标记新增/降价/下架
  4. 报告内容更丰富: 各面值行情(最低价/在售数)、折扣分布TOP10、完整礼品卡清单
  5. 诈骗过滤: 含"vx/微信/加好友/v"私聊特征的条目打上 SCAM 标记不计入命中
"""
import json, os, re, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

SCT_KEY = os.environ.get("SCT_SENDKEY", "").strip()
COOKIE = os.environ.get("XY_COOKIE", "").strip()
SEARCH_URL = "https://www.goofish.com/search?q=%E7%91%9E%E5%B9%B8%20%E7%A4%BC%E5%93%81%E5%8D%A1"
DISCOUNT_LINE = 0.8
FACES = [10, 20, 30, 50, 100, 200]
MAX_PAGES_DEFAULT = 4   # 默认排序翻页数
MAX_PAGES_PRICE = 2     # 价格升序翻页数
CST = timezone(timedelta(hours=8))
SCAM_PAT = re.compile(r"(vx|vx号|weixin|微信|加好友|加我|私聊|直接拍价|看头像)", re.I)

def now_str():
    return datetime.now(CST).strftime("%m-%d %H:%M")

def push(title, text):
    if not SCT_KEY:
        print("[WARN] 未配置 SCT_SENDKEY, 跳过推送"); return
    data = urllib.parse.urlencode({"title": title, "desp": text}).encode()
    req = urllib.request.Request(f"https://sctapi.ftqq.com/{SCT_KEY}.send", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("PUSH_OK" if b'"code":0' in r.read() else "PUSH_FAIL")
    except Exception as e:
        print("PUSH_FAIL", e)

EXTRACT_JS = """
() => {
  const items = [];
  document.querySelectorAll('a[href*="item?id="]').forEach(a => {
    const t = (a.innerText || '').trim();
    if (t) items.push({text: t.replace(/\\n/g, ' | '), href: a.href});
  });
  return items;
}
"""

def parse_price(text):
    # 取“价格段”（第二个 | 段附近）里的数字
    parts = text.split('|')
    seg = parts[1] if len(parts) > 1 else text
    m = re.search(r'(\d+(?:\.\d+)?)', seg)
    if m:
        v = float(m.group(1))
        if 0.5 <= v <= 3000:
            return v
    return None

FACE_SET = (10, 20, 30, 50, 100, 200)

def face_value(text):
    """从标题中识别礼品卡面值。
    提取所有“N元/面值N”写法中的候选面值:
    - 唯一面值 → 返回该面值
    - 多个不同面值(多面值合售) → 返回 None, 避免误算折扣"""
    cands = set()
    # 面值N 或 面值N,M,... 列表写法
    for m in re.finditer(r'面值\s*((?:10|20|30|50|100|200)(?:\s*[,，/]\s*(?:10|20|30|50|100|200))*)(?!\d)', text):
        for v in re.findall(r'10|20|30|50|100|200', m.group(1)):
            cands.add(int(v))
    for m in re.finditer(r'(10|20|30|50|100|200)\s*(?:元|块)(?:面值)?', text):
        cands.add(int(m.group(1)))
    cands = {c for c in cands if c in FACE_SET}
    if len(cands) == 1:
        return cands.pop()
    return None

def goto_page(page, n):
    """点击分页按钮跳到第 n 页(n>=1)"""
    if n == 1:
        return True
    for _ in range(3):
        clicked = page.evaluate("""(n) => {
          const els = [...document.querySelectorAll('li,div,span,button,a')]
            .filter(e => e.innerText && e.innerText.trim() === String(n)
                         && e.getBoundingClientRect().top > 300 && e.children.length <= 2);
          if (els.length) { els[0].click(); return true; }
          return false;
        }""", n)
        if clicked:
            time.sleep(4)
            return True
        page.mouse.wheel(0, 3000)
        time.sleep(1)
    return False

def collect_pages(page, max_pages, seen, cards):
    for n in range(1, max_pages + 1):
        if n > 1 and not goto_page(page, n):
            break
        time.sleep(2)
        for it in page.evaluate(EXTRACT_JS):
            if it["href"] in seen:
                continue
            seen.add(it["href"])
            cards.append(it)

def main():
    from playwright.sync_api import sync_playwright
    hits, all_cards = [], []
    stats = {f: [] for f in FACES}
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900}, locale="zh-CN")
        if COOKIE:
            ctx.add_cookies([{"name": k.split("=", 1)[0], "value": k.split("=", 1)[1], "domain": ".goofish.com", "path": "/"}
                             for k in COOKIE.split("; ") if "=" in k])
        page = ctx.new_page()
        page.goto(SEARCH_URL, timeout=60000, wait_until="domcontentloaded")
        time.sleep(8)
        body = page.evaluate("() => document.body.innerText.slice(0, 500)")
        logged_in = "订单" in body and not re.search(r"\n登录\n", body)
        if COOKIE and not logged_in:
            push("⚠️ 闲鱼Cookie已过期", f"{now_str()} 云端检测到登录态失效，行情可能失真，请更新 XY_COOKIE Secret。")

        collect_pages(page, MAX_PAGES_DEFAULT, seen, all_cards)

        # 价格升序再抓 2 页: 直接命中最低价商品
        try:
            page.evaluate("""() => {
              const t = [...document.querySelectorAll('span')]
                .find(e => e.innerText.trim() === '价格' && e.getBoundingClientRect().top < 200);
              if (t) t.click();
            }""")
            time.sleep(5)
            collect_pages(page, MAX_PAGES_PRICE, seen, all_cards)
        except Exception as e:
            print("[WARN] 价格排序失败:", e)
        browser.close()

    # 解析礼品卡
    for it in all_cards:
        text = it["text"]
        low = text.replace(" ", "").replace("｜", "")
        is_card = ("礼品卡" in low or "现金卡" in low or "好运卡" in low) and ("瑞幸" in low or "luckin" in low.lower())
        if not is_card:
            continue
        face = face_value(low)
        price = parse_price(text)
        scam = bool(SCAM_PAT.search(low))
        rec = {"id": it["href"].split("id=")[1].split("&")[0], "title": text[:70],
               "url": it["href"], "price": price, "face": face, "scam": scam}
        if face and price is not None:
            stats[face].append((price, rec))
        all_gift = rec
        if face and price and price / face <= DISCOUNT_LINE and not scam:
            rec["discount"] = price / face
            hits.append(rec)
    # 全部礼品卡记录(带面值和价)
    card_records = []
    for f, lst in stats.items():
        for price, rec in lst:
            r = dict(rec); r["face"] = f; r["discount"] = price / f
            card_records.append(r)

    # 与上轮 state 对比(状态存在 last_report.md 尾部 HTML 注释里, 随报告一起被 workflow 提交持久化)
    def load_state():
        for src in ("state.json", "last_report.md"):
            try:
                txt = open(src, encoding="utf-8").read()
                m = re.search(r'<!--STATE:(\{.*?\})-->', txt, re.S)
                if m:
                    return {x["id"]: x for x in json.loads(m.group(1))}
            except Exception:
                pass
        return {}
    prev = load_state()
    for r in card_records:
        pid = r["id"]
        if pid in prev:
            op = prev[pid].get("price")
            r["status"] = "降价%s→%.2f" % (op, r["price"]) if (op and r["price"] < op) else ("在售" if op == r["price"] else "涨价%s→%.2f" % (op, r["price"]))
        else:
            r["status"] = "新增"
    # 下架检测
    gone = [prev[i] for i in prev if i not in {r["id"] for r in card_records}]
    state_blob = json.dumps([{"id": r["id"], "price": r["price"], "title": r["title"][:50]} for r in card_records], ensure_ascii=False)
    open("state.json", "w", encoding="utf-8").write(state_blob)  # 本地调试用

    # ===== 生成报告 =====
    lines = [f"## 闲鱼瑞幸礼品卡扫描 {now_str()}",
             f"共扫描 {len(seen)} 条商品 | 礼品卡类 {len(card_records)} 条 | 登录态: {'是' if logged_in else '否(行情仅供粗筛参考)'}", ""]
    lines.append("### 各面值行情(最低价/在售条数)")
    for f in FACES:
        lst = stats[f]
        if not lst:
            lines.append(f"- {f}元面值: 无在售")
        else:
            low = min(lst, key=lambda x: x[0])[0]
            lines.append(f"- {f}元面值: 最低 ¥{low:g} | {len(lst)}条在售 | 最低折扣 {low/f*100:.1f}折")
    valid = sorted([r for r in card_records], key=lambda r: r["discount"])
    lines += ["", "### 最低折扣 TOP10（已过滤诈骗标记）"]
    n = 0
    for r in valid:
        if r.get("scam"):
            continue
        n += 1
        lines.append(f"- {r['discount']*100:.1f}折 | {r['face']}元卡 ¥{r['price']:g} | [{r['title'][:30]}]({r['url']}) | {r['status']}")
        if n >= 10:
            break
    if hits:
        lines += ["", "### 🎯 命中(≤8折)"]
        for r in hits:
            lines.append(f"- [{r['face']}元卡 ¥{r['price']:g}（{r['discount']*100:.1f}折）]({r['url']})\n  {r['title']}")
    else:
        lines += ["", "### 🎯 命中(≤8折)", "- 本次无≤8折商品"]
    if gone:
        lines += ["", f"### 上轮在售、本轮未见 ({len(gone)}条)", *[f"- {g['title'][:40]}" for g in gone[:5]]]
    report = "\n".join(lines)
    print(report)

    # 推送: 命中推全量; 无命中推简报
    if hits:
        push("🎯 瑞幸卡低价命中! %d条" % len(hits), report[:2800])
    else:
        brief = f"{now_str()} 扫描{len(seen)}条(礼品卡{len(card_records)}条)，无≤8折命中。\n" + \
                "\n".join(f"{f}元: " + ("无在售" if not stats[f] else f"最低¥{min(x[0] for x in stats[f]):g}") for f in FACES)
        push(f"瑞幸卡扫描简报 {now_str()}", brief)

    with open("last_report.md", "w", encoding="utf-8") as f:
        f.write(report + "\n<!--STATE:" + state_blob + "-->")

if __name__ == "__main__":
    main()
