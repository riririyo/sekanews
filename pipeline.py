# -*- coding: utf-8 -*-
"""
pipeline.py
===========
① ニュース取得(Google News RSS) → ② AI要約・ジャンル・地名判定・タイトル和訳(Groq)
→ ③ 重複除去・地域ノルマで選抜 → ④ 座標変換(Nominatim) → ⑤ 前回分・スポンサーピンと
マージしてnews_data.json出力

設定はすべて config.py にまとめてあるので、挙動を変えたいときはそちらを編集する。

重要な設計変更(蓄積方式): 1回の実行で必ず1000件集める必要はない。今回新しく取得できた
記事(new_items)と、前回までに集めて有効期限内の記事(kept_previous)をマージして
出力するため、1時間ごとの取得件数が少ない時間帯でも、地図全体としては常にMAX_PINS_TOTALに
近いピンが世界中に散らばっている状態を保てる。ITEM_MAX_AGE_HOURSを過ぎた記事は自動的に
地図から消える。スポンサーピン(SPONSOR_PINS)はこの蓄積・期限管理の対象外で、
config.pyに書いてある限り常に地図に表示され続ける。
"""

import concurrent.futures
import difflib
import json
import math
import os
import random
import re
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import requests

from config import (
    AI_MAX_RETRIES,
    AI_MODEL,
    ARTICLES_PER_AI_BATCH,
    DUPLICATE_COORD_JITTER_DEGREES,
    DUPLICATE_TITLE_SIMILARITY_THRESHOLD,
    GENRE_COLORS,
    GEOCODE_CACHE_FILE,
    GROQ_API_KEY,
    GROQ_MAX_WORKERS,
    GROQ_REQUEST_DELAY_SEC,
    GROQ_RETRY_GIVE_UP_THRESHOLD_SEC,
    ITEM_MAX_AGE_HOURS,
    GOOGLE_NEWS_RESOLVE_TIMEOUT_SEC,
    LOCAL_CITIES,
    MAX_NEW_ARTICLES_PER_RUN,
    MAX_PINS_TOTAL,
    NEWS_COUNTRIES,
    NEWS_MAX_ITEMS_PER_COUNTRY,
    NEWS_MAX_ITEMS_PER_LOCAL_CITY,
    NEWS_MAX_RETRIES,
    NEWS_MAX_RETRY_WAIT_SEC,
    NEWS_REQUEST_DELAY_SEC,
    NEWS_REQUEST_TIMEOUT_SEC,
    NEWS_RSS_URL_TEMPLATE,
    NEWS_SEARCH_RSS_URL_TEMPLATE,
    NOMINATIM_ACCEPT_LANGUAGE,
    NOMINATIM_DELAY_SEC,
    NOMINATIM_USER_AGENT,
    OUTPUT_FILE,
    PLACEHOLDER_IMAGE_BASE,
    REAL_IMAGE_FETCH_MAX_BYTES,
    REAL_IMAGE_FETCH_MAX_WORKERS,
    REAL_IMAGE_FETCH_TIMEOUT_SEC,
    RESOLVE_GOOGLE_NEWS_URLS,
    LOCAL_SELECT_RATIO,
    REGION_QUOTAS,
    SENSITIVE_KEYWORDS,
    SPONSOR_PINS,
    UPDATE_INTERVAL_MINUTES,
    VALID_GENRES,
)

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Google Newsの中の人にbotだと弾かれないよう、普通のブラウザっぽいUser-Agentを付ける。
NEWS_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# 黄金角(度)。同じ座標に複数ピンが重なるとき、視覚的にきれいに散らすための
# スパイラル配置に使う(向日葵の種の並びと同じ原理)。
GOLDEN_ANGLE_DEG = 137.5077640500378

