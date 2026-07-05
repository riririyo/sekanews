# -*- coding: utf-8 -*-
"""
config.py
=========
地図×ニュースアプリのパイプライン設定。基本的にはこのファイルの数字・文字列を
いじるだけで挙動を調整できます。コードの中身(pipeline.py)は触らなくてOKです。
"""

import os

# ---------------------------------------------------------------------------
# ① Groq API (無料枠 1日14,000回)
# ---------------------------------------------------------------------------
# https://console.groq.com で取得したキーを環境変数 GROQ_API_KEY に入れておくこと。
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# 使用するAIモデル。将来Geminiなど別プロバイダに乗り換える場合は、
# ここに加えて pipeline.py の GROQ_ENDPOINT / call_groq_batch() のURLも変更する。
AI_MODEL = "llama-3.3-70b-versatile"

# 1回のAI呼び出しにまとめて渡す記事数。多すぎるとAIの精度が落ちるので8〜10が目安。
ARTICLES_PER_AI_BATCH = 8

# AI呼び出し失敗時のリトライ回数
AI_MAX_RETRIES = 2

# Groqのレート制限(1分あたりのリクエスト数)に引っかからないよう、バッチ間に
# 空ける秒数。無料枠のレート制限はモデル・時期により変わるので、429が頻発する
# ようならここを増やす(https://console.groq.com の Limits ページで確認できる)。
GROQ_REQUEST_DELAY_SEC = 1.2

# ---------------------------------------------------------------------------
# ② ニュース取得 (GDELT 2.0 DOC API / 無料・キー不要)
# ---------------------------------------------------------------------------
# 取得対象の時間範囲。1時間ごと更新を想定し、cronの実行遅延(GitHub Actionsは
# 高負荷時に数分〜十数分ずれることがある)に備えて少し広め(75分)にしてある。
GDELT_TIMESPAN = "75min"

# 各地域につき「ノルマ数 × この倍率」件を候補として取得する(GDELT_MAX_RECORDS_CAPで頭打ち)。
GDELT_MAX_RECORDS_PER_REGION = 3

# GDELT DOC APIの1リクエストあたりの最大取得件数(API仕様上の上限)。
GDELT_MAX_RECORDS_CAP = 250

# GDELTへのリクエスト間隔(秒)。連続アクセスを避けるためのマナー。
GDELT_REQUEST_DELAY_SEC = 1.0

# GDELT取得が失敗した場合のリトライ回数。
GDELT_MAX_RETRIES = 2

# ---------------------------------------------------------------------------
# ③ 地域バランス (合計およそ1000件のピンを狙う設定)
# ---------------------------------------------------------------------------
# 各地域から最低何件ピンを立てたいか(ノルマ)。数字を増減させると、その地域の
# 記事が濃く/薄くなる。ここの合計がMAX_PINS_TOTALの目安。
# 注意: ニュースが少ない地域(オセアニア等)は該当時間帯にノルマ分の記事が
#       存在せず未達になることがある。その場合は他地域のあまり記事で自動的に
#       穴埋めされるので、合計ピン数自体はMAX_PINS_TOTALに近づく。
REGION_QUOTAS = {
    "日本": 90,
    "東アジア": 90,
    "東南アジア": 70,
    "南アジア": 70,
    "中東": 90,
    "アフリカ北部": 50,
    "アフリカ南部・東部": 110,
    "西欧": 110,
    "東欧・ロシア": 70,
    "北米": 130,
    "中南米": 70,
    "オセアニア": 50,
}

