"""礼儀正しいHTTP取得。

- robots.txt を必ず確認し、拒否されたURLは取得しない
- 同じホストへは最低 DELAY 秒あける
- ETag / Last-Modified で差分取得(相手サーバーの負荷を減らす)
- 連絡先入りの User-Agent を名乗る
"""
from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

USER_AGENT = os.environ.get(
    "LIVEHUB_UA",
    "LiveHubBot/0.1 (+https://tdtaisei-droid.github.io/live-hub/about.html; live info aggregator; opt-out contact on about page)",  # ヘッダーはASCIIのみ
)
DELAY = float(os.environ.get("LIVEHUB_DELAY", "3"))
TIMEOUT = 20
MAX_BYTES = 3_000_000


# 読み取り処理(collectors)を改良したら上げる
CACHE_VERSION = "4"


@dataclass
class Response:
    url: str
    status: int
    text: str
    content_type: str
    not_modified: bool = False


class BlockedByRobots(Exception):
    pass


class Fetcher:
    def __init__(self, cache_path: Optional[Path] = None):
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self.robots_state: dict[str, str] = {}  # ホスト → "ok" / "none"(無い) / "unreachable"(確認できず)
        self._last_hit: dict[str, float] = {}
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path and cache_path.exists():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if self.cache.get("__version__") != CACHE_VERSION:
            # 読み取り方を改良したら、変更のないページも一度は読み直す(改良を反映させるため)
            self.cache = {"__version__": CACHE_VERSION}

    def save_cache(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")

    # --- robots.txt ---
    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._raw_get(base + "/robots.txt", use_cache=False)
                if 400 <= r.status < 500:
                    rp = None  # robots.txt が無い = 制限なし
                    self.robots_state[base] = "none"
                elif r.status >= 500:
                    rp.parse(["User-agent: *", "Disallow: /"])  # サーバー不調時は巡回しない
                    self.robots_state[base] = f"unreachable (HTTP {r.status})"
                else:
                    rp.parse(r.text.splitlines())
                    self.robots_state[base] = "ok"
            except Exception as exc:
                rp.parse(["User-agent: *", "Disallow: /"])  # 確認できないときは巡回しない(安全側)
                why = str(getattr(exc, "reason", "") or exc)[:80]
                # 名前解決できない(=URLの誤り)か、接続を断られた/応答がない(=海外からの遮断の可能性)かを区別して残す
                self.robots_state[base] = f"unreachable ({type(exc).__name__}: {why})"
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(USER_AGENT.split("/")[0], url)

    def crawl_delay(self, url: str) -> float:
        rp = self._robots.get("{0.scheme}://{0.netloc}".format(urlsplit(url)))
        d = rp.crawl_delay(USER_AGENT.split("/")[0]) if rp else None
        return max(DELAY, float(d or 0))

    # --- 取得 ---
    def get(self, url: str, use_cache: bool = True) -> Response:
        """use_cache=False は差分取得をせず毎回本文を受け取る(トップページからの自動発見用)。"""
        url = safe_url(url)
        if not self.allowed(url):
            raise BlockedByRobots(url)
        return self._raw_get(url, use_cache=use_cache)

    def _raw_get(self, url: str, use_cache: bool = True) -> Response:
        host = urlsplit(url).netloc
        wait = self.crawl_delay(url) - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Accept-Language": "ja"}
        cached = self.cache.get(url) if use_cache else None
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                body = res.read(MAX_BYTES + 1)[:MAX_BYTES]
                if res.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                ctype = res.headers.get("Content-Type", "")
                text = _decode(body, ctype)
                if use_cache:
                    self.cache[url] = {
                        "etag": res.headers.get("ETag"),
                        "last_modified": res.headers.get("Last-Modified"),
                    }
                return Response(res.geturl(), res.status, text, ctype)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return Response(url, 304, "", "", not_modified=True)
            return Response(url, e.code, "", "")
        finally:
            self._last_hit[host] = time.monotonic()


def _decode(body: bytes, ctype: str) -> str:
    charset = None
    if "charset=" in ctype.lower():
        charset = ctype.lower().split("charset=")[-1].split(";")[0].strip()
    head = body[:2000].decode("ascii", "ignore").lower()
    if not charset and "charset=" in head:
        charset = head.split("charset=")[1].split('"')[0].split("'")[0].split(">")[0].strip(" /;")
    for enc in filter(None, [charset, "utf-8", "cp932", "euc_jp"]):
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def safe_url(url: str) -> str:
    """日本語などを含むURLを、送信できる形(パーセントエンコード)にする。"""
    from urllib.parse import quote
    return quote(url, safe=":/?#[]@!$&'()*+,;=%~")