# 記事ページのHTML内からog:image(SNSシェア用画像)のURLを抜き出すための正規表現。
# <meta property="og:image" content="..."> と <meta content="..." property="og:image">
# の両方の属性順に対応する(サイトによって順番が違うため)。
_OG_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# ① ニュース取得 (Google News RSS: 国・言語ごとの「トップニュース」フィード)
# ---------------------------------------------------------------------------
def _parse_pubdate(raw_pubdate):
    """RSSのpubDate(RFC822形式)をISO8601(UTC)文字列に変換する。パース失敗時はNone。"""
    if not raw_pubdate:
        return None
    try:
        dt = parsedate_to_datetime(raw_pubdate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _parse_news_rss(xml_bytes, country_label):
    """Google News RSSのXMLをパースして記事リストに変換する。"""
    articles = []
    root = ET.fromstring(xml_bytes)
    items = root.findall("./channel/item")

    for item in items[:NEWS_MAX_ITEMS_PER_COUNTRY]:
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")
        pubdate_el = item.find("pubDate")

        raw_title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        domain = (source_el.text or "").strip() if source_el is not None else ""
        raw_pubdate = (pubdate_el.text or "").strip() if pubdate_el is not None else ""

        if not raw_title or not link:
            continue

        # Google Newsのタイトルは「見出し - 情報源名」の形式で来るので、末尾の
        # 情報源名を切り離しておく(AIに渡す見出しをなるべくきれいにするため)。
        title = raw_title
        if domain and raw_title.endswith(domain):
            title = raw_title[: -len(domain)].rstrip(" -–—").strip()

        articles.append({
            "title": title,
            "url": link,
            "domain": domain,
            "socialimage": None,
            "published_at": _parse_pubdate(raw_pubdate),
            "_region_hint": "",
            "_country_label": country_label,
        })

    return articles


def fetch_country_articles(country_label, gl, hl, ceid):
    url = NEWS_RSS_URL_TEMPLATE.format(hl=hl, gl=gl, ceid=ceid)

    last_err = None
    for attempt in range(NEWS_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=NEWS_REQUEST_TIMEOUT_SEC, headers=NEWS_REQUEST_HEADERS)
            resp.raise_for_status()
            return _parse_news_rss(resp.content, country_label)
        except Exception as e:
            last_err = e
            if attempt < NEWS_MAX_RETRIES:
                wait = min(5 * (2 ** attempt), NEWS_MAX_RETRY_WAIT_SEC)
                print(f"    [WARN] ニュース取得失敗(試行{attempt + 1}, {country_label}): {e} -> {wait}秒待って再試行")
                time.sleep(wait)

    print(f"  [WARN] ニュース取得を諦めます ({country_label}): {last_err}")
    return []


def fetch_local_city_articles(label, query, gl, hl, ceid):
    """Googleニュースの検索フィード(地名検索)で、その都市に関する記事を取得する。
    国別トップニュースでは拾えない、地方メディアのローカルニュースを補う目的。"""
    url = NEWS_SEARCH_RSS_URL_TEMPLATE.format(query=quote(query), hl=hl, gl=gl, ceid=ceid)

    last_err = None
    for attempt in range(NEWS_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=NEWS_REQUEST_TIMEOUT_SEC, headers=NEWS_REQUEST_HEADERS)
            resp.raise_for_status()
            articles = _parse_news_rss(resp.content, label)
            return articles[:NEWS_MAX_ITEMS_PER_LOCAL_CITY]
        except Exception as e:
            last_err = e
            if attempt < NEWS_MAX_RETRIES:
                wait = min(5 * (2 ** attempt), NEWS_MAX_RETRY_WAIT_SEC)
                print(f"    [WARN] ローカルニュース取得失敗(試行{attempt + 1}, {label}): {e} -> {wait}秒待って再試行")
                time.sleep(wait)

    print(f"  [WARN] ローカルニュース取得を諦めます ({label}): {last_err}")
    return []


def collect_all_articles():
    all_articles = []
    seen_urls = set()

    for country in NEWS_COUNTRIES:
        label = country["label"]
        print(f"  [FETCH] {label} (gl={country['gl']} hl={country['hl']})")
        articles = fetch_country_articles(label, country["gl"], country["hl"], country["ceid"])

        added = 0
        for a in articles:
            url = a.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                a["_source_type"] = "top"  # 国別トップニュース由来
                all_articles.append(a)
                added += 1
        print(f"          -> {added}件追加(重複除く)")

        time.sleep(NEWS_REQUEST_DELAY_SEC)

    print(f"  [FETCH] 国別トップニュース候補: {len(all_articles)}件")

    # ローカルニュース(地名検索フィード): 首都・国全体ではなく特定都市の
    # ローカルメディアの記事も拾えるようにする(例: 札幌のSTVなど)。
    for city in LOCAL_CITIES:
        label = city["label"]
        print(f"  [FETCH-LOCAL] {label} (地名検索: {city['query']})")
        articles = fetch_local_city_articles(label, city["query"], city["gl"], city["hl"], city["ceid"])

        added = 0
        for a in articles:
            url = a.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                a["_source_type"] = "local"  # 都市名検索(ローカル)由来
                all_articles.append(a)
                added += 1
        print(f"          -> {added}件追加(重複除く)")

        time.sleep(NEWS_REQUEST_DELAY_SEC)

    print(f"  [FETCH] 合計候補(トップ+ローカル): {len(all_articles)}件")
    return all_articles


# ---------------------------------------------------------------------------
# ② AI要約・ジャンル・地名判定・タイトル和訳 (Groq)
# ---------------------------------------------------------------------------
def _build_prompt(batch):
    items_text = "\n".join(
        f"{i}. title: {a.get('title', '')}\n"
        f"   domain: {a.get('domain', '')}\n"
        f"   参考国ヒント: {a.get('_country_label', '')}"
        for i, a in enumerate(batch)
    )
    genre_list = "、".join(VALID_GENRES)
    region_list = "、".join(REGION_QUOTAS.keys())

    system_prompt = (
        "あなたはニュース記事を分析するアシスタントです。"
        "与えられるのは記事の見出し(タイトル)だけで、本文は渡されません。"
        "各記事について、以下を判定してください。\n"
        f"1. genre: 次のいずれか一つ -> {genre_list}\n"
        f"2. region: 次のいずれか一つ -> {region_list}\n"
        "3. place: 見出しの内容が実際に起きた/関係する場所として、地図でジオコーディング"
        "できる具体的な地名(できれば都市名。見出しに区・駅・施設名など都市より細かい"
        "地名があればそちらを優先すること)。その国全体の選挙・政策・災害など国名レベルの"
        "出来事なら国名でもよい。英語の一般的な地名表記で統一すること"
        "(例: '東京' ではなく 'Tokyo, Japan'、'ニューヨーク' ではなく 'New York, USA')。\n"
        "   重要: 各記事に付いている「参考国ヒント」は、そのニュースをどの国のRSS"
        "フィードから取得したかを示すだけで、記事の内容の発生場所ではない。見出し自体に"
        "地名・国名を示す語が含まれていない限り、参考国ヒントをそのままplaceに使っては"
        "いけない。健康・科学・生活の知恵・一般的なノウハウ・人物の日常的な話題など、"
        "特定の場所と結びつかない見出しは、どの国のフィードから来ていてもplaceを空文字"
        "\"\"にすること。タイトルから具体的な場所が全く読み取れない場合は例外なく空文字"
        "\"\"とすること。\n"
        "4. title_ja: 見出しを自然な日本語に翻訳したもの。要約ではなく翻訳なので、"
        "見出しの情報を削らずに日本語にすること。見出しが既に日本語の場合はそのまま"
        "(表記の乱れがあれば軽く整える程度でよい)。\n"
        "5. summary_ja: 見出しの内容について日本語で3〜4文(120〜200字程度)で説明する"
        "文章。見出しに書かれていない具体的な数字・固有名詞・引用・結果を勝手に創作しないこと。"
        "ただし、見出しに出てくる人物・組織・出来事の種類について一般的に知られている"
        "背景情報(どんな職業/組織か、通常どんな文脈で報じられる話題かなど)を補って、"
        "読み応えのある説明にするのは良い。断定できない部分は「〜とみられる」"
        "「〜と考えられる」のような表現を使い、見出しに書かれた事実と区別すること。\n"
        "見出しが日本語以外の言語で書かれていても、内容を理解した上でtitle_ja/"
        "summary_jaは日本語で書くこと。\n"
        "\n"
        '出力は必ず {"results": [ {"index": 0, "genre": "...", "region": "...", '
        '"place": "...", "title_ja": "...", "summary_ja": "..."}, ... ] } '
        "という形のJSONオブジェクトのみ。説明文やコードブロック記号は一切付けないこと。"
    )
    user_prompt = f"以下の記事を分析してください:\n{items_text}"
    return system_prompt, user_prompt


def call_groq_batch(batch):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY が設定されていません")

    system_prompt, user_prompt = _build_prompt(batch)
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    last_err = None
    for attempt in range(AI_MAX_RETRIES + 1):
        try:
            resp = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=30)
            # 429(レート制限)は別扱いにする。Groqが返す Retry-After
            # (待つべき秒数)ヘッダーがあればその秒数だけ待ってから再試行することで、
            # 盲目的な短いリトライでリトライ回数を使い果たしてバッチを丸ごと落とす(run #31での)
            # 失敗を防ぐ。Retry-Afterが無い場合は指数バックオフで待つ。
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                try:
                    raw_wait = float(retry_after)
                except (TypeError, ValueError):
                    raw_wait = min(2 ** (attempt + 1), 30)
                last_err = RuntimeError(f"HTTP 429 (rate limit), Retry-After={retry_after or 'なし'}")
                # 2026-07-07: 以前はここで(Retry-Afterが何秒であれ)60秒に丸めて
                # 再試行していたが、本番run#34で実測したところRetry-Afterが816秒/823秒
                # (=短時間の混雑ではなく1時間/1日単位のクォータ切れを示唆)なのに60秒だけ
                # 待って再試行し、当然また429になる、をAI_MAX_RETRIES回(6回)繰り返して
                # 1バッチだけで6分(60秒×6回)を無駄にし続けていたことが判明した。
                # Retry-AfterがGROQ_RETRY_GIVE_UP_THRESHOLD_SECを超える場合は、
                # このrun中には回復しない長時間のレート制限とみなし、再試行を諦めて
                # 即座にこのバッチをスキップする。
                if raw_wait > GROQ_RETRY_GIVE_UP_THRESHOLD_SEC:
                    print(f"  [WARN] Groqレート制限(429) -> Retry-After={raw_wait:.0f}秒は長すぎるため、"
                          f"再試行を諦めてこのバッチをスキップします(クォータ切れの可能性)")
                    break
                wait = min(max(raw_wait, 1.0), 60.0)
                if attempt < AI_MAX_RETRIES:
                    print(f"  [WARN] Groqレート制限(429, 試行{attempt + 1}) -> {wait:.0f}秒待って再試行")
                    time.sleep(wait)
                    continue
                break
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            results = parsed.get("results", [])
            if not isinstance(results, list):
                raise ValueError("'results' が配列ではありません")
            return results
        except Exception as e:
            # Authorizationヘッダーやペイロード本文はログに出さない(APIキー漏洩防止)。
            last_err = e
            if attempt < AI_MAX_RETRIES:
                wait = min(2 ** (attempt + 1), 30)
                print(f"  [WARN] Groq呼び出し失敗(試行{attempt + 1}): {type(e).__name__}: {e} -> {wait}秒待って再試行")
                time.sleep(wait)

    raise RuntimeError(f"Groq呼び出しが{AI_MAX_RETRIES + 1}回とも失敗: {last_err}")


