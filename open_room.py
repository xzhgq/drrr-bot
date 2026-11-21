from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import random
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

TAIWAN_TZ = timezone(timedelta(hours=8))


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


def solve_pow(
    nonce: str, timestamp: str, difficulty: int, start_counter: int = 1
) -> dict:
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


def perform_login(
    session: requests.Session,
    html: str,
    base_url: str,
    headers: dict[str, str],
    nickname: str,
    tripcode: str,
) -> tuple[bool, str, str]:
  """回傳 (是否成功, 最新HTML, 最新URL)"""
  print("    [登入] 偵測到登入表單，開始解析挑戰與計算 PoW...")
  nonce, timestamp, difficulty = extract_challenge_fields(html)
  print(
      f"    [登入] nonce={nonce} | timestamp={timestamp} |"
      f" difficulty={difficulty}"
  )

  if not nonce or not timestamp:
    print("    ❌ [登入] 無法取得 nonce 或 timestamp。")
    return False, html, base_url

  pow_res = solve_pow(nonce, timestamp, difficulty)
  print(
      f"    [登入] PoW 完成 | counter={pow_res['counter']} |"
      f" 耗時={pow_res['_elapsed_ms']}ms | hash={pow_res['hash'][:16]}..."
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
  post_url = (
      urljoin(str(base_url), act_m.group(1)) if act_m else "https://drrr.com/"
  )

  time.sleep(random.uniform(0.8, 2.0))
  resp2 = session.post(post_url, data=payload, headers=headers, timeout=25)
  new_html = resp2.text
  new_url = str(resp2.url)
  clean2 = clean_html_text(new_html)
  print(f"    [登入] 登入後狀態碼: {resp2.status_code} | 網址: {new_url}")

  if is_cf_blocked(new_html):
    print("    ❌ [登入] 登入回應命中 Cloudflare 驗證。")
    return False, new_html, new_url

  if "登入發生錯誤" in clean2:
    idx = clean2.find("登入發生錯誤")
    print("    ❌ [登入] 仍出現登入錯誤:", clean2[max(0, idx - 40) : idx + 140])
    return False, new_html, new_url

  print("    ✅ [登入] 登入成功！")
  return True, new_html, new_url


def extract_room_url(html: str, url_now: str) -> str:
  if "/room" in url_now:
    return url_now
  m = re.search(r'href=["\']([^"\']*?/room[^"\']*)["\']', html, re.IGNORECASE)
  if m:
    return urljoin("https://drrr.com/", m.group(1))
  return ""


def create_room(
    session: requests.Session,
    html: str,
    url_now: str,
    headers: dict[str, str],
    room_name: str,
    room_desc: str,
    limit: str,
    lang: str,
) -> tuple[bool, str, str, str]:
  """嘗試建立房間，回傳 (是否成功, 最新HTML, 最新URL, 房間URL)"""
  print(
      f"    [建房] 嘗試建立房間 -> 房名: {room_name} | 上限: {limit} | 語系:"
      f" {lang}"
  )
  target_html = html
  target_url = url_now

  if "lounge" not in url_now and "create" not in html.lower():
    time.sleep(random.uniform(0.5, 1.5))
    r_lounge = session.get("https://drrr.com/lounge", headers=headers, timeout=20)
    if is_cf_blocked(r_lounge.text):
      print("    ❌ [建房] 進入大廳命中 Cloudflare 驗證。")
      return False, r_lounge.text, str(r_lounge.url), ""
    target_html = r_lounge.text
    target_url = str(r_lounge.url)

  form_m = re.search(
      r"<form[^>]*(?:create|room)[^>]*>[\s\S]*?</form>",
      target_html,
      re.IGNORECASE,
  )
  if not form_m:
    print(
        "    ⚠️ [建房] 目前頁面未找到建房表單，頁面片段:",
        clean_html_text(target_html)[:240],
    )
    return False, target_html, target_url, extract_room_url(target_html, target_url)

  form_html = form_m.group(0)
  act_m = re.search(r'action=["\']([^"\']*)["\']', form_html, re.IGNORECASE)
  post_url = (
      urljoin("https://drrr.com/", act_m.group(1))
      if act_m
      else "https://drrr.com/create"
  )
  data: dict[str, str] = {}
  for hm in re.finditer(
      r'<input[^>]*type=["\']?hidden["\']?[^>]*>', form_html, re.IGNORECASE
  ):
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

  time.sleep(random.uniform(0.6, 1.6))
  r_create = session.post(post_url, data=data, headers=headers, timeout=25)
  new_html = r_create.text
  new_url = str(r_create.url)
  room_url = extract_room_url(new_html, new_url)
  print(
      f"    [建房] 回應狀態碼: {r_create.status_code} | 網址: {new_url}"
      f" | 房址: {room_url or '未取得'}"
  )
  print("    [建房] 頁面片段:", clean_html_text(new_html)[:240])

  if is_cf_blocked(new_html):
    print("    ❌ [建房] 建房回應命中 Cloudflare 驗證。")
    return False, new_html, new_url, room_url

  return True, new_html, new_url, room_url


def send_room_me_message(
    session: requests.Session,
    html: str,
    url_now: str,
    room_url: str,
    headers: dict[str, str],
    me_text: str,
) -> tuple[bool, str, str]:
  """在房間內以 /me 小字發送訊息，回傳 (是否送出成功, 最新HTML, 最新URL)"""
  current_html = html
  current_url = url_now
  formatted_msg = f"/me {me_text.lstrip()}"

  # 若目前不在房間頁且有已知房間網址，先回到房間頁取得最新表單
  if "/room" not in current_url and room_url:
    resp_room = session.get(room_url, headers=headers, timeout=20)
    current_html = resp_room.text
    current_url = str(resp_room.url)
    if is_cf_blocked(current_html):
      return False, current_html, current_url

  # 尋找可能的聊天輸入表單
  forms = re.findall(r"<form[\s\S]*?</form>", current_html, re.IGNORECASE)
  candidate_msg_fields = [
      "message",
      "msg",
      "text",
      "say",
      "comment",
      "content",
      "body",
  ]

  target_form_html = ""
  msg_field_name = ""

  for f_html in forms:
    inputs = re.findall(
        r"<(?:input|textarea)[^>]*>", f_html, re.IGNORECASE
    )
    for inp in inputs:
      nm_m = re.search(r'name=["\']([^"\']+)["\']', inp, re.IGNORECASE)
      if not nm_m:
        continue
      fname = nm_m.group(1)
      if fname.lower() in candidate_msg_fields:
        target_form_html = f_html
        msg_field_name = fname
        break
    if target_form_html:
      break

  if target_form_html and msg_field_name:
    act_m = re.search(
        r'action=["\']([^"\']*)["\']', target_form_html, re.IGNORECASE
    )
    post_url = (
        urljoin(current_url, act_m.group(1)) if act_m else current_url
    )
    payload: dict[str, str] = {}
    for hm in re.finditer(
        r'<input[^>]*type=["\']?hidden["\']?[^>]*>',
        target_form_html,
        re.IGNORECASE,
    ):
      nm = re.search(r'name=["\']([^"\']+)["\']', hm.group(0), re.IGNORECASE)
      vm = re.search(r'value=["\']([^"\']*)["\']', hm.group(0), re.IGNORECASE)
      if nm:
        payload[nm.group(1)] = vm.group(1) if vm else ""
    payload[msg_field_name] = formatted_msg

    time.sleep(random.uniform(0.4, 1.2))
    r_send = session.post(post_url, data=payload, headers=headers, timeout=20)
    new_html = r_send.text
    new_url = str(r_send.url)
    if is_cf_blocked(new_html):
      return False, new_html, new_url
    return True, new_html, new_url

  # 備援：若頁面為 JSON/AJAX 結構或表單未匹配，嘗試送往常見房間訊息端點
  post_target = room_url or (current_url if "/room" in current_url else "https://drrr.com/room/")
  for field in ("message", "msg", "text"):
    try:
      r_fb = session.post(
          post_target, data={field: formatted_msg}, headers=headers, timeout=15
      )
      if r_fb.status_code in (200, 204) and not is_cf_blocked(r_fb.text):
        return True, r_fb.text, str(r_fb.url)
    except Exception:
      continue

  return False, current_html, current_url


def check_room_alive(
    session: requests.Session,
    headers: dict[str, str],
    room_name: str,
    room_url: str,
) -> tuple[bool, bool, str, str, str]:
  """
    優先檢查房間狀態，避免沒事就去 /lounge 導致被視為離開房間。
    回傳 (是否仍登入, 房間是否仍在線上, 最新HTML, 最新URL, 房間URL)
    """
  target_url = room_url if room_url else "https://drrr.com/lounge"
  resp = session.get(target_url, headers=headers, timeout=20)
  html = resp.text
  url_now = str(resp.url)

  if is_cf_blocked(html):
    return False, False, html, url_now, room_url

  has_login_form = ('id="form-name"' in html) or ('name="login"' in html)
  if has_login_form and ("lounge" not in url_now) and ("/room" not in url_now):
    return False, False, html, url_now, room_url

  if "/room" in url_now:
    return True, True, html, url_now, url_now

  # 若原本有 room_url 但被導向到別處（例如大廳），代表已離開或掉房
  if room_url and "/lounge" in url_now:
    return True, False, html, url_now, ""

  # 若目前在大廳，檢查房間名稱是否仍在列表中
  clean_txt = clean_html_text(html)
  if "lounge" in url_now:
    room_present = (room_name in html) or (room_name in clean_txt)
    found_room_url = extract_room_url(html, url_now)
    return True, room_present, html, url_now, found_room_url

  return True, True, html, url_now, room_url


def main() -> None:
  nickname = os.environ.get("NICKNAME", "罌粟").strip()
  tripcode = os.environ.get("TRIPCODE", "TW._.MIT").strip()
  room_name = os.environ.get("ROOM_NAME", "深渊凝视著你").strip()
  room_desc = os.environ.get(
      "ROOM_DESC",
      "与怪物战斗的人，应当小心自己不要变成怪物。当你凝视深渊时，深渊也在凝视着你。",
  ).strip()
  limit = os.environ.get("ROOM_LIMIT", "20").strip()
  lang = os.environ.get("DRRR_LANG", "zh-TW").strip()
  raw_cookie = os.environ.get("DRRR_COOKIE", "").strip()

  try:
    heartbeat_hours = float(os.environ.get("HEARTBEAT_HOURS", "8").strip())
  except ValueError:
    heartbeat_hours = 8.0

  try:
    announce_minutes = max(
        0.5, float(os.environ.get("ANNOUNCE_MINUTES", "5").strip())
    )
  except ValueError:
    announce_minutes = 5.0

  announce_interval = announce_minutes * 60.0
  heartbeat_interval = 15
  duration_seconds = max(15.0, heartbeat_hours * 3600.0)
  total_beats = int(duration_seconds // heartbeat_interval)

  try:
    recreate_cooldown = max(
        5.0, float(os.environ.get("RECREATE_COOLDOWN", "20").strip())
    )
  except ValueError:
    recreate_cooldown = 20.0

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
  url_now = str(resp.url)
  print(f"    狀態碼: {resp.status_code} | 網址: {url_now}")

  if is_cf_blocked(html):
    print("❌ 命中 Cloudflare 驗證，中止。")
    return

  has_login_form = ('id="form-name"' in html) or ('name="login"' in html)
  if has_login_form:
    ok, html, url_now = perform_login(
        session, html, url_now, headers, nickname, tripcode
    )
    if not ok:
      return
  else:
    print("✅ 已處於登入狀態，跳過登入表單。")

  print("[3] 初始建房流程...")
  _, html, url_now, room_url = create_room(
      session, html, url_now, headers, room_name, room_desc, limit, lang
  )
  last_recreate_ts = time.time()
  last_announce_ts = time.time()

  start_ts = time.time()
  end_ts = start_ts + duration_seconds
  print(
      f"[4] 開始保活心跳（目標 {heartbeat_hours:g} 小時，每"
      f" {announce_minutes:g} 分鐘台灣時間 /me 報時）..."
  )

  idx = 0
  while time.time() < end_ts:
    jitter = random.uniform(-3.0, 5.0)
    sleep_sec = max(8.0, heartbeat_interval + jitter)
    remaining = end_ts - time.time()
    if remaining <= 0:
      break
    time.sleep(min(sleep_sec, remaining))
    if time.time() >= end_ts:
      break

    idx += 1
    elapsed_min = int((time.time() - start_ts) // 60)

    try:
      logged_in, room_alive, html, url_now, room_url = check_room_alive(
          session, headers, room_name, room_url
      )
      cf_hit = is_cf_blocked(html)

      print(
          f"    心跳 ({idx}/{total_beats}, 已跑 {elapsed_min} 分鐘) |"
          f" 登入={logged_in} | 房間在線={room_alive} | CF驗證={cf_hit}"
      )

      if cf_hit:
        print("    ⚠️ 偵測到 Cloudflare 驗證，提前停止心跳。")
        break

      if not logged_in:
        print("    🔄 偵測到未登入狀態，嘗試重新登入...")
        ok_login, html, url_now = perform_login(
            session, html, url_now, headers, nickname, tripcode
        )
        if not ok_login:
          print("    ⚠️ 重新登入未成功，等待下一輪心跳再試。")
          continue

      if not room_alive:
        since_last = time.time() - last_recreate_ts
        if since_last >= recreate_cooldown:
          print(
              f"    🛠️ 偵測到掉房（距離上次建房 {since_last:.1f} 秒），開始自動重新建房..."
          )
          _, html, url_now, room_url = create_room(
              session, html, url_now, headers, room_name, room_desc, limit, lang
          )
          last_recreate_ts = time.time()
        else:
          wait_left = recreate_cooldown - since_last
          print(
              f"    ⏳ 房間不在線上，但仍在重建冷卻中（剩餘 {wait_left:.1f} 秒）。"
          )

      # 每 5 分鐘（台灣時間）發送一次 /me 小字報時
      now_ts = time.time()
      if room_alive and (now_ts - last_announce_ts >= announce_interval):
        tw_now_str = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M")
        me_content = f"台灣時間 {tw_now_str}"
        sent, html, url_now = send_room_me_message(
            session, html, url_now, room_url, headers, me_content
        )
        last_announce_ts = now_ts
        if sent:
          print(f"    🕒 已發送小字報時: /me {me_content}")
        else:
          print(
              "    ⚠️ 嘗試發送小字報時未成功（可能需依實際房間表單欄位微調）。"
          )

    except Exception as exc:
      print(f"    心跳 ({idx}/{total_beats}) 例外: {exc}")

  print("[5] 8 小時心跳階段結束。")


if __name__ == "__main__":
  main()
