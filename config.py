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
# ② ニュース取得 (Google News RSS / 無料・キー不要・国別)
# ---------------------------------------------------------------------------
# 重要な経緯: 以前はGDELT 2.0 DOC APIを使っていたが、実際にGitHub Actions上で
# 動かしたところ、クエリをどう工夫しても(1国だけの単純なクエリでも)ほぼ100%
# 429(レート制限)になることが実測でわかった。GDELT側がGitHub Actionsのような
# クラウドの共有IPそのものを広範囲にブロックしている可能性が高く、リクエストの
# 減らし方やリトライ待機時間をいくら調整しても解決しなかった(実際に検証したところ、
# 6グループ×5回リトライ=合計30リクエストが「全て」429になった)。
# そのため取得元をGoogle News RSS(国・言語ごとの「トップニュース」フィード、
# https://news.google.com/rss?hl=..&gl=..&ceid=.. )に切り替えた。APIキー不要・
# クラウドIPからでも問題なく使え、国ごとに1リクエストで済むため合計リクエスト数も
# 少ない。ただし記事本文への直リンクではなくGoogle Newsの中継URLになる点、
# サムネイル画像が付かない点(→プレースホルダー画像で補う)には留意。
NEWS_RSS_URL_TEMPLATE = "https://news.google.com/rss?hl={hl}&gl={gl}&ceid={ceid}"

# Google Newsへのリクエスト間隔(秒)。GDELTほど神経質になる必要はないが、
# 短時間に連打すると一時的に弾かれることがあるので余裕を持たせてある。
NEWS_REQUEST_DELAY_SEC = 1.5

# 1リクエストのタイムアウト秒数。
NEWS_REQUEST_TIMEOUT_SEC = 10

# 取得が失敗した場合のリトライ回数と待機時間(秒、指数的に増加、上限あり)。
# GDELTでの反省を踏まえ、万一Google News側も塞がっていた場合に備えて短めにしてある
# (1国あたり最大でもリクエストタイムアウト10秒×3回+待機10秒程度に収まるようにし、
# 仮に全滅しても全体の実行時間が際限なく伸びないようにしている)。
NEWS_MAX_RETRIES = 2
NEWS_MAX_RETRY_WAIT_SEC = 10

# 1つの国のフィードから読み込む記事数の上限(Google News RSSは1フィードにつき
# だいたい30〜100件程度を返す。多すぎる場合はここで頭打ちにする)。
NEWS_MAX_ITEMS_PER_COUNTRY = 100