def _is_sensitive_title(title):
    """見出しに配慮が必要なキーワードが含まれていないか簡易チェックする。"""
    if not title:
        return False
    normalized = unicodedata.normalize("NFKC", title).casefold()
    for kw in SENSITIVE_KEYWORDS:
        if unicodedata.normalize("NFKC", kw).casefold() in normalized:
            return True
    return False


def _process_ai_batch(batch, batch_num, total_batches):
    """1バッチ分のGroq呼び出し+結果検証をまとめて行う。並列実行(ThreadPoolExecutor)
    から呼ばれる前提で、副作用(print以外)を持たない純粋な処理として切り出してある。
    戻り値: (このバッチで採用できた記事のリスト, センシティブ判定で弾いた件数)"""
    print(f"  [AI] バッチ {batch_num}/{total_batches} ({len(batch)}件)")

    try:
        results = call_groq_batch(batch)
    except Exception as e:
        print(f"  [SKIP] バッチ{batch_num}を諦めて次へ: {e}")
        print(f"         詳細: {traceback.format_exc(limit=2)}")
        return [], 0

    batch_enriched = []
    blocked = 0

    for r in results:
        idx = r.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(batch)):
            continue
        article = batch[idx]

        if _is_sensitive_title(article.get("title", "")):
            blocked += 1
            continue

        genre = r.get("genre") or ""
        region = r.get("region") or ""
        place = (r.get("place") or "").strip()
        title_ja = (r.get("title_ja") or "").strip()
        summary = (r.get("summary_ja") or "").strip()

        # --- AIの返りを検証(不正なジャンル/地域は矯正 or 除外) ---
        if genre not in VALID_GENRES:
            genre = "その他"
        if region not in REGION_QUOTAS:
            continue  # 地域を集計できない記事はノルマ選抜の対象外にする

        # 地名が取れない記事はピンにしない、という設計上のルール
        if not place or not summary:
            continue

        batch_enriched.append({
            **article,
            "ai_genre": genre,
            "ai_region": region,
            "ai_place": place,
            "ai_title_ja": title_ja or article.get("title", ""),
            "ai_summary": summary,
        })

    return batch_enriched, blocked