# 各地域をGDELTから取得する際の絞り込み条件(sourcecountryはFIPS 10-4の2文字コード)。
# 見た目の記事が偏っていると感じたら、ここの国コードを増減して調整する。
REGION_QUERIES = {
    "日本": "sourcecountry:JA",
    "東アジア": "sourcecountry:CH OR sourcecountry:KS OR sourcecountry:TW OR sourcecountry:KN",
    "東南アジア": "sourcecountry:ID OR sourcecountry:TH OR sourcecountry:VM OR sourcecountry:RP OR sourcecountry:MY OR sourcecountry:SN",
    "南アジア": "sourcecountry:IN OR sourcecountry:PK OR sourcecountry:BG OR sourcecountry:CE",
    "中東": "sourcecountry:SA OR sourcecountry:IR OR sourcecountry:IZ OR sourcecountry:IS OR sourcecountry:TU OR sourcecountry:JO",
    "アフリカ北部": "sourcecountry:EG OR sourcecountry:LY OR sourcecountry:AG OR sourcecountry:MO OR sourcecountry:TS",
    "アフリカ南部・東部": "sourcecountry:SF OR sourcecountry:KE OR sourcecountry:ET OR sourcecountry:TZ OR sourcecountry:UG OR sourcecountry:NI",
    "西欧": "sourcecountry:UK OR sourcecountry:FR OR sourcecountry:GM OR sourcecountry:IT OR sourcecountry:SP OR sourcecountry:NL OR sourcecountry:BE",
    "東欧・ロシア": "sourcecountry:RS OR sourcecountry:PL OR sourcecountry:UP OR sourcecountry:RO",
    "北米": "sourcecountry:US OR sourcecountry:CA OR sourcecountry:MX",
    "中南米": "sourcecountry:BR OR sourcecountry:AR OR sourcecountry:CI OR sourcecountry:CO OR sourcecountry:PE OR sourcecountry:VE",
    "オセアニア": "sourcecountry:AS OR sourcecountry:NZ",
}

# ---------------------------------------------------------------------------
# ④ ピンの総数・ジャンル
# ---------------------------------------------------------------------------
MAX_PINS_TOTAL = 1000

# AIに判定させるジャンルの許容リスト。ここに無い値が返ってきたら"その他"に矯正する。
VALID_GENRES = ["テック", "経済", "エンタメ", "政治", "環境", "スポーツ", "科学", "事件・事故", "その他"]

# ジャンルごとのピンの色(フロントのTailwindカラーに合わせてある)。
GENRE_COLORS = {
    "テック": "#3b82f6",      # blue-500
    "経済": "#10b981",        # emerald-500
    "エンタメ": "#ec4899",    # pink-500
    "政治": "#ef4444",        # red-500
    "環境": "#22c55e",        # green-500
    "スポーツ": "#f59e0b",    # amber-500
    "科学": "#8b5cf6",        # violet-500
    "事件・事故": "#64748b",  # slate-500
    "その他": "#9ca3af",      # gray-400
}

# 似たタイトルの記事(同じ出来事を別ソースが報じたもの)を重複ピンとして弾くための
# 類似度しきい値(0〜1、difflib.SequenceMatcher基準)。1に近いほど判定が厳しくなる。
DUPLICATE_TITLE_SIMILARITY_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# ⑤ ジオコーディング (Nominatim / OpenStreetMap)
# ---------------------------------------------------------------------------
# 利用規約で必須。アプリ名と連絡先メールアドレスを入れておくこと。
NOMINATIM_USER_AGENT = "WorldNewsMapApp/1.0 (rimocon.rimocon.rimocon@gmail.com)"

# Nominatimは「1秒に1回まで」が利用規約上のルール。余裕を持って1.1秒にしてある。
# なお、無人の定期実行から継続的にアクセスし続ける使い方はNominatimの利用ポリシー上
# 「bulk geocoding」に近く、本来はセルフホストや商用ジオコーダー推奨とされている。
# geocode_cache.json による再利用でリクエスト数そのものを減らすのが最大の対策。
NOMINATIM_DELAY_SEC = 1.1

GEOCODE_CACHE_FILE = "geocode_cache.json"

# ---------------------------------------------------------------------------
# ⑥ 出力・その他
# ---------------------------------------------------------------------------
OUTPUT_FILE = "news_data.json"

# 記事にサムネイル画像URLを持たせたい場合のプレースホルダー(無料・キー不要)。
# GDELTのsocialimage(実際の記事画像)が取れなかった記事にのみ使われるフォールバック。
# id をシードにして毎回同じ画像が出るようにしている。
PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"

# 注意: サイトタイトル/サブタイトル(「せかにゅ」「ニュースと地理」)は
# index.html 側に直接書いてある(フロントは静的1ファイルでテンプレート化していないため)。
# 変更する場合は index.html の <title> と #site-title / #site-subtitle を編集すること。
