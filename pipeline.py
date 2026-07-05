# -*- coding: utf-8 -*-
"""
pipeline.py
===========
① ニュース取得(Google News RSS) → ② AI要約・ジャンル・地名判定(Groq)
→ ③ 重複除去・地域ノルマで選抜 → ④ 座標変換(Nominatim) → ⑤ news_data.json 出力

設定はすべて config.py にまとめてあるので、挙動を変えたいときはそちらを編集する。
"""

import difflib
import json
import os
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from config import (
    AI_MAX_RETRIES,
    AI_MODEL,
    ARTICLES_PER_AI_BATCH,
    DUPLICATE_TITLE_SIMILARITY_THRESHOLD,
    GENRE_COLORS,
    GEOCODE_CACHE_FILE,
    GROQ_API_KEY,
    GROQ_REQUEST_DELAY_SEC,
    MAX_PINS_TOTAL,
    NEWS_COUNTRIES,
    NEWS_MAX_ITEMS_PER_COUNTRY,
    NEWS_MAX_RETRIES,
    NEWS_MAX_RETRY_WAIT_SEC,
    NEWS_REQUEST_DELAY_SEC,
    NEWS_REQUEST_TIMEOUT_SEC,
    NEWS_RSS_URL_TEMPLATE,
    NOMINATIM_DELAY_SEC,
    NOMINATIM_USER_AGENT,
    OUTPUT_FILE,
    PLACEHOLDER_IMAGE_BASE,
    REGION_QUOTAS,
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


# ---------------------------------------------------------------------------
# ① ニュース取得 (Google News RSS: 国・言語ごとの「トップニュース」フィード)
# ---------------------------------------------------------------------------
def _parse_news_rss(xml_bytes, country_label):
    """Google News RSSのXMLをパースして記事リストに変換する。"""
    articles = []
    root = ET.fromstring(xml_bytes)
    items = root.findall("./channel/item")

    for item in items[:NEWS_MAX_ITEMS_PER_COUNTRY]:
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")

        raw_title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        domain = (source_el.text or "").strip() if source_el is not None else ""

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
                all_articles.append(a)
                added += 1
        print(f"          -> {added}件追加(重複除く)")

        time.sleep(NEWS_REQUEST_DELAY_SEC)

    print(f"  [FETCH] 合計候補: {len(all_articles)}件")
    return all_articles


# ---------------------------------------------------------------------------
# ② AI要約・ジャンル・地名判定 (Groq)
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
        "3. place: 地図でジオコーディングできる具体的な地名(できれば都市名。"
        "国名しか分からない場合は国名でもよい。英語の一般的な地名表記で統一すること"
        "(例: '東京' ではなく 'Tokyo, Japan'、'ニューヨーク' ではなく 'New York, USA')。"
        "タイトルから具体的な場所が全く読み取れない場合は空文字\"\"。\n"
        "4. summary_ja: 見出しから読み取れる範囲だけで書いた日本語の説明(40〜80字程度)。"
        "本文を読んでいないので、見出しに無い具体的な数字・固有名詞・引用・詳細を"
        "勝手に推測して付け加えないこと。見出しの内容を平易な日本語で言い換える程度に留める。"
        "見出しが日本語以外の言語で書かれていても、内容を理解した上で日本語で要約すること。\n"
        "\n"
        '出力は必ず {"results": [ {"index": 0, "genre": "...", "region": "...", '
        '"place": "...", "summary_ja": "..."}, ... ] } という形のJSONオブジェクトのみ。'
        "説明文やコードブロック記号は一切付けないこと。"
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
            wait = 2 * (attempt + 1)
            print(f"  [WARN] Groq呼び出し失敗(試行{attempt + 1}): {type(e).__name__}: {e} -> {wait}秒待って再試行")
            time.sleep(wait)

    raise RuntimeError(f"Groq呼び出しが{AI_MAX_RETRIES + 1}回とも失敗: {last_err}")


def enrich_articles_with_ai(articles):
    enriched = []
    total_batches = (len(articles) + ARTICLES_PER_AI_BATCH - 1) // ARTICLES_PER_AI_BATCH

    for b in range(total_batches):
        start = b * ARTICLES_PER_AI_BATCH
        batch = articles[start:start + ARTICLES_PER_AI_BATCH]
        print(f"  [AI] バッチ {b + 1}/{total_batches} ({len(batch)}件)")

        try:
            results = call_groq_batch(batch)
        except Exception as e:
            print(f"  [SKIP] バッチ{b + 1}を諦めて次へ: {e}")
            print(f"         詳細: {traceback.format_exc(limit=2)}")
            continue

        for r in results:
            idx = r.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                continue
            article = batch[idx]

            genre = r.get("genre") or ""
            region = r.get("region") or ""
            place = (r.get("place") or "").strip()
            summary = (r.get("summary_ja") or "").strip()

            # --- AIの返りを検証(不正なジャンル/地域は矯正 or 除外) ---
            if genre not in VALID_GENRES:
                genre = "その他"
            if region not in REGION_QUOTAS:
                continue  # 地域を集計できない記事はノルマ選抜の対象外にする

            # 地名が取れない記事はピンにしない、という設計上のルール
            if not place or not summary:
                continue

            enriched.append({
                **article,
                "ai_genre": genre,
                "ai_region": region,
                "ai_place": place,
                "ai_summary": summary,
            })

        time.sleep(GROQ_REQUEST_DELAY_SEC)

    print(f"  [AI] 判定OK: {len(enriched)}件")
    return enriched


# ---------------------------------------------------------------------------
# ③ 重複除去 + 地域ノルマで選抜
# ---------------------------------------------------------------------------
def dedup_similar_titles(items):
    """同じ出来事を報じた似たタイトルの記事を弾く(O(n^2)だが選抜後の件数=最大MAX_PINS_TOTALなので許容範囲)。"""
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


def select_by_region_quota(enriched):
    by_region = {}
    for a in enriched:
        by_region.setdefault(a["ai_region"], []).append(a)

    selected = []
    for region, quota in REGION_QUOTAS.items():
        candidates = by_region.get(region, [])
        picked = candidates[:quota]
        selected.extend(picked)
        print(f"  [SELECT] {region}: {len(picked)}/{quota}件")

    if len(selected) < MAX_PINS_TOTAL:
        selected_urls = {a.get("url") for a in selected}
        leftovers = [a for a in enriched if a.get("url") not in selected_urls]
        need = MAX_PINS_TOTAL - len(selected)
        selected.extend(leftovers[:need])

    selected = dedup_similar_titles(selected)
    selected = selected[:MAX_PINS_TOTAL]
    print(f"  [SELECT] 最終選抜: {len(selected)}件 (上限{MAX_PINS_TOTAL})")
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
    params = {"q": place, "format": "json", "limit": 1}
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


# ---------------------------------------------------------------------------
# ⑤ JSON出力
# ---------------------------------------------------------------------------
def _valid_http_url(url):
    return isinstance(url, str) and (url.startswith("http://") or url.startswith("https://"))


def build_output(selected, cache):
    output = []
    next_id = 1

    for a in selected:
        place = a["ai_place"]
        coords = geocode_place(place, cache)
        if not coords:
            continue  # 座標が取れなければピンにしない

        genre = a["ai_genre"]
        color = GENRE_COLORS.get(genre, GENRE_COLORS["その他"])

        # Google News RSSには記事画像が含まれないため、基本的にプレースホルダー画像を使う。
        social_image = a.get("socialimage")
        image_url = social_image if _valid_http_url(social_image) else f"{PLACEHOLDER_IMAGE_BASE}/{next_id}/800/600"

        output.append({
            "id": next_id,
            "location": {"lat": coords["lat"], "lng": coords["lng"], "name": place},
            "category": genre,
            "title": a.get("title", ""),
            "aiSummary": a["ai_summary"],
            "aiSummaryIsHeadlineOnly": True,  # フロントで「見出しのみからのAI推測」と明示するためのフラグ
            "url": a.get("url", ""),
            "color": color,
            "imageUrl": image_url,
        })
        next_id += 1

    return output


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

    print("=== ② AI要約・ジャンル・地名判定 (Groq) ===")
    enriched = enrich_articles_with_ai(articles)
    if not enriched:
        print("[ERROR] AI判定を通過した記事が0件でした。config.pyのAI_MODELやプロンプトを確認してください。")
        sys.exit(1)

    print("=== ③ 重複除去 + 地域ノルマで選抜 ===")
    selected = select_by_region_quota(enriched)

    print("=== ④ 座標変換 (Nominatim) ===")
    cache = load_geocode_cache()
    try:
        output = build_output(selected, cache)
    finally:
        # 途中で失敗しても、そこまでに得たジオコーディング結果は必ずキャッシュに残す。
        save_geocode_cache(cache)

    print("=== ⑤ JSON出力 ===")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(output),
        "items": output,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  {OUTPUT_FILE} に {len(output)}件 書き出し完了")


if __name__ == "__main__":
    main()