def enrich_articles_with_ai(articles):
    total_batches = (len(articles) + ARTICLES_PER_AI_BATCH - 1) // ARTICLES_PER_AI_BATCH
    batches = [
        articles[b * ARTICLES_PER_AI_BATCH: b * ARTICLES_PER_AI_BATCH + ARTICLES_PER_AI_BATCH]
        for b in range(total_batches)
    ]

    # 2026-07-07: 以前はバッチを1つずつ直列に処理し、間にGROQ_REQUEST_DELAY_SEC秒
    # 待っていた。新規記事が数千件規模の時間帯だと数百バッチを直列待機することになり、
    # パイプライン全体が60分(cronの実行間隔)を超えて「1時間に1回のはずが3時間に
    # 1回程度しか更新されない」不具合の主因になっていた。GROQ_MAX_WORKERS件を
    # 同時実行することで待機時間を実質1/GROQ_MAX_WORKERSに圧縮する。429(レート制限)が
    # 起きてもcall_groq_batch側の指数バックオフ再試行で自然に吸収される設計。
    enriched = []
    blocked_sensitive = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=GROQ_MAX_WORKERS) as executor:
        future_to_num = {
            executor.submit(_process_ai_batch, batch, i + 1, total_batches): i + 1
            for i, batch in enumerate(batches)
        }
        for future in concurrent.futures.as_completed(future_to_num):
            batch_num = future_to_num[future]
            try:
                batch_enriched, batch_blocked = future.result()
            except Exception as e:
                print(f"  [SKIP] バッチ{batch_num}で予期しない例外: {e}")
                continue
            enriched.extend(batch_enriched)
            blocked_sensitive += batch_blocked

    if blocked_sensitive:
        print(f"  [FILTER] センシティブな見出しのためピン化を見送り: {blocked_sensitive}件")
    print(f"  [AI] 判定OK: {len(enriched)}件")
    return enriched


# ---------------------------------------------------------------------------
# ③ 重複除去 + 地域ノルマで選抜
# ---------------------------------------------------------------------------
def dedup_similar_titles(items):
    """同じ出来事を報じた似たタイトルの記事を弾く。"""
    kept = []
    kept_titles = []
    removed = 0

    for item in items:
        title = (item.get("title") or "").strip().lower()
        is_dup = False
        for kt in kept_titles:
            if difflib.SequenceMatcher(None, title, kt).ratio() >= DUPLICATE_TITLE_SIMILARITY_THRESHOLD:
                is_dup = True
                break
        if is_dup:
            removed += 1
        else:
            kept.append(item)
            kept_titles.append(title)

    if removed:
        print(f"  [DEDUP] 類似タイトルの重複記事を除去: {removed}件")
    return kept


def _split_by_ratio(primary, secondary, total, primary_ratio):
    """primary(優先したいグループ)とsecondary(確保したいグループ)から合計total件を、
    primary_ratioの比率でバランスよく採る。どちらかが足りなければ、余った枠はもう一方で
    埋める(枠を無駄にしない)。各グループ内はシャッフルして公平に選ぶ。
    ここでは primary=ローカル記事 / secondary=トップニュース として使い、
    「ローカルを厚く採りつつ、トップニュースの枠も必ず確保する」ために用いる。"""
    if total <= 0:
        return []
    p = primary[:]
    s = secondary[:]
    random.shuffle(p)
    random.shuffle(s)

    p_target = int(round(total * primary_ratio))
    s_target = total - p_target

    picked_p = p[:p_target]
    picked_s = s[:s_target]

    # どちらかが目標に届かなければ、残り枠をもう一方の余りで補充する。
    remaining = total - len(picked_p) - len(picked_s)
    if remaining > 0:
        leftover = p[len(picked_p):] + s[len(picked_s):]
        random.shuffle(leftover)
        picked = picked_p + picked_s + leftover[:remaining]
    else:
        picked = picked_p + picked_s

    random.shuffle(picked)  # トップとローカルが固まらないよう最後に混ぜる
    return picked


def select_by_region_quota(enriched):
    by_region = {}
    for a in enriched:
        by_region.setdefault(a["ai_region"], []).append(a)

    selected = []
    for region, quota in REGION_QUOTAS.items():
        candidates = by_region.get(region, [])
        # 2026-07-07: ノルマ枠を「ローカル(都市検索由来)」と「トップ(国別トップニュース由来)」に
        # LOCAL_SELECT_RATIOの比率で配分して選抜する。これにより、トランプの言動・ホルムズ海峡・
        # 経済危機のような硬派な大ニュース(トップ)の枠を必ず確保しつつ、知らない街の開店・
        # 地元の話題のようなローカルニュースを厚めに採れる。どちらかの候補が足りない地域では
        # 余った枠をもう一方で埋めるので、枠は無駄にならない。
        local_cands = [a for a in candidates if a.get("_source_type") == "local"]
        top_cands = [a for a in candidates if a.get("_source_type") != "local"]
        picked = _split_by_ratio(local_cands, top_cands, quota, LOCAL_SELECT_RATIO)
        selected.extend(picked)
        n_local = sum(1 for a in picked if a.get("_source_type") == "local")
        n_top = len(picked) - n_local
        print(f"  [SELECT] {region}: {len(picked)}/{quota}件 (ローカル{n_local}/トップ{n_top})")

    selected = dedup_similar_titles(selected)
    print(f"  [SELECT] 今回の新規選抜: {len(selected)}件")
    return selected


