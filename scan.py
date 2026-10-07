#!/usr/bin/env python3
"""闲鱼瑞幸礼品卡监控 - 云端扫描脚本(无登录态粗筛)
运行环境: GitHub Actions (Python 3.11 + Playwright Chromium)
Secrets:
  SCT_SENDKEY  - Server酱 SendKey (必需)
  XY_COOKIE    - 闲鱼 Cookie 字符串 (可选, 提供后使用登录态)
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

SCT_KEY = os.environ.get("SCT_SENDKEY", "").strip()
COOKIE = os.environ.get("XY_COOKIE", "").strip()
SEARCH_URL = "https://www.goofish.com/search?q=%E7%91%9E%E5%B9%B8%20%E7%A4%BC%E5%93%81%E5%8D%A1"
DISCOUNT_LINE = 0.8  # 8折命中线
FACES = [20, 30, 50, 100, 200]
CST = timezone(timedelta(hours=8))

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

def extract_items(page):
    return page.evaluate("""
() => {
  const items = [];
  document.querySelectorAll('a[href*="item?id="]').forEach(a => {
    const t = (a.innerText || '').trim();
    if (t) items.push({text: t.replace(/\\n/g, ' | '), href: a.href});
  });
  return items;
}
""")

def parse_price(text):
    nums = re.findall(r'(\d+(?:\.\d+)?)', text.split('|')[1] if '|' in text else text)
    for n in nums:
        v = float(n)
        if 0.5 <= v <= 3000:
            return v
    return None

def face_value(text):
    """从标题中识别礼品卡面值"""
    m = re.search(r'(?:¥|￥|面值|张)?\s*(20|30|50|100|200)\s*(?:元|块|面值)?\s*(?:礼品卡|现金卡|卡)', text)
    if m:
        return int(m.group(1))
    m = re.search(r'(20|30|50|100|200)(?:元|块).{0,12}(?:礼品卡|现金卡)', text)
    if m:
        return int(m.group(1))
    return None

def main():
    from playwright.sync_api import sync_playwright
    hits, stats = [], {f: None for f in FACES}
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
        }
        ctx = browser.new_context(**ctx_args)
        if COOKIE:
            ctx.add_cookies([{"name": k.split("=", 1)[0], "value": k.split("=", 1)[1], "domain": ".goofish.com", "path": "/"}
                             for k in COOKIE.split("; ") if "=" in k])
        page = ctx.new_page()
        page.goto(SEARCH_URL, timeout=60000, wait_until="domcontentloaded")
        time.sleep(8)
        items = extract_items(page)
        # 检测登录态
        body = page.evaluate("() => document.body.innerText.slice(0, 500)")
        logged_in = "订单" in body and not re.search(r"\n登录\n", body)
        if COOKIE and not logged_in:
            push("⚠️ 闲鱼Cookie已过期", f"{now_str()} 云端检测到登录态失效，行情可能失真，请更新 XY_COOKIE Secret。")
        for it in items:
            if it["href"] in seen:
                continue
            seen.add(it["href"])
            text = it["text"]
            low = text.replace(" ", "").replace("｜", "")
            is_card = ("礼品卡" in low or "现金卡" in low) and ("瑞幸" in low or "luckin" in low.lower())
            face = face_value(low) if is_card else None
            price = parse_price(text)
            if face:
                discount = price / face if price else None
                if stats[face] is None or (price and price < stats[face]):
                    stats[face] = price
                if discount is not None and discount <= DISCOUNT_LINE:
                    hits.append(f"- [{face}元卡 ¥{price:g}（{discount*100:.1f}折）]({it['href']})\n  {text[:80]}")
        browser.close()

    lines = [f"## 闲鱼瑞幸礼品卡扫描 {now_str()}",
             f"共 {len(seen)} 条 | 登录态: {'是' if logged_in else '否(行情仅供粗筛参考)'}", ""]
    lines.append("### 行情最低价")
    for f in FACES:
        lines.append(f"- {f}元面值: {'无在售' if stats[f] is None else f'¥{stats[f]:g}'}")
    if hits:
        lines.append("\n### 🎯 命中(≤8折)")
        lines.extend(hits)
    report = "\n".join(lines)
    print(report)
    # 命中必推; 无命中也推简报(与本地技能"每轮必发"一致)
    push("🎯 瑞幸卡低价命中!" if hits else f"瑞幸卡扫描简报 {now_str()}",
         report if hits else f"{now_str()} 扫描{len(seen)}条，无≤8折命中。\n" +
         "\n".join(f"{f}元: " + ("无在售" if stats[f] is None else f"¥{stats[f]:g}") for f in FACES))

    with open("last_report.md", "w", encoding="utf-8") as f:
        f.write(report)

if __name__ == "__main__":
    main()
