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
MAX_PAGES_NEWEST = 2    # 新发布排序翻页数(优先抓新挂出的卡)
MAX_PAGES_PRICE = 2     # 价格升序翻页数
CST = timezone(timedelta(hours=8))
SCAM_PAT = re.compile(r"(加好友|加我好友|看头像|直接拍价|私下交易|链接私信)", re.I)

def now_str():
    return datetime.now(CST).strftime("%m-%d %H:%M")

def trim_lines(text, limit):
    """按行截断到 limit 字符内, 避免表格行被切一半"""
    out, n = [], 0
    for line in text.split("\n"):
        if n + len(line) + 1 > limit:
            break
        out.append(line)
        n += len(line) + 1
    return "\n".join(out)

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
    # 价格紧跟在 ¥ 后面，但可能被竖线拆成多个 token（如 "¥ | 18 | .90"）
    seg = re.search(r'¥([^¥]{0,30})', text)
    if seg:
        tokens = [x.strip() for x in seg.group(1).split('|')]
        int_part = dec_part = None
        for tok in tokens:
            if int_part is None:
                if re.fullmatch(r'\d+', tok):
                    int_part = tok
                elif re.fullmatch(r'\d+\.\d+', tok):
                    v = float(tok)
                    return v if 0.5 <= v <= 3000 else None
            elif re.fullmatch(r'\.\d+', tok):
                dec_part = tok[1:]
                break
            elif tok:
                break
        if int_part:
            v = float(int_part + ('.' + dec_part if dec_part else ''))
            if 0.5 <= v <= 3000:
                return v
    # 兜底：任意段里的独立小数价格（如 9.9）
    for p in text.split('|'):
        m = re.fullmatch(r'\s*(\d+\.\d+)\s*', p)
        if m:
            v = float(m.group(1))
            if 0.5 <= v <= 3000:
                return v
    return None

FACE_SET = (10, 20, 30, 50, 100, 200)