# ---------------------------------------------------------------------------
# ④ 座標変換 (Nominatim)
# ---------------------------------------------------------------------------
def normalize_place_key(place):
    """表記揺れ(全角/半角、大文字小文字、前後空白)を吸収してキャッシュのヒット率を上げる。"""
    normalized = unicodedata.normalize("NFKC", place).strip().casefold()
    return " ".join(normalized.split())


def load_geocode_cache():
    if os.path.exists(GEOCODE_CACHE_FILE):
        try:
            with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_geocode_cache(cache):
    with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def geocode_place(place, cache):
    key = normalize_place_key(place)
    if key in cache:
        return cache[key]

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": place,
        "format": "json",
        "limit": 1,
        "accept-language": NOMINATIM_ACCEPT_LANGUAGE,
    }
    headers = {"User-Agent": NOMINATIM_USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        results = resp.json()
        time.sleep(NOMINATIM_DELAY_SEC)  # Nominatim利用規約: 1秒1回厳守

        if results:
            coords = {"lat": float(results[0]["lat"]), "lng": float(results[0]["lon"])}
            cache[key] = coords
            return coords
        else:
            # 失敗はキャッシュに永続化しない(一時的な問題かもしれないので次回また試す)
            return None
    except Exception as e:
        print(f"  [WARN] ジオコーディング失敗 '{place}': {e}")
        time.sleep(NOMINATIM_DELAY_SEC)
        return None


def jitter_duplicate_coords(lat, lng, occurrence_index):
    """同じ地名(=同じ座標)の記事が複数あるとき、ピンが完全に重ならないよう
    ひまわりの種状(黄金角スパイラル)にわずかにずらす。
    都市代表点のジオコーディング自体が元々数百m〜数kmの誤差を持つため、
    この程度のずらしは位置精度を実質的に悪化させるものではない。
    """
    if occurrence_index <= 0:
        return lat, lng

    angle_rad = math.radians(occurrence_index * GOLDEN_ANGLE_DEG)
    radius = DUPLICATE_COORD_JITTER_DEGREES * math.sqrt(min(occurrence_index, 12))

    lat_offset = radius * math.cos(angle_rad)
    # 経度方向は緯度が高いほど同じ度数でも実距離が縮むため、cos(緯度)で補正する。
    lng_scale = math.cos(math.radians(lat)) if abs(lat) < 89 else 1.0
    lng_offset = (radius * math.sin(angle_rad)) / max(lng_scale, 0.01)

    return lat + lat_offset, lng + lng_offset


# ---------------------------------------------------------------------------
# ⑤ 座標付与 + 前回分・スポンサーピンとのマージ + JSON出力
# ---------------------------------------------------------------------------
def _valid_http_url(url):
    return isinstance(url, str) and (url.startswith("http://") or url.startswith("https://"))


def _resolve_google_news_url(url):
    """Google Newsの中継URL(news.google.com/rss/articles/... や /read/...)を、
    実際の記事ページのURLに解決する。Google News RSSのlinkはJavaScriptで最終URLへ
    飛ぶ中継ページで、requestsのHTTPリダイレクト追跡だけでは実記事に到達できない
    (中継ページ自体にはog:imageが無いので、従来はog:image取得が常に失敗していた)。
    Googleの内部エンドポイント(batchexecute)を使って最終URLを取り出す。

    ※これはGoogleの公開APIではなく内部実装に依存するため、Google側の仕様変更で
      将来動かなくなる可能性がある。その場合でも例外を握りつぶしてNoneを返し、
      パイプライン全体は止めず「画像なし」に自然にフォールバックする
      (config.py の RESOLVE_GOOGLE_NEWS_URLS を False にすれば無効化できる)。

    解決できたら実URL文字列、できなければNoneを返す。中継URLでなければ元のurlを返す。"""
    try:
        parsed = urlparse(url)
        if parsed.hostname not in ("news.google.com",):
            return url  # そもそもGoogle News中継URLでなければそのまま
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2 or parts[-2] not in ("articles", "read"):
            return url
        article_id = parts[-1]

        # ステップ1: 中継ページを取得し、batchexecuteに必要な署名(sig)と
        # タイムスタンプ(ts)を抜き出す。
        r = requests.get(
            f"https://news.google.com/rss/articles/{article_id}",
            headers=NEWS_REQUEST_HEADERS,
            timeout=GOOGLE_NEWS_RESOLVE_TIMEOUT_SEC,
        )
        if r.status_code != 200:
            return None
        sig_m = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts_m = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if not sig_m or not ts_m:
            return None
        signature = sig_m.group(1)
        timestamp = ts_m.group(1)

        # ステップ2: batchexecuteに問い合わせて最終URLを得る。
        inner = json.dumps([
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            article_id, int(timestamp), signature,
        ])
        freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
        resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": NEWS_REQUEST_HEADERS["User-Agent"],
            },
            data={"f.req": freq},
            timeout=GOOGLE_NEWS_RESOLVE_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            return None
        # レスポンスは )]}' で始まる特殊なJSON。実URLは "garturlres" の直後に入る。
        text = resp.text
        m = re.search(r'\\"garturlres\\",\\"(http[^\\"]+)\\"', text)
        if m:
            return m.group(1).encode("utf-8").decode("unicode_escape")
        # フォールバック: エスケープされたhttp(s) URLを素朴に拾う。
        m2 = re.search(r'(https?:\\/\\/[^\\"]+)', text)
        if m2:
            return m2.group(1).replace("\\/", "/")
        return None
    except Exception:
        return None


