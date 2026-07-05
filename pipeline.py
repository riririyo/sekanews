# -*- coding: utf-8 -*-
"""
pipeline.py
===========
① ニュース取得(GDELT) → ② AI要約・ジャンル・地名判定(Groq)
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
from datetime import datetime, timezone

import requests

from config import (
    AI_MAX_RETRIES,
    AI_MODEL,
    ARTICLES_PER_AI_BATCH,
    DUPLICATE_TITLE_SIMILARITY_THRESHOLD,
    GDELT_GLOBAL_POOL_MAX_RECORDS,
    GDELT_MAX_RECORDS_CAP,
    GDELT_MAX_RECORDS_PER_REGION,
    GDELT_MAX_RETRIES,
    GDELT_REQUEST_DELAY_SEC,
    GDELT_TIMESPAN,
    GENRE_COLORS,
    GEOCODE_CACHE_FILE,
    GROQ_API_KEY,
    GROQ_REQUEST_DELAY_SEC,
    MAX_PINS_TOTAL,
    NOMINATIM_DELAY_SEC,
    NOMINATIM_USER_AGENT,
    OUTPUT_FILE,
    PLACEHOLDER_IMAGE_BASE,
    REGION_QUERIES,
    REGION_QUOTAS,
    VALID_GENRES,
)

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


# ---------------------------------------------------------------------------
# ① ニュース取得 (GDELT)
# ---------------------------------------------------------------------------
def fetch_region_articles(region_name, query_filter, max_records):
    max_records = min(max_records, GDELT_MAX_RECORDS_CAP)
    params = {
        "query": f"({query_filter}) sourcelang:eng",
        "mode": "ArtList",
        "maxrecords": max_records,
        "timespan": GDELT_TIMESPAN,
        "format": "json",
        "sort": "DateDesc",
    }

    last_err = None
    for attempt in range(GDELT_MAX_RETRIES + 1):
        try:
            resp = requests.get(GDELT_ENDPOINT, params=params, timeout=25)
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", []) or []
            for a in articles:
                a["_region_hint"] = region_name
            return articles
        except Exception as e:
            last_err = e
            if attempt < GDELT_MAX_RETRIES:
                wait = 15 * (2 ** attempt)
                print(f"    [WARN] GDELT取得失敗(試行{attempt + 1}, {region_name}): {e} -> {wait}秒待って再試行")
                time.sleep(wait)

    print(f"  [WARN] GDELT取得を諦めます ({region_name}): {last_err}")
    return []


def collect_all_articles():
    all_articles = []
    seen_urls = set()

    print("  [FETCH] 全世界プール(地域絞り込みなし、フォールバック用)")
    global_articles = fetch_region_articles("グローバル", "sourcelang:eng", GDELT_GLOBAL_POOL_MAX_RECORDS)
    added = 0
    for a in global_articles:
        a["_region_hint"] = None
        url = a.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            all_articles.append(a)
            added += 1
    print(f"          -> {added}件追加(重複除く)")
    time.sleep(GDELT_REQUEST_DELAY_SEC)

    for region_name, query_filter in REGION_QUERIES.items():
        quota = REGION_QUOTAS.get(region_name, 0)
        if quota <= 0:
            continue

        max_records = max(quota * GDELT_MAX_RECORDS_PER_REGION, 10)
        print(f"  [FETCH] {region_name} (ノルマ{quota}件 / 候補最大{min(max_records, GDELT_MAX_RECORDS_CAP)}件)")
        articles = fetch_region_articles(region_name, query_filter, max_records)

        added = 0
        for a in articles:
            url = a.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_articles.append(a)
                added += 1
        print(f"          -> {added}件追加(重複除く)")

        time.sleep(GDELT_REQUEST_DELAY_SEC)

    print(f"  [FETCH] 合計候補: {len(all_articles)}件")
    return all_articles


# ---------------------------------------------------------------------------
# ② AI要約・ジャンル・地名判定 (Groq)
# ---------------------------------------------------------------------------
def _build_prompt(batch):
    items_text = "\n".join(
        f"{i}. title: {a.get('title', '')}\n"
        f"   domain: {a.get('domain', '')}\n"
        f"   参考地域ヒント: {a.get('_region_hint', '')}"
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
        "勝手に推測して付け加えないこと。見出しの内容を平易な日本語で言い換える程度に留める。\n"
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
            last_err = e
            wait = 2 * (attempt + 1)
            print(f"  [WARN] Groq呼び出し失敗(試行{attempt + 1}): {type(e).__name__}: {e} -> {wait}秒待って再試行")
            time.sleep(wait)

    raise RuntimeError(f"Groq呼び出しが{AI_MAX_RETRIES + 1}回とも失敗: {last_err}")


def enrich_articles_with_ai(articles):
    enriched = []
    region_mismatch_count = 0
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

            if genre not in VALID_GENRES:
                genre = "その他"
            if region not in REGION_QUOTAS:
                continue

            if article.get("_region_hint") and region != article["_region_hint"]:
                region_mismatch_count += 1

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
    if region_mismatch_count:
        print(f"  [INFO] GDELTの地域ヒントとAI判定地域が食い違った記事: {region_mismatch_count}件(AI側を採用)")
    return enriched


# ---------------------------------------------------------------------------
# ③ 重複除去 + 地域ノルマで選抜
# ---------------------------------------------------------------------------
def dedup_similar_titles(items):
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
        time.sleep(NOMINATIM_DELAY_SEC)

        if results:
            coords = {"lat": float(results[0]["lat"]), "lng": float(results[0]["lon"])}
            cache[key] = coords
            return coords
        else:
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
            continue

        genre = a["ai_genre"]
        color = GENRE_COLORS.get(genre, GENRE_COLORS["その他"])

        social_image = a.get("socialimage")
        image_url = social_image if _valid_http_url(social_image) else f"{PLACEHOLDER_IMAGE_BASE}/{next_id}/800/600"

        output.append({
            "id": next_id,
            "location": {"lat": coords["lat"], "lng": coords["lng"], "name": place},
            "category": genre,
            "title": a.get("title", ""),
            "aiSummary": a["ai_summary"],
            "aiSummaryIsHeadlineOnly": True,
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

    print("=== ① ニュース取得 (GDELT) ===")
    articles = collect_all_articles()
    if not articles:
        print("[ERROR] 記事が1件も取得できませんでした。ネットワークかGDELTの状態を確認してください。")
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
