import fs from "node:fs";

function loadDotEnvIfExists(filepath = "config.env") {
  if (!fs.existsSync(filepath)) return;
  try {
    const lines = fs.readFileSync(filepath, "utf8").split(/\r?\n/);
    for (const line of lines) {
      const s = line.trim();
      if (!s || s.startsWith("#") || !s.includes("=")) continue;
      const idx = s.indexOf("=");
      const k = s.slice(0, idx).trim();
      const v = s.slice(idx + 1).trim().replace(/^['"]|['"]$/g, "");
      if (k && !(k in process.env)) {
        process.env[k] = v;
      }
    }
  } catch (err) {
    console.log(`[設定] 讀取 ${filepath} 失敗: ${err?.message || err}`);
  }
}

loadDotEnvIfExists("config.env");

const CONFIG = {
  cookieFile: ".drrr_cookie.txt",
  rawCookie: (process.env.DRRR_COOKIE || "").trim(),
  lang: (process.env.DRRR_LANG || "zh-TW").trim(),
  pollIntervalMs: Math.max(5, Number(process.env.POLL_SECONDS || "15") || 15) * 1000,
  reportIntervalMs:
    Math.max(1, Number(process.env.REPORT_MINUTES || "20") || 20) * 60 * 1000,
  runHours: Math.max(0.05, Number(process.env.RUN_HOURS || "24") || 24),
  retryDelayMs: 20_000,
  maxMisses: 3,
};

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function loadCookie() {
  try {
    if (fs.existsSync(CONFIG.cookieFile)) {
      const fileCookie = fs.readFileSync(CONFIG.cookieFile, "utf8").trim();
      if (fileCookie) return fileCookie;
    }
  } catch (_) {}
  return CONFIG.rawCookie;
}

function saveCookie(cookieStr) {
  if (!cookieStr) return;
  try {
    fs.writeFileSync(CONFIG.cookieFile, cookieStr.trim(), "utf8");
  } catch (_) {}
}

function mergeSetCookie(currentCookie, res) {
  const jar = new Map();
  if (currentCookie) {
    for (const part of currentCookie.split(";")) {
      const idx = part.indexOf("=");
      if (idx > 0) {
        jar.set(part.slice(0, idx).trim(), part.slice(idx + 1).trim());
      }
    }
  }

  const rawHeaders =
    typeof res.headers.getSetCookie === "function"
      ? res.headers.getSetCookie()
      : [];

  for (const sc of rawHeaders) {
    const firstPart = sc.split(";")[0]?.trim();
    const idx = firstPart ? firstPart.indexOf("=") : -1;
    if (idx > 0) {
      jar.set(firstPart.slice(0, idx).trim(), firstPart.slice(idx + 1).trim());
    }
  }

  const merged = Array.from(jar.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  if (merged) {
    saveCookie(merged);
  }
  return merged || currentCookie;
}

function getTWTime() {
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function isCloudflareBlock(text) {
  if (!text) return false;
  return (
    text.includes("Just a moment") ||
    text.includes("cf-browser-verification") ||
    text.includes("Checking your browser") ||
    text.includes("Verify you are human")
  );
}

function buildHeaders(cookie, referer = "https://drrr.com/room/") {
  const headers = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": `${CONFIG.lang},zh;q=0.9,en-US;q=0.8,en;q=0.7`,
    "Origin": "https://drrr.com",
    "Referer": referer,
  };
  if (cookie) {
    headers["Cookie"] = cookie;
  }
  return headers;
}

async function checkRoom(cookie) {
  const headers = buildHeaders(cookie, "https://drrr.com/room/");
  const res = await fetch("https://drrr.com/json.php", { headers });
  const nextCookie = mergeSetCookie(cookie, res);
  const text = await res.text();

  if (isCloudflareBlock(text)) {
    return {
      ok: false,
      cookie: nextCookie,
      status: res.status,
      reason: "被 Cloudflare 阻擋／需要驗證",
    };
  }

  if (!res.ok) {
    return {
      ok: false,
      cookie: nextCookie,
      status: res.status,
      reason: `HTTP 狀態碼 ${res.status}`,
    };
  }

  try {
    const data = JSON.parse(text);
    const inRoom = Boolean(
      data &&
        typeof data === "object" &&
        !data.redirect &&
        (Array.isArray(data.users) || data.room)
    );
    if (!inRoom) {
      return {
        ok: false,
        cookie: nextCookie,
        status: res.status,
        reason: "目前不在房間內或 Session 已失效",
      };
    }
    return {
      ok: true,
      cookie: nextCookie,
      status: res.status,
      reason: "房間連線正常",
    };
  } catch (_) {
    return {
      ok: false,
      cookie: nextCookie,
      status: res.status,
      reason: "回傳內容不是合法 JSON（可能被導向首頁或驗證頁）",
    };
  }
}

async function sendReportMessage(cookie, message) {
  const endpoints = ["https://drrr.com/room/?ajax=1", "https://drrr.com/room/"];
  for (const endpoint of endpoints) {
    try {
      const headers = {
        ...buildHeaders(cookie, "https://drrr.com/room/"),
        "Content-Type": "application/x-www-form-urlencoded",
      };
      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body: new URLSearchParams({ message, ajax: "1" }),
      });
      const nextCookie = mergeSetCookie(cookie, res);
      const bodyText = await res.text();
      if (res.ok && !isCloudflareBlock(bodyText)) {
        console.log(`[報時] ${message} | 狀態: ${res.status}`);
        return { ok: true, cookie: nextCookie };
      }
    } catch (_) {}
  }
  console.log(`[報時] 發送失敗: ${message}`);
  return { ok: false, cookie };
}

async function main() {
  let cookie = loadCookie();
  if (cookie) {
    saveCookie(cookie);
  } else {
    console.log("[提示] 目前沒有偵測到 Cookie，若需要登入狀態請填入 config.env 或 DRRR_COOKIE。");
  }

  const deadline = Date.now() + CONFIG.runHours * 3600 * 1000;
  let lastReport = Date.now();
  let misses = 0;
  let tick = 0;

  console.log(
    `[JS啟動] 輪詢=${CONFIG.pollIntervalMs / 1000}s | 報時=${
      CONFIG.reportIntervalMs / 60000
    }m | 執行上限=${CONFIG.runHours}h`
  );

  while (Date.now() < deadline) {
    await sleep(CONFIG.pollIntervalMs);
    tick += 1;

    try {
      const statusRes = await checkRoom(cookie);
      cookie = statusRes.cookie;

      if (!statusRes.ok) {
        misses += 1;
        console.log(
          `[JS #${tick}] 異常：${statusRes.reason} (狀態=${statusRes.status}) | 累計=${misses}/${CONFIG.maxMisses}`
        );

        if (misses >= CONFIG.maxMisses) {
          console.log(
            `[JS重試] 連續異常達上限，等待 ${CONFIG.retryDelayMs / 1000} 秒後重新嘗試連線...`
          );
          misses = 0;
          cookie = loadCookie();
          await sleep(CONFIG.retryDelayMs);
        }
        continue;
      }

      misses = 0;
      if (tick % 4 === 0) {
        console.log(`[JS #${tick}] ${statusRes.reason} | 狀態=${statusRes.status}`);
      }

      const now = Date.now();
      if (now - lastReport >= CONFIG.reportIntervalMs) {
        const rep = await sendReportMessage(
          cookie,
          `【系統報時】現在時間：${getTWTime()}`
        );
        cookie = rep.cookie;
        if (rep.ok) {
          lastReport = now;
        }
      }
    } catch (err) {
      misses += 1;
      console.log(
        `[JS #${tick}] 網路例外: ${err?.message || err} | 累計=${misses}/${CONFIG.maxMisses}`
      );
      if (misses >= CONFIG.maxMisses) {
        console.log(
          `[JS重試] 例外達上限，等待 ${CONFIG.retryDelayMs / 1000} 秒後重新嘗試...`
        );
        misses = 0;
        await sleep(CONFIG.retryDelayMs);
      }
    }
  }

  console.log("[JS結束] 達到設定執行時長。");
}

main();