def fetch_real_article_image(url):
    """記事ページ(Google Newsの中継URLの先)からog:image(実際の記事のSNSシェア画像)を
    軽量に取得する。記事全文をダウンロードする必要はなく、通常<head>付近にog:imageが
    あるため、REAL_IMAGE_FETCH_MAX_BYTESぶんだけ読んで打ち切ることで負荷・時間を
    抑えている。失敗(タイムアウト・403・og:image無し等)は珍しくないため、パイプライン
    全体は止めない。

    戻り値: (image_url_or_None, reason)
    reason は診断用の理由コード(build_new_items側で集計してログに出す)。
    2026-07-05にこの実画像取得機能を入れて以来、本番で成功率0%が続いており
    (ブラウザでの手動検証ではGoogle Newsのリダイレクト自体は正常なHTTPリダイレクトで、
    リンク先ページにも普通にog:imageがあることまでは確認済み)、原因がタイムアウトなのか
    403などのブロックなのかog:image不在なのか、ログだけでは切り分けられなかった。
    そのため理由コードを返すようにして、次回の本番実行ログで内訳を集計できるようにした。"""
    if not _valid_http_url(url):
        return None, "invalid_url"

    # Google Newsの中継URLは実記事へJavaScriptで飛ぶだけでog:imageを持たないため、
    # まず実記事URLへ解決する(解決できなければog:imageは取りようがないので理由を明示)。
    target_url = url
    if RESOLVE_GOOGLE_NEWS_URLS and "news.google.com" in url:
        resolved = _resolve_google_news_url(url)
        if not resolved or not _valid_http_url(resolved) or "news.google.com" in resolved:
            return None, "gnews_resolve_failed"
        target_url = resolved

    try:
        resp = requests.get(
            target_url,
            timeout=REAL_IMAGE_FETCH_TIMEOUT_SEC,
            headers=NEWS_REQUEST_HEADERS,
            allow_redirects=True,
            stream=True,
        )
        if resp.status_code != 200:
            reason = f"http_{resp.status_code}"
            resp.close()
            return None, reason

        chunk = b""
        for part in resp.iter_content(chunk_size=8192):
            chunk += part
            if len(chunk) >= REAL_IMAGE_FETCH_MAX_BYTES:
                break
        resp.close()

        html = chunk.decode("utf-8", errors="ignore")
        for pattern in _OG_IMAGE_PATTERNS:
            m = pattern.search(html)
            if m:
                img_url = m.group(1).strip()
                if _valid_http_url(img_url):
                    return img_url, "ok"
        return None, "no_og_image_tag"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.SSLError:
        return None, "ssl_error"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.TooManyRedirects:
        return None, "too_many_redirects"
    except Exception as e:
        return None, f"other:{type(e).__name__}"


def build_new_items(selected, cache):
    """今回新しく取得した記事を、座標付きの出力用アイテムに変換する
    (idはまだ振らない。前回分とマージした後にまとめて振り直す)。"""
    items = []
    coord_seen_count = {}
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # og:image取得(記事ごとに最大REAL_IMAGE_FETCH_TIMEOUT_SEC秒かかりうる)は、下の
    # ジオコーディングの逐次ループとは別に、先にまとめて並列で済ませておく。
    # ジオコーディング(Nominatim)は利用規約上1.1秒/件を守る必要があり並列化できないが、
    # og:imageの取得先は記事ごとにバラバラなニュースサイトなので、並列化してもポリシー上
    # 問題になりにくい。これをやらないと、新規記事が多い回に"1.1秒+最大4秒"が記事数分
    # そのまま積み上がり、パイプライン全体が60分(cronの実行間隔)を超えて次の定期実行と
    # 衝突・キャンセルされ続けるリスクがある。
    urls_needing_fetch = []
    for a in selected:
        url = a.get("url", "")
        if url and not _valid_http_url(a.get("socialimage")):
            urls_needing_fetch.append(url)

    real_image_by_url = {}
    if urls_needing_fetch:
        reason_counts = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=REAL_IMAGE_FETCH_MAX_WORKERS) as executor:
            future_to_url = {
                executor.submit(fetch_real_article_image, u): u for u in urls_needing_fetch
            }
            for future in concurrent.futures.as_completed(future_to_url):
                u = future_to_url[future]
                try:
                    img_url, reason = future.result()
                except Exception as e:
                    img_url, reason = None, f"future_exception:{type(e).__name__}"
                real_image_by_url[u] = img_url
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        # 2026-07-07: 実画像取得が本番で0%成功が続いていた問題を切り分けるための
        # 診断ログ。原因(タイムアウト/403等のブロック/og:image不在/その他例外)の
        # 内訳がここに出るので、次回の本番実行ログを見れば根本原因が特定できるはず。
        ok_count = reason_counts.get("ok", 0)
        print(f"  [IMAGE] og:image取得: {ok_count}/{len(urls_needing_fetch)}件成功")
        for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
            if reason != "ok":
                print(f"    [IMAGE] 失敗内訳 {reason}: {count}件")

    for a in selected:
        place = a["ai_place"]
        coords = geocode_place(place, cache)
        if not coords:
            continue  # 座標が取れなければピンにしない

        place_key = normalize_place_key(place)
        occurrence = coord_seen_count.get(place_key, 0)
        coord_seen_count[place_key] = occurrence + 1
        lat, lng = jitter_duplicate_coords(coords["lat"], coords["lng"], occurrence)

        genre = a["ai_genre"]
        color = GENRE_COLORS.get(genre, GENRE_COLORS["その他"])

        url = a.get("url", "")
        # まずRSS自体に画像があればそれを使う(通常は無い)。無ければ上で並列取得しておいた
        # og:image(実際の記事のSNSシェア画像)を使う。それも取れなければランダムな
        # プレースホルダーは出さず、画像なし(None)として扱う
        # (記事と無関係な写真が表示されるのは誤解を招く、という指摘を受けての変更)。
        social_image = a.get("socialimage")
        if _valid_http_url(social_image):
            image_url = social_image
        else:
            image_url = real_image_by_url.get(url)

        original_title = a.get("title", "")
        display_title = a.get("ai_title_ja") or original_title

        items.append({
            "location": {"lat": lat, "lng": lng, "name": place},
            "category": genre,
            "title": display_title,
            "originalTitle": original_title,
            "sourceName": a.get("domain", ""),
            "aiSummary": a["ai_summary"],
            "aiSummaryIsHeadlineOnly": True,  # フロントで「見出しのみからのAI推測」と明示するためのフラグ
            "publishedAt": a.get("published_at"),
            "firstSeenAt": now_iso,  # このパイプラインが初めてこの記事を捉えた時刻
            "url": url,
            "color": color,
            "imageUrl": image_url,
            "isSponsored": False,
        })

    return items


