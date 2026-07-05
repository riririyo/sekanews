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

# GDELT DOC APIの1リクエストあたりの最大取得件数(API仕様上の上限)。
# 実測で判明した重要な問題: GitHub Actionsの共有IPは既に他の無数のワークフローが
# GDELTを叩いているらしく、リクエスト数が多いほど429(レート制限)に当たりやすい。
# そのため「国ごとに細かくリクエストを分ける」のをやめ、後述のFETCH_GROUPSで
# 数回の大きなリクエストにまとめてリクエスト総数そのものを減らす方針にしてある。
GDELT_MAX_RECORDS_CAP = 250

# GDELTへのリクエスト間隔(秒)。
GDELT_REQUEST_DELAY_SEC = 5.0

# GDELT取得が失敗した場合のリトライ回数。待ち時間は 15s, 30s, 60s, 120s, 120s... と
# 指数的に伸びて120秒で頭打ちにしてある(GDELTの429は数秒待つ程度では解消しないことが
# 多いため長めに待つが、際限なく伸ばすとジョブが終わらなくなるので上限を設けている)。
GDELT_MAX_RETRIES = 5
GDELT_MAX_RETRY_WAIT_SEC = 120

# 地域別クエリが軒並み429で失敗しても最低限の記事が確保できるよう、国コードで絞らない
# 「全世界の英語ニュース」も1回まとめて取得しておく(地域はAIの判定(ai_region)で
# 事後的に割り振られるので、地域選抜のロジックには影響しない)。
GDELT_GLOBAL_POOL_MAX_RECORDS = 250

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

# ---------------------------------------------------------------------------
# GDELTから記事を取ってくる際の「取得グループ」。
# ---------------------------------------------------------------------------
# 重要: 以前は上のREGION_QUOTASと同じ12分類でGDELTに1回ずつ(=12回)リクエストして
# いたが、GitHub Actionsの共有IPからのアクセスは元々429(レート制限)に当たりやすく、
# リクエスト回数が多いほど失敗が増えることが実測でわかった。
# そこで「取得(何回GDELTを叩くか)」と「選抜(どの地域に何件割り当てるか)」を分離し、
# 取得は下記の数個の大きなグループにまとめてリクエスト総数を減らす。どのグループの
# 記事も、実際の地域振り分けはAIの判定(ai_region)で行われる(上のREGION_QUOTASの
# 12分類に従う)ので、ここのグループ分けは「何をまとめて1回のリクエストにするか」
# だけの都合であり、REGION_QUOTASのキーと一致している必要はない。
# 各グループにはできるだけ多くの国コードを詰め込むことで、南アフリカだけでなく
# ザンビアやジンバブエ、ブラジルだけでなくボリビアやエクアドルなど、あまり報道され
# ない国のニュースも拾える確率を上げている(FIPS 10-4コードはうろ覚えの部分もあるので、
# 狙った国が全然出てこない場合はここのコードを見直すとよい)。
FETCH_GROUPS = {
    "日本": "sourcecountry:JA",
    "アジア(東・東南・南)": (
        "sourcecountry:CH OR sourcecountry:KS OR sourcecountry:TW OR sourcecountry:KN OR "
        "sourcecountry:MG OR sourcecountry:ID OR sourcecountry:TH OR sourcecountry:VM OR "
        "sourcecountry:RP OR sourcecountry:MY OR sourcecountry:SN OR sourcecountry:CB OR "
        "sourcecountry:LA OR sourcecountry:BM OR sourcecountry:IN OR sourcecountry:PK OR "
        "sourcecountry:BG OR sourcecountry:CE OR sourcecountry:NP OR sourcecountry:BT"
    ),
    "中東・アフリカ": (
        "sourcecountry:SA OR sourcecountry:IR OR sourcecountry:IZ OR sourcecountry:IS OR "
        "sourcecountry:TU OR sourcecountry:JO OR sourcecountry:LE OR sourcecountry:SY OR "
        "sourcecountry:YM OR sourcecountry:KU OR sourcecountry:EG OR sourcecountry:LY OR "
        "sourcecountry:AG OR sourcecountry:MO OR sourcecountry:TS OR sourcecountry:SF OR "
        "sourcecountry:KE OR sourcecountry:ET OR sourcecountry:TZ OR sourcecountry:UG OR "
        "sourcecountry:NI OR sourcecountry:ZA OR sourcecountry:ZI OR sourcecountry:MZ OR "
        "sourcecountry:RW OR sourcecountry:SO OR sourcecountry:MA OR sourcecountry:GH OR "
        "sourcecountry:CM OR sourcecountry:SG OR sourcecountry:IV OR sourcecountry:BC OR "
        "sourcecountry:WA"
    ),
    "欧州": (
        "sourcecountry:UK OR sourcecountry:FR OR sourcecountry:GM OR sourcecountry:IT OR "
        "sourcecountry:SP OR sourcecountry:NL OR sourcecountry:BE OR sourcecountry:RS OR "
        "sourcecountry:PL OR sourcecountry:UP OR sourcecountry:RO OR sourcecountry:SW OR "
        "sourcecountry:SZ OR sourcecountry:AU OR sourcecountry:GR OR sourcecountry:PO OR "
        "sourcecountry:HU OR sourcecountry:DA OR sourcecountry:NO OR sourcecountry:FI OR "
        "sourcecountry:IC OR sourcecountry:EI"
    ),
    "南北アメリカ・オセアニア": (
        "sourcecountry:US OR sourcecountry:CA OR sourcecountry:MX OR sourcecountry:BR OR "
        "sourcecountry:AR OR sourcecountry:CI OR sourcecountry:CO OR sourcecountry:PE OR "
        "sourcecountry:VE OR sourcecountry:BL OR sourcecountry:EC OR sourcecountry:UY OR "
        "sourcecountry:PM OR sourcecountry:GT OR sourcecountry:CU OR sourcecountry:DR OR "
        "sourcecountry:AS OR sourcecountry:NZ"
    ),
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