def face_value(text):
    """从标题中识别礼品卡面值，覆盖常见写法：
    面值N / N元面值 / N元礼品卡 / N礼品卡 / 礼品卡N / 礼品卡 N两张 / 礼品券，N元
    多个不同面值(多面值合售) → 返回 None, 避免误算折扣"""
    cands = set()
    text = re.sub(r'\d+(?:\.\d+)?折', '', text)  # 剔除折扣数字，避免"面值93折"混入
    # 面值N 或 面值N,M,... 列表写法
    for m in re.finditer(r'面值\s*((?:\d+(?:\.\d+)?)(?:\s*[,，/]\s*\d+(?:\.\d+)?)*)', text):
        for v in re.findall(r'\d+(?:\.\d+)?', m.group(1)):
            cands.add(float(v))
    # N元面值 / N面值 / N面额
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*面[值额]', text):
        cands.add(float(m.group(1)))
    # N元礼品卡/现金卡/礼品券（数字在前）
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*元?\s*(?:礼品卡|现金卡|好运卡)', text):
        cands.add(float(m.group(1)))
    # 礼品卡N / 礼品卡 N两张（"两张"是数量）
    m = re.search(r'(?:礼品卡|现金卡|好运卡)\s*(\d+(?:\.\d+)?)(?!\.?\d*折)', text)
    if m:
        cands.add(float(m.group(1)))
    # 礼品卡/礼品券 后紧邻的 "N元"（不允许跨过另一个N元，避免把多张打包的总额混入）
    m = re.search(r'(?:礼品卡|现金卡|好运卡|礼品券)\s*(\d+(?:\.\d+)?)\s*元', text)
    if m:
        cands.add(float(m.group(1)))
    # 多张打包优先判定：有“N张”时用总面值（共N元/打包N元优先，否则单价×张数）
    m_qty = re.search(r'(\d+|两|二|三|四|五|六|七|八|九|十)\s*张', text)
    if m_qty:
        cn = {'两':2,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
        q = m_qty.group(1)
        qty = cn.get(q) or int(q)
        if qty > 1:
            m_total = re.search(r'(?:共|合计|打包)\s*(\d+(?:\.\d+)?)\s*元', text)
            if m_total:
                v = float(m_total.group(1))
                return v if 5 <= v <= 2000 else None
            base = [c for c in cands if 5 <= c <= 500]
            if len(base) == 1:
                return base[0] * qty
            return None
        return None  # “1张/单张”等表述不改变逻辑，走下面的常规判定
    # 相邻多面值（如“10元20元”“100元300元两种”）→ 歧义，跳过
    vals = set(re.findall(r'(\d+(?:\.\d+)?)\s*元', text))
    if len(vals) > 1:
        return None
    cands = {c for c in cands if 5 <= c <= 500}
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
            # 每天(北京时间)最多提醒一次, 避免每15分钟轰炸
            today = datetime.now(CST).strftime("%Y-%m-%d")
            last_warn = ""
            if os.path.exists("cookie_warn_date.txt"):
                last_warn = open("cookie_warn_date.txt", encoding="utf-8").read().strip()
            if last_warn != today:
                push("⚠️ 闲鱼Cookie已过期", f"{now_str()} 云端检测到登录态失效，行情可能失真，请重新导出 Cookie 并更新 XY_COOKIE Secret。")
                open("cookie_warn_date.txt", "w", encoding="utf-8").write(today)

        collect_pages(page, MAX_PAGES_DEFAULT, seen, all_cards)

        # 新发布排序再抓几页: 优先看到刚挂出的卡
        try:
            page.evaluate("""() => {
              const t = [...document.querySelectorAll('span')]
                .find(e => e.innerText.trim() === '新发布' && e.getBoundingClientRect().top < 200);
              if (t) t.click();
            }""")
            time.sleep(5)
            collect_pages(page, MAX_PAGES_NEWEST, seen, all_cards)
        except Exception as e:
            print("[WARN] 新发布排序失败:", e)

        # 价格升序再抓几页: 直接命中最低价商品
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

    # 解析礼品卡(按商品id去重, 同一商品只计一次)
    seen_ids = set()
    all_card_recs = []   # 所有识别为礼品卡的商品(含无面值)
    for it in all_cards:
        text = it["text"]
        low = text.replace(" ", "").replace("｜", "")
        is_card = ("礼品卡" in low or "现金卡" in low or "好运卡" in low) and ("瑞幸" in low or "luckin" in low.lower())
        if not is_card:
            continue
        pid = it["href"].split("id=")[1].split("&")[0]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        face = face_value(low)
        price = parse_price(text)
        scam = bool(SCAM_PAT.search(low))
        rec = {"id": pid, "title": text[:70],
               "url": it["href"], "price": price, "face": face, "scam": scam}
        all_card_recs.append(rec)
        if face and price is not None:
            stats.setdefault(face, []).append((price, rec))
        if face and price and 0.5 <= price / face <= DISCOUNT_LINE and not scam:
            rec["discount"] = price / face
            hits.append(rec)
    # 全部礼品卡记录(带面值和价)
    card_records = []
    for f, lst in stats.items():
        for price, rec in lst:
            r = dict(rec); r["face"] = f; r["discount"] = price / f
            card_records.append(r)
    # 面值/价格缺失的卡也纳入展示与状态对比
    priced_ids = {r["id"] for r in card_records}
    for rec in all_card_recs:
        if rec["id"] not in priced_ids:
            card_records.append(dict(rec))

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
            if op is None or r["price"] is None:
                r["status"] = "在售"
            elif r["price"] < op:
                r["status"] = "降价%s→%.2f" % (op, r["price"])
            elif r["price"] == op:
                r["status"] = "在售"
            else:
                r["status"] = "涨价%s→%.2f" % (op, r["price"])
        else:
            r["status"] = "新增"
    # 下架检测
    gone = [prev[i] for i in prev if i not in {r["id"] for r in all_card_recs}]
    state_blob = json.dumps([{"id": r["id"], "price": r["price"], "title": r["title"][:50]} for r in all_card_recs], ensure_ascii=False)
    open("state.json", "w", encoding="utf-8").write(state_blob)  # 本地调试用

    # ===== 生成报告 =====
    lines = [f"## 闲鱼瑞幸礼品卡扫描 {now_str()}",
             f"共扫描 {len(seen)} 条商品 | 礼品卡类 {len(all_card_recs)} 条(可定价{len(priced_ids)}条) | 登录态: {'是' if logged_in else '否(行情仅供粗筛参考)'}", ""]
    lines.append("### 各面值行情(最低价/在售条数)")
    faces_show = FACES + sorted(set(stats) - set(FACES))
    for f in faces_show:
        lst = stats.get(f, [])
        if not lst:
            lines.append(f"- {f:g}元面值: 无在售")
        else:
            low = min(lst, key=lambda x: x[0])[0]
            lines.append(f"- {f:g}元面值: 最低 ¥{low:g} | {len(lst)}条在售 | 最低折扣 {low/f*10:.1f}折")
    valid = sorted([r for r in card_records if "discount" in r and r["discount"] >= 0.5], key=lambda r: r["discount"])
    lines += ["", "### 最低折扣 TOP10（已过滤诈骗标记）"]
    n = 0
    for r in valid:
        if r.get("scam"):
            continue
        n += 1
        lines.append(f"- {r['discount']*10:.1f}折 | {r['face']}元卡 ¥{r['price']:g} | [{r['title'][:30]}]({r['url']}) | {r['status']}")
        if n >= 10:
            break
    if hits:
        lines += ["", "### 🎯 命中(≤8折)"]
        for r in hits:
            lines.append(f"- [{r['face']}元卡 ¥{r['price']:g}（{r['discount']*10:.1f}折）]({r['url']})\n  {r['title']}")
    else:
        lines += ["", "### 🎯 命中(≤8折)", "- 本次无≤8折商品"]
    if gone:
        lines += ["", f"### 上轮在售、本轮未见 ({len(gone)}条)", *[f"- {g['title'][:40]}" for g in gone[:5]]]
    report = "\n".join(lines)
    print(report)

    # 推送(Server酱 Markdown 表格样式)
    def md_table(rows, header):
        out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        out.extend(rows)
        return "\n".join(out)

    def row_of(r):
        if r.get("scam"):
            mark = "🔴疑似诈骗"
        elif r.get("status") == "新增":
            mark = "🆕新增"
        elif r.get("status") == "在售":
            mark = "持平"
        else:
            mark = "⬇️降价"
        return "| %s | [%s](%s) | %s元 | ¥%s | %.1f折 |" % (mark, r["title"][:20], r["url"], r["face"], r["price"], r["discount"]*10)

    top_rows = [row_of(r) for r in valid if not r.get("scam")][:15]
    table = md_table(top_rows, ["标记", "标题", "面值", "售价", "折扣"]) if top_rows else "- 暂无在售礼品卡"
    header_info = ("检查时间: %s；结果: %s（扫描%d条，礼品卡/现金卡类%d条在售，登录态: %s）"
                   % (now_str(), ("⚠️发现%d条≤8折商品" % len(hits)) if hits else "无≤8折商品", len(seen), len(card_records), "是" if logged_in else "否"))
    if hits:
        hit_rows = [row_of(r) for r in hits]
        body = header_info + "\n\n### 🎯 命中(≤8折)\n" + md_table(hit_rows, ["标记", "标题", "面值", "售价", "折扣"]) + "\n\n### 在售最低折扣TOP15\n" + table
        push("🎯 瑞幸卡低价命中! %d条" % len(hits), trim_lines(body, 2800))
    else:
        body = header_info + "\n\n### 在售最低折扣TOP15\n" + table
        push("瑞幸卡扫描简报 %s" % now_str(), trim_lines(body, 2800))

    with open("last_report.md", "w", encoding="utf-8") as f:
        f.write(report + "\n<!--STATE:" + state_blob + "-->")

if __name__ == "__main__":
    main()