def build_sponsor_items(now_iso):
    """config.py の SPONSOR_PINS を出力用アイテムに変換する。
    ニュースのように期限切れで消えることはなく、毎回config.pyの内容がそのまま
    反映される(前回分の蓄積とは無関係に、常に最新のSPONSOR_PINSを使う)。"""
    items = []
    for idx, s in enumerate(SPONSOR_PINS):
        lat = s.get("lat")
        lng = s.get("lng")
        if lat is None or lng is None:
            print(f"  [WARN] SPONSOR_PINS[{idx}]に lat/lng が無いためスキップします")
            continue
        name = s.get("name", "広告")
        url = s.get("url", "#")
        image_url = s.get("imageUrl") or f"{PLACEHOLDER_IMAGE_BASE}/sponsor{idx}/800/600"
        items.append({
            "location": {"lat": lat, "lng": lng, "name": s.get("place", "")},
            "category": "PR",
            "title": name,
            "originalTitle": name,
            "sourceName": "",
            "aiSummary": s.get("message", ""),
            "aiSummaryIsHeadlineOnly": False,
            "publishedAt": None,
            "firstSeenAt": now_iso,
            "url": url,
            "color": s.get("color", "#eab308"),
            "imageUrl": image_url,
            "isSponsored": True,
        })
    return items


def load_previous_items():
    """前回までのnews_data.json(gh-pagesから復元されたもの)を読み込む。
    存在しない/壊れている場合は空リストを返す(蓄積無しで今回分のみになるだけで、
    処理自体は継続できる)。スポンサーピンは毎回config.pyから作り直すため、
    ここでは除外して返す(でないと期限管理の対象になってしまう/二重に増え続ける)。"""
    if not os.path.exists(OUTPUT_FILE):
        return [], None
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        if not isinstance(items, list):
            return [], None
        items = [it for it in items if not it.get("isSponsored")]
        return items, data.get("generated_at")
    except Exception as e:
        print(f"  [WARN] 既存{OUTPUT_FILE}の読み込みに失敗、今回の新規分のみで出力します: {e}")
        return [], None


def _parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _effective_timestamp(item, fallback_iso):
    """記事の「実質的な時刻」を求める。firstSeenAt→publishedAtの順に見て、
    どちらも無ければ(旧スキームのデータ等)前回実行のgenerated_atを使う。
    それも無ければ現在時刻(=最新扱い)。"""
    for key in ("firstSeenAt", "publishedAt"):
        dt = _parse_iso(item.get(key))
        if dt:
            return dt
    dt = _parse_iso(fallback_iso)
    return dt or datetime.now(timezone.utc)


def merge_with_previous(new_items, previous_items, previous_generated_at, now_dt):
    """今回の新規記事と、前回までの有効期限内の記事をマージする(スポンサーピンは
    別途build_sponsor_itemsで扱うため、ここにはニュース記事のみが渡ってくる)。
    これにより、1回の実行で集まる記事が少ない時間帯でも、地図全体としては
    常にMAX_PINS_TOTALに近いピンが世界中に散らばっている状態を保てる。"""
    max_age = timedelta(hours=ITEM_MAX_AGE_HOURS)
    new_urls = {it.get("url") for it in new_items if it.get("url")}

    kept_previous = []
    expired = 0
    for it in previous_items:
        url = it.get("url")
        if url and url in new_urls:
            continue  # 同じ記事は今回取得した新しい方を使う

        eff_ts = _effective_timestamp(it, previous_generated_at)
        if now_dt - eff_ts > max_age:
            expired += 1
            continue

        # 旧スキーマ(タイトル和訳/公開日時導入前)のデータでも壊れないよう補完する。
        it.setdefault("originalTitle", it.get("title", ""))
        it.setdefault("aiSummaryIsHeadlineOnly", True)
        it.setdefault("sourceName", "")
        it.setdefault("isSponsored", False)
        it["firstSeenAt"] = eff_ts.isoformat().replace("+00:00", "Z")
        kept_previous.append(it)

    print(f"  [MERGE] 前回までのピンのうち有効期限切れ({ITEM_MAX_AGE_HOURS}時間超)で除外: {expired}件")
    print(f"  [MERGE] 継続ピン: {len(kept_previous)}件 + 新規ピン: {len(new_items)}件")

    combined = new_items + kept_previous
    combined = dedup_similar_titles(combined)

    combined.sort(key=lambda it: _effective_timestamp(it, previous_generated_at), reverse=True)
    return combined


