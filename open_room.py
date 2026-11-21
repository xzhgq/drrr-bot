import hashlib
import json
import os
import re
import time
from urllib.parse import urljoin
from curl_cffi import requests

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

CF_MARKERS = [
    "正在執行安全驗證",
    "請稍候",
    "Just a moment",
    "cf-browser-verification",
    "Verify you are human",
    "Checking your browser",
]


def is_cf_blocked(text: str) -> bool:
  return any(marker in text for marker in CF_MARKERS)


def parse_cookies(raw_cookie: str) -> dict[str, str]:
  parsed: dict[str, str] = {}
  if not raw_cookie:
    return parsed
  for chunk in raw_cookie.split(";"):
    chunk = chunk.strip()
    if "=" in chunk:
      k, v = chunk.split("=", 1)
      parsed[k.strip()] = v.strip()
  return parsed


def clean_html_text(html: str) -> str:
  text = re.sub(r"<script[\s\S]*?</script>", " ", html, re.IGNORECASE)
  text = re.sub(r"<style[\s\S]*?</style>", " ", text, re.IGNORECASE)
  text = re.sub(r"<[^>]+>", " ", text)
  return re.sub(r"\s+", " ", text).strip()


def solve_pow(nonce: str, timestamp: str, difficulty: int, start_counter: int = 1) -> dict:
  prefix = f"{nonce}{timestamp}".encode("ascii")
  target_prefix = "0" * max(0, difficulty)
  t0 = time.time()
  counter = start_counter

  while True:
    msg = prefix + str(counter).encode("ascii")
    digest = hashlib.sha256(msg).hexdigest()
    if digest.startswith(target_prefix):
      elapsed_ms = int((time.time() - t0) * 1000)
      return {
          "hash": digest,
          "nonce": nonce,
          "timestamp": timestamp,
          "counter": counter,
          "difficulty": difficulty,
          "stats": None,
          "_elapsed_ms": elapsed_ms,
      }
    counter += 1