# 取得対象の国・言語の一覧。(表示用ラベル, gl=国コード, hl=言語コード, ceid)。
# ここを増減させることで「どの国から記事を集めてくるか」を調整できる。
# 実際にどの地域のノルマに割り振られるかはAIの判定(ai_region)で決まるため、
# ここに無い国の都市名がAIによって記事から読み取られることもある
# (例: 記事本文中に別の国の地名が出てくる場合など)。
# ザンビア・ボリビアのような、通常あまり報道で目立たない国もあえて含めている。
NEWS_COUNTRIES = [
    # --- 日本 ---
    {"label": "日本", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    # --- 東アジア ---
    {"label": "中国", "gl": "CN", "hl": "zh-Hans", "ceid": "CN:zh-Hans"},
    {"label": "韓国", "gl": "KR", "hl": "ko", "ceid": "KR:ko"},
    {"label": "台湾", "gl": "TW", "hl": "zh-Hant", "ceid": "TW:zh-Hant"},
    # --- 東南アジア ---
    {"label": "インドネシア", "gl": "ID", "hl": "id", "ceid": "ID:id"},
    {"label": "タイ", "gl": "TH", "hl": "th", "ceid": "TH:th"},
    {"label": "ベトナム", "gl": "VN", "hl": "vi", "ceid": "VN:vi"},
    {"label": "フィリピン", "gl": "PH", "hl": "en-PH", "ceid": "PH:en"},
    {"label": "マレーシア", "gl": "MY", "hl": "en-MY", "ceid": "MY:en"},
    {"label": "シンガポール", "gl": "SG", "hl": "en-SG", "ceid": "SG:en"},
    # --- 南アジア ---
    {"label": "インド", "gl": "IN", "hl": "en-IN", "ceid": "IN:en"},
    {"label": "パキスタン", "gl": "PK", "hl": "en-PK", "ceid": "PK:en"},
    {"label": "バングラデシュ", "gl": "BD", "hl": "bn-BD", "ceid": "BD:bn"},
    {"label": "スリランカ", "gl": "LK", "hl": "en-LK", "ceid": "LK:en"},
    {"label": "ネパール", "gl": "NP", "hl": "ne-NP", "ceid": "NP:ne"},
    # --- 中東 ---
    {"label": "サウジアラビア", "gl": "SA", "hl": "ar", "ceid": "SA:ar"},
    {"label": "イスラエル", "gl": "IL", "hl": "en-IL", "ceid": "IL:en"},
    {"label": "トルコ", "gl": "TR", "hl": "tr", "ceid": "TR:tr"},
    {"label": "アラブ首長国連邦", "gl": "AE", "hl": "en-AE", "ceid": "AE:en"},
    {"label": "ヨルダン", "gl": "JO", "hl": "ar", "ceid": "JO:ar"},
    # --- アフリカ北部 ---
    {"label": "エジプト", "gl": "EG", "hl": "ar", "ceid": "EG:ar"},
    {"label": "モロッコ", "gl": "MA", "hl": "fr", "ceid": "MA:fr"},
    {"label": "アルジェリア", "gl": "DZ", "hl": "fr", "ceid": "DZ:fr"},
    {"label": "チュニジア", "gl": "TN", "hl": "fr", "ceid": "TN:fr"},
    # --- アフリカ南部・東部 ---
    {"label": "南アフリカ", "gl": "ZA", "hl": "en-ZA", "ceid": "ZA:en"},
    {"label": "ケニア", "gl": "KE", "hl": "en-KE", "ceid": "KE:en"},
    {"label": "ナイジェリア", "gl": "NG", "hl": "en-NG", "ceid": "NG:en"},
    {"label": "ガーナ", "gl": "GH", "hl": "en-GH", "ceid": "GH:en"},
    {"label": "タンザニア", "gl": "TZ", "hl": "en-TZ", "ceid": "TZ:en"},
    {"label": "ウガンダ", "gl": "UG", "hl": "en-UG", "ceid": "UG:en"},
    {"label": "ザンビア", "gl": "ZM", "hl": "en-ZM", "ceid": "ZM:en"},
    {"label": "ジンバブエ", "gl": "ZW", "hl": "en-ZW", "ceid": "ZW:en"},
    # --- 西欧 ---
    {"label": "イギリス", "gl": "GB", "hl": "en-GB", "ceid": "GB:en"},
    {"label": "フランス", "gl": "FR", "hl": "fr", "ceid": "FR:fr"},
    {"label": "ドイツ", "gl": "DE", "hl": "de", "ceid": "DE:de"},
    {"label": "イタリア", "gl": "IT", "hl": "it", "ceid": "IT:it"},
    {"label": "スペイン", "gl": "ES", "hl": "es", "ceid": "ES:es"},
    {"label": "オランダ", "gl": "NL", "hl": "nl", "ceid": "NL:nl"},
    # --- 東欧・ロシア ---
    {"label": "ロシア", "gl": "RU", "hl": "ru", "ceid": "RU:ru"},
    {"label": "ポーランド", "gl": "PL", "hl": "pl", "ceid": "PL:pl"},
    {"label": "ウクライナ", "gl": "UA", "hl": "uk", "ceid": "UA:uk"},
    {"label": "ルーマニア", "gl": "RO", "hl": "ro", "ceid": "RO:ro"},
    # --- 北米 ---
    {"label": "アメリカ", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "カナダ", "gl": "CA", "hl": "en-CA", "ceid": "CA:en"},
    {"label": "メキシコ", "gl": "MX", "hl": "es-419", "ceid": "MX:es-419"},
    # --- 中南米 ---
    {"label": "ブラジル", "gl": "BR", "hl": "pt-BR", "ceid": "BR:pt-419"},
    {"label": "アルゼンチン", "gl": "AR", "hl": "es-419", "ceid": "AR:es-419"},
    {"label": "コロンビア", "gl": "CO", "hl": "es-419", "ceid": "CO:es-419"},
    {"label": "ペルー", "gl": "PE", "hl": "es-419", "ceid": "PE:es-419"},
    {"label": "ボリビア", "gl": "BO", "hl": "es-419", "ceid": "BO:es-419"},
    {"label": "エクアドル", "gl": "EC", "hl": "es-419", "ceid": "EC:es-419"},
    {"label": "チリ", "gl": "CL", "hl": "es-419", "ceid": "CL:es-419"},
    # --- オセアニア ---
    {"label": "オーストラリア", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "ニュージーランド", "gl": "NZ", "hl": "en-NZ", "ceid": "NZ:en"},
]

# ---------------------------------------------------------------------------
# ③ 地域バランス (合計およそ1000件のピンを狙う設定)
# ---------------------------------------------------------------------------
# 各地域から最低何件ピンを立てたいか(ノルマ)。数字を増減させると、その地域の
# 記事が濃く/薄くなる。ここの合計がMAX_PINS_TOTALの目安。
# 注意: ニュースが少ない地域(オセアニア等)は該当時間帯にノルマ分の記事が
#       存在せず未達になることがある。その場合は他地域のあまり記事で自動的に
#       穴埋めされるので、合計ピン数自体はMAX_PINS_TOTALに近づく。
# なお、この分類はNEWS_COUNTRIESの取得元とは独立していて、実際にどの地域に
# 割り振られるかはAIが記事内容から判定する(ai_region)。
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
# Google News RSSには記事画像が含まれないため、基本的に全記事がこのプレースホルダー
# 画像になる。id をシードにして毎回同じ画像が出るようにしている。
PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"

# 注意: サイトタイトル/サブタイトル(「せかにゅ」「ニュースと地理」)は
# index.html 側に直接書いてある(フロントは静的1ファイルでテンプレート化していないため)。
# 変更する場合は index.html の <title> と #site-title / #site-subtitle を編集すること。