def main():
    if not GROQ_API_KEY:
        print("[ERROR] GROQ_API_KEY が設定されていません。")
        print('        export GROQ_API_KEY=gsk_あなたのキー  を実行してから再度お試しください。')
        sys.exit(1)

    print("=== ① ニュース取得 (Google News RSS) ===")
    articles = collect_all_articles()
    if not articles:
        print("[ERROR] 記事が1件も取得できませんでした。ネットワークの状態を確認してください。")
        sys.exit(1)

    # 前回までに蓄積済みの記事(=news_data.jsonに既にあるURL)は、内容が同じなのに
    # 毎回AI判定をやり直すのは無駄なので、AI呼び出し前にここで除外する。
    # Google Newsの"トップニュース"フィードは同じ記事が何時間も掲載され続ける
    # ことが多く(NEWS_COUNTRIES 60ヶ国超 + LOCAL_CITIES 40都市超で、収集記事数は
    # 1回の実行で数千件規模になりうる)、これをやらないとGroq無料枠(1日14,000回)を
    # 既知記事の再判定だけで使い切ってしまいかねない。副次的に、既知記事の
    # firstSeenAt(蓄積の起点時刻)が再判定のたびに現在時刻へ上書きされて
    # ITEM_MAX_AGE_HOURSのTTLが実質リセットされ続けてしまう問題も、この除外により
    # 併せて防げる(同じURLは常にkept_previous側の元のfirstSeenAtが引き継がれる)。
    print("=== ② 前回分の読み込み + 既知記事の除外(AI判定の節約) ===")
    previous_items, previous_generated_at = load_previous_items()
    known_urls = {it.get("url") for it in previous_items if it.get("url")}
    before_dedup_count = len(articles)
    articles = [a for a in articles if a.get("url") not in known_urls]
    skipped_known = before_dedup_count - len(articles)
    if skipped_known:
        print(f"  [DEDUP] 前回までに処理済みの記事を除外: {skipped_known}件 (新規候補{len(articles)}件)")

    # 2026-07-07: Groq無料枠(トークン毎分/1日あたりのリクエスト数)と、1時間ごとの
    # 実行サイクル(60分)に収めるため、1回でAI判定にかける新規記事数に上限を設ける。
    # シャッフルしてから切り詰めることで、国別トップニュース(先頭)だけでなく
    # アフリカ・中南米などローカルフィード由来の記事も均等に残す。
    if len(articles) > MAX_NEW_ARTICLES_PER_RUN:
        before_cap = len(articles)
        # 単純なシャッフル切り詰めだと、候補数で圧倒的に多いトップニュースばかりが残り、
        # 数の少ないローカル記事がAI判定に到達しにくい。LOCAL_SELECT_RATIOと同じ比率で
        # ローカルを優先的に残しつつトップの枠も確保して切り詰める(最終選抜と方針を揃える)。
        local_cands = [a for a in articles if a.get("_source_type") == "local"]
        top_cands = [a for a in articles if a.get("_source_type") != "local"]
        articles = _split_by_ratio(local_cands, top_cands, MAX_NEW_ARTICLES_PER_RUN, LOCAL_SELECT_RATIO)
        n_local = sum(1 for a in articles if a.get("_source_type") == "local")
        print(f"  [LIMIT] 新規候補{before_cap}件をGroq無料枠に合わせて{len(articles)}件に制限"
              f"(ローカル{n_local}/トップ{len(articles) - n_local})")

    print("=== ③ AI要約・ジャンル・地名判定・タイトル和訳 (Groq) ===")
    if articles:
        enriched = enrich_articles_with_ai(articles)
        if not enriched:
            print("  [WARN] 新規記事はあったがAI判定を通過した記事が0件でした"
                  "(Groqのレート制限/障害の可能性)。今回は新規ピン無しで前回分のみ出力します。")
    else:
        print("  [INFO] 新規記事が無かったためAI判定はスキップします。")
        enriched = []

    print("=== ④ 重複除去 + 地域ノルマで選抜 ===")
    selected = select_by_region_quota(enriched) if enriched else []

    print("=== ⑤ 座標変換 (Nominatim) ===")
    cache = load_geocode_cache()
    try:
        new_items = build_new_items(selected, cache) if selected else []
    finally:
        # 途中で失敗しても、そこまでに得たジオコーディング結果は必ずキャッシュに残す。
        save_geocode_cache(cache)

    print("=== ⑥ 前回分・スポンサーピンとマージしてJSON出力 ===")
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")
    merged = merge_with_previous(new_items, previous_items, previous_generated_at, now_dt)

    sponsor_items = build_sponsor_items(now_iso)
    if sponsor_items:
        room = max(MAX_PINS_TOTAL - len(sponsor_items), 0)
        if len(merged) > room:
            dropped = len(merged) - room
            print(f"  [MERGE] スポンサーピン{len(sponsor_items)}件分の枠を確保するため、"
                  f"最も古い{dropped}件を追加で除外")
        merged = merged[:room]
        final_items = sponsor_items + merged
        print(f"  [MERGE] スポンサーピン: {len(sponsor_items)}件")
    else:
        if len(merged) > MAX_PINS_TOTAL:
            dropped = len(merged) - MAX_PINS_TOTAL
            print(f"  [MERGE] 合計{len(merged)}件が上限{MAX_PINS_TOTAL}件を超えたため、最も古い{dropped}件を除外")
            merged = merged[:MAX_PINS_TOTAL]
        final_items = merged

    for idx, it in enumerate(final_items, start=1):
        it["id"] = idx

    next_update_dt = now_dt + timedelta(minutes=UPDATE_INTERVAL_MINUTES)
    payload = {
        "generated_at": now_iso,
        "next_update_at": next_update_dt.isoformat().replace("+00:00", "Z"),
        "count": len(final_items),
        "items": final_items,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  {OUTPUT_FILE} に {len(final_items)}件 書き出し完了(新規{len(new_items)}件を含む)")


if __name__ == "__main__":
    main()