def extract_challenge_fields(html: str) -> tuple[str, str, int]:
  def find_val(name: str) -> str:
    m = re.search(
        rf'<input[^>]*name=["\']?{re.escape(name)}["\']?[^>]*value=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
      return m.group(1)
    m2 = re.search(
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']?{re.escape(name)}["\']?',
        html,
        re.IGNORECASE,
    )
    return m2.group(1) if m2 else ""

  nonce = find_val("nonce")
  timestamp = find_val("timestamp")
  diff_str = find_val("difficulty") or "4"
  try:
    difficulty = int(diff_str)
  except ValueError:
    difficulty = 4
  return nonce, timestamp, difficulty


def main() -> None:
  nickname = os.environ.get("NICKNAME", "罌粟").strip()
  tripcode = os.environ.get("TRIPCODE", "TW._.MIT").strip()
  room_name = os.environ.get("ROOM_NAME", "深渊凝视著你").strip()
  room_desc = os.environ.get(
      "ROOM_DESC",
      "与怪物战斗的人，应当小心自己不要变成怪物。当你凝视深渊时，深渊也在凝视着你。",
  ).strip()
  limit = os.environ.get("ROOM_LIMIT", "2").strip()
  lang = os.environ.get("DRRR_LANG", "zh-TW").strip()
  raw_cookie = os.environ.get("DRRR_COOKIE", "").strip()

  try:
    heartbeat_hours = float(os.environ.get("HEARTBEAT_HOURS", "8").strip())
  except ValueError:
    heartbeat_hours = 8.0
  heartbeat_interval = 15
  duration_seconds = max(15.0, heartbeat_hours * 3600.0)
  total_beats = int(duration_seconds // heartbeat_interval)

  headers = {
      "User-Agent": CHROME_UA,
      "Accept": (
          "text/html,application/xhtml+xml,application/xml;q=0.9,"
          "image/avif,image/webp,*/*;q=0.8"
      ),
      "Accept-Language": f"{lang},zh;q=0.9,en-US;q=0.8,en;q=0.7",
      "Origin": "https://drrr.com",
      "Referer": "https://drrr.com/",
      "Upgrade-Insecure-Requests": "1",
  }

  session = requests.Session(impersonate="chrome")
  cookies = parse_cookies(raw_cookie)
  if cookies:
    session.cookies.update(cookies)
    print(f"[Session] 已掛載 {len(cookies)} 筆 Cookie: {list(cookies.keys())}")

  print("[1] 連線 https://drrr.com/ ...")
  resp = session.get("https://drrr.com/", headers=headers, timeout=25)
  html = resp.text
  print(f"    狀態碼: {resp.status_code} | 網址: {resp.url}")

  if is_cf_blocked(html):
    print("❌ 命中 Cloudflare 驗證，中止。")
    return

  has_login_form = ('id="form-name"' in html) or ('name="login"' in html)

  if has_login_form:
    print("[2] 偵測到登入表單，開始解析挑戰與計算 PoW...")
    nonce, timestamp, difficulty = extract_challenge_fields(html)
    print(f"    nonce={nonce} | timestamp={timestamp} | difficulty={difficulty}")

    if not nonce or not timestamp:
      print("❌ 無法取得 nonce 或 timestamp，中止。")
      return

    pow_res = solve_pow(nonce, timestamp, difficulty)
    print(
        f"    PoW 完成 | counter={pow_res['counter']} | 耗時={pow_res['_elapsed_ms']}ms"
        f" | hash={pow_res['hash'][:16]}..."
    )

    challenged_payload = json.dumps(
        {
            "hash": pow_res["hash"],
            "nonce": pow_res["nonce"],
            "timestamp": pow_res["timestamp"],
            "counter": pow_res["counter"],
            "difficulty": pow_res["difficulty"],
            "stats": None,
        },
        separators=(",", ":"),
    )

    form_m = re.search(
        r'<form[^>]*id=["\']?form-name["\']?[^>]*>[\s\S]*?</form>',
        html,
        re.IGNORECASE,
    )
    scope = form_m.group(0) if form_m else html
    payload: dict[str, str] = {}

    for tag_m in re.finditer(r"<(?:input|select)[^>]*>", scope, re.IGNORECASE):
      tag = tag_m.group(0)
      name_m = re.search(r'name=["\']([^"\']+)["\']', tag, re.IGNORECASE)
      if not name_m:
        continue
      fname = name_m.group(1)
      val_m = re.search(r'value=["\']([^"\']*)["\']', tag, re.IGNORECASE)
      dval_m = re.search(r'data-value=["\']([^"\']*)["\']', tag, re.IGNORECASE)
      val = val_m.group(1) if val_m else (dval_m.group(1) if dval_m else "")

      if fname == "icon":
        if "icon" not in payload or re.search(r"\bchecked\b", tag, re.IGNORECASE):
          payload["icon"] = val
      else:
        payload[fname] = val

    payload["name"] = nickname
    if tripcode:
      payload["tripcode"] = tripcode
    payload["challenged"] = challenged_payload
    payload["login"] = payload.get("login") or "ENTER"

    act_m = re.search(r'action=["\']([^"\']*)["\']', scope, re.IGNORECASE)
    post_url = urljoin(str(resp.url), act_m.group(1)) if act_m else "https://drrr.com/"
    resp2 = session.post(post_url, data=payload, headers=headers, timeout=25)
    html = resp2.text
    clean2 = clean_html_text(html)
    print(f"    登入後狀態碼: {resp2.status_code} | 網址: {resp2.url}")

    if "登入發生錯誤" in clean2:
      idx = clean2.find("登入發生錯誤")
      print("❌ 仍出現登入錯誤:", clean2[max(0, idx - 40) : idx + 140])
      return
    print("✅ 登入成功（未出現登入錯誤）！")
  else:
    print("✅ 已處於登入狀態，跳過登入表單。")

  print(f"[3] 嘗試建立房間 -> 房名: {room_name} | 上限: {limit} | 語系: {lang}")
  target_html = html
  if "lounge" not in str(resp.url) and "create" not in html.lower():
    r_lounge = session.get("https://drrr.com/lounge", headers=headers, timeout=20)
    if not is_cf_blocked(r_lounge.text):
      target_html = r_lounge.text

  form_m = re.search(
      r"<form[^>]*(?:create|room)[^>]*>[\s\S]*?</form>",
      target_html,
      re.IGNORECASE,
  )
  if form_m:
    form_html = form_m.group(0)
    act_m = re.search(r'action=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
    url = urljoin("https://drrr.com/", act_m.group(1)) if act_m else "https://drrr.com/create"
    data = {}
    for hm in re.finditer(r'<input[^>]*type=["\']?hidden["\']?[^>]*>', form_html, re.IGNORECASE):
      nm = re.search(r'name=["\']([^"\']+)["\']', hm.group(0), re.IGNORECASE)
      vm = re.search(r'value=["\']([^"\']*)["\']', hm.group(0), re.IGNORECASE)
      if nm:
        data[nm.group(1)] = vm.group(1) if vm else ""
    data.update({
        "name": room_name,
        "description": room_desc,
        "limit": str(limit),
        "language": lang,
    })
    r_create = session.post(url, data=data, headers=headers, timeout=25)
    print(f"    建房回應狀態碼: {r_create.status_code} | 網址: {r_create.url}")
    print("    建房頁面片段:", clean_html_text(r_create.text)[:300])
  else:
    print("    目前頁面未找到建房表單，頁面片段:", clean_html_text(target_html)[:300])

  start_ts = time.time()
  end_ts = start_ts + duration_seconds
  print(
      f"[4] 開始保活心跳（目標 {heartbeat_hours:g} 小時，共 {total_beats} 輪，間隔 {heartbeat_interval} 秒）..."
  )

  idx = 0
  while time.time() < end_ts:
    remaining = end_ts - time.time()
    time.sleep(min(heartbeat_interval, max(0.0, remaining)))
    if time.time() >= end_ts:
      break
    idx += 1
    try:
      r_hb = session.get("https://drrr.com/", headers=headers, timeout=20)
      cf_hit = is_cf_blocked(r_hb.text)
      elapsed_min = int((time.time() - start_ts) // 60)
      print(
          f"    心跳 ({idx}/{total_beats}, 已跑 {elapsed_min} 分鐘) | 狀態碼: {r_hb.status_code}"
          f" | 網址: {r_hb.url} | CF驗證: {cf_hit}"
      )
      if cf_hit:
        print("    ⚠️ 偵測到 Cloudflare 驗證，提前停止心跳。")
        break
    except Exception as exc:
      print(f"    心跳 ({idx}/{total_beats}) 例外: {exc}")

  print("[5] 心跳階段結束。")


if __name__ == "__main__":
  main()
