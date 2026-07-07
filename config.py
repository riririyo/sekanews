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
ARTICLES_PER_AI_BATCH = 10

# AI呼び出し失敗時のリトライ回数
AI_MAX_RETRIES = 5

# Groqが429(レート制限)のRetry-Afterヘッダーでこの秒数を超える待機を要求してきた
# 場合、再試行そのものを諦めて即座にこのバッチをスキップする。
# 2026-07-07: 実際の本番run(#34)で、Retry-Afterが816秒/823秒(=1日/長時間単位の
# クォータ切れを示唆)なのに、待機時間を60秒に丸めて再試行するコードになっていたため、
# 毎回また429になるだけの再試行をAI_MAX_RETRIES回(6回)繰り返し、1バッチだけで
# 6分(60秒×6回)を無駄にし続けるという不具合があった。短時間の混雑(せいぜい
# 数十秒待てば解消するもの)ならこれまで通り待って再試行する価値があるが、
# それより明らかに長いRetry-Afterは「このrun中には回復しない」合図として扱う。
GROQ_RETRY_GIVE_UP_THRESHOLD_SEC = 90

# Groqのレート制限(1分あたりのリクエスト数)に引っかからないよう、バッチ間に
# 空ける秒数。無料枠のレート制限はモデル・時期により変わるので、429が頻発する
# ようならここを増やす(https://console.groq.com の Limits ページで確認できる)。
# 2026-07-07: 以前はこの秒数だけ「1バッチ処理するごとに」直列で待っていたが、
# 新規記事が多い時間帯(数千件規模)だと数百バッチ×待機秒数が積み重なり、
# パイプライン全体が60分(cronの実行間隔)を超えて「1時間に1回のはずが3時間に
# 1回程度しか更新されない」不具合の主因になっていた。GROQ_MAX_WORKERSによる
# 並列実行に切り替えたため、このディレイは現在使われていない
# (call_groq_batch側の429時リトライ待機だけで足りるため)。将来また直列方式に
# 戻す場合のために定数自体は残してある。
GROQ_REQUEST_DELAY_SEC = 1.2

# Groqバッチ呼び出しを同時に何件まで並列実行するか。無料枠のレート制限に
# 引っかかりにくいよう控えめな値にしてある。429が頻発するようならここを
# 減らす、逆にもっと速くしたい/余裕があるようなら増やす。
GROQ_MAX_WORKERS = 2

# 2026-07-07: 1回の実行でAI(Groq)判定にかける新規記事数の上限。
# 無料枠にはリクエスト毎分/トークン毎分/1日あたりの上限があり、5並列で数百バッチを
# 一気に投げたところ大量に429(レート制限)が発生し、実際のrun #31では271バッチ中
# 100件以上が処理されずに捨てられていた。並列数を2に絞り+429時にRetry-Afterを
# 尊重するリトライに変えたうえで、そもそも1回で投げる件数をこの値で頭打ちにする。
# ピンは48時間かけて蓄積される設計なので、1回の取り込みを絞っても地図全体の
# ピン数・地域バランスは回を重ねて埋まっていく(=毎時更新・地域網羅性は維持される)。
# 上限に達したときはシャッフルしてから切り詰めるため、国別トップニュースだけでなく
# アフリカ・中南米などローカルフィード由来の記事も均等に生き残る。
MAX_NEW_ARTICLES_PER_RUN = 300

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
# 2026-07-05: 西欧・北欧・中東・アフリカ・中南米の追加国を足して、地図全体の
# 偏り(特にヨーロッパ・北米方面が薄く見える問題)を緩和した。
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
    {"label": "イラン", "gl": "IR", "hl": "fa", "ceid": "IR:fa"},
    {"label": "イラク", "gl": "IQ", "hl": "ar", "ceid": "IQ:ar"},
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
    {"label": "エチオピア", "gl": "ET", "hl": "en-ET", "ceid": "ET:en"},
    {"label": "セネガル", "gl": "SN", "hl": "fr", "ceid": "SN:fr"},
    # --- 西欧 ---
    {"label": "イギリス", "gl": "GB", "hl": "en-GB", "ceid": "GB:en"},
    {"label": "フランス", "gl": "FR", "hl": "fr", "ceid": "FR:fr"},
    {"label": "ドイツ", "gl": "DE", "hl": "de", "ceid": "DE:de"},
    {"label": "イタリア", "gl": "IT", "hl": "it", "ceid": "IT:it"},
    {"label": "スペイン", "gl": "ES", "hl": "es", "ceid": "ES:es"},
    {"label": "オランダ", "gl": "NL", "hl": "nl", "ceid": "NL:nl"},
    {"label": "スウェーデン", "gl": "SE", "hl": "sv", "ceid": "SE:sv"},
    {"label": "スイス", "gl": "CH", "hl": "de", "ceid": "CH:de"},
    {"label": "ポルトガル", "gl": "PT", "hl": "pt-PT", "ceid": "PT:pt-150"},
    {"label": "ギリシャ", "gl": "GR", "hl": "el", "ceid": "GR:el"},
    {"label": "アイルランド", "gl": "IE", "hl": "en-IE", "ceid": "IE:en"},
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
    {"label": "キューバ", "gl": "CU", "hl": "es-419", "ceid": "CU:es-419"},
    # --- オセアニア ---
    {"label": "オーストラリア", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "ニュージーランド", "gl": "NZ", "hl": "en-NZ", "ceid": "NZ:en"},
]

# ---------------------------------------------------------------------------
# ②-b ローカルニュース取得 (Google News RSS「地名検索」フィード)
# ---------------------------------------------------------------------------
# NEWS_COUNTRIESの「国別トップニュース」は、その国の全国的なニュースしか
# ほとんど拾えない(例: 日本のトップニュースに札幌のローカルニュースはまず出てこない)。
# そこでGoogleニュースの検索フィード(rss/search?q=地名)を都市名で叩くことで、
# 全国紙だけでなく地方メディア(例: 札幌のSTVなど)の記事も横断的に拾えるようにする。
# 新しいAPIキーは不要(Google News RSSの仕組みをそのまま流用)。
#
# 都市はあえて首都・最大都市ではなく、二番手都市や地方都市を中心に選んである
# (首都圏の話題はNEWS_COUNTRIESの国別トップニュース側で既にカバーされているため、
# ここで同じ都市を選んでも新しい情報が増えにくい)。
# パイロットとしてまず各大陸から40都市前後を選定。様子を見て増減させる。
NEWS_SEARCH_RSS_URL_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"

# 検索フィード1件あたりの読み込み記事数上限(国別トップニュースより少なめでよい)。
NEWS_MAX_ITEMS_PER_LOCAL_CITY = 30

LOCAL_CITIES = [
    # --- 日本 ---
    {"label": "札幌", "query": "札幌", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "仙台", "query": "仙台", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "福岡", "query": "福岡", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "那覇", "query": "那覇 沖縄", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    # --- 東アジア ---
    {"label": "成都", "query": "成都", "gl": "CN", "hl": "zh-Hans", "ceid": "CN:zh-Hans"},
    {"label": "西安", "query": "西安", "gl": "CN", "hl": "zh-Hans", "ceid": "CN:zh-Hans"},
    {"label": "釜山", "query": "부산", "gl": "KR", "hl": "ko", "ceid": "KR:ko"},
    {"label": "高雄", "query": "高雄", "gl": "TW", "hl": "zh-Hant", "ceid": "TW:zh-Hant"},
    # --- 東南アジア ---
    {"label": "スラバヤ", "query": "Surabaya", "gl": "ID", "hl": "id", "ceid": "ID:id"},
    {"label": "チェンマイ", "query": "Chiang Mai", "gl": "TH", "hl": "th", "ceid": "TH:th"},
    {"label": "ダナン", "query": "Da Nang", "gl": "VN", "hl": "vi", "ceid": "VN:vi"},
    {"label": "セブ", "query": "Cebu", "gl": "PH", "hl": "en-PH", "ceid": "PH:en"},
    # --- 南アジア ---
    {"label": "ベンガルール", "query": "Bengaluru", "gl": "IN", "hl": "en-IN", "ceid": "IN:en"},
    {"label": "コルカタ", "query": "Kolkata", "gl": "IN", "hl": "en-IN", "ceid": "IN:en"},
    # --- 中東 ---
    {"label": "ジェッダ", "query": "Jeddah", "gl": "SA", "hl": "ar", "ceid": "SA:ar"},
    {"label": "イズミル", "query": "Izmir", "gl": "TR", "hl": "tr", "ceid": "TR:tr"},
    # --- アフリカ北部 ---
    {"label": "アレクサンドリア", "query": "Alexandria Egypt", "gl": "EG", "hl": "ar", "ceid": "EG:ar"},
    # --- アフリカ南部・東部 ---
    {"label": "ダーバン", "query": "Durban", "gl": "ZA", "hl": "en-ZA", "ceid": "ZA:en"},
    {"label": "モンバサ", "query": "Mombasa", "gl": "KE", "hl": "en-KE", "ceid": "KE:en"},
    {"label": "カノ", "query": "Kano Nigeria", "gl": "NG", "hl": "en-NG", "ceid": "NG:en"},
    # 2026-07-07: アフリカのピンが少ないとの指摘を受けて追加(ラゴス/アクラ/
    # ダルエスサラーム/カンパラ)。都市検索フィードは国別トップニュースより
    # 地元色の強い(≒マイナーでほのぼのした)ニュースも拾いやすい。
    {"label": "ラゴス", "query": "Lagos Nigeria", "gl": "NG", "hl": "en-NG", "ceid": "NG:en"},
    {"label": "アクラ", "query": "Accra", "gl": "GH", "hl": "en-GH", "ceid": "GH:en"},
    {"label": "ダルエスサラーム", "query": "Dar es Salaam", "gl": "TZ", "hl": "en-TZ", "ceid": "TZ:en"},
    {"label": "カンパラ", "query": "Kampala", "gl": "UG", "hl": "en-UG", "ceid": "UG:en"},
    # --- アフリカ北部(追加) ---
    {"label": "カサブランカ", "query": "Casablanca", "gl": "MA", "hl": "fr", "ceid": "MA:fr"},
    # --- 西欧 ---
    {"label": "マンチェスター", "query": "Manchester", "gl": "GB", "hl": "en-GB", "ceid": "GB:en"},
    {"label": "グラスゴー", "query": "Glasgow", "gl": "GB", "hl": "en-GB", "ceid": "GB:en"},
    {"label": "マルセイユ", "query": "Marseille", "gl": "FR", "hl": "fr", "ceid": "FR:fr"},
    {"label": "ミュンヘン", "query": "München", "gl": "DE", "hl": "de", "ceid": "DE:de"},
    {"label": "ミラノ", "query": "Milano", "gl": "IT", "hl": "it", "ceid": "IT:it"},
    {"label": "バルセロナ", "query": "Barcelona", "gl": "ES", "hl": "es", "ceid": "ES:es"},
    # --- 東欧・ロシア ---
    {"label": "ノヴォシビルスク", "query": "Новосибирск", "gl": "RU", "hl": "ru", "ceid": "RU:ru"},
    # --- 北米 ---
    {"label": "シカゴ", "query": "Chicago", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "ヒューストン", "query": "Houston", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "シアトル", "query": "Seattle", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "マイアミ", "query": "Miami", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "バンクーバー", "query": "Vancouver", "gl": "CA", "hl": "en-CA", "ceid": "CA:en"},
    {"label": "モントリオール", "query": "Montreal", "gl": "CA", "hl": "en-CA", "ceid": "CA:en"},
    {"label": "グアダラハラ", "query": "Guadalajara", "gl": "MX", "hl": "es-419", "ceid": "MX:es-419"},
    # --- 中南米 ---
    {"label": "レシフェ", "query": "Recife", "gl": "BR", "hl": "pt-BR", "ceid": "BR:pt-419"},
    {"label": "ポルトアレグレ", "query": "Porto Alegre", "gl": "BR", "hl": "pt-BR", "ceid": "BR:pt-419"},
    {"label": "コルドバ", "query": "Córdoba Argentina", "gl": "AR", "hl": "es-419", "ceid": "AR:es-419"},
    # 2026-07-07: 南アメリカのピンが少ないとの指摘を受けて追加(サルバドール/
    # メデジン/グアヤキル/バルパライソ/グアテマラシティ)。グアテマラは
    # NEWS_COUNTRIESに国別トップニュースが無いので、この都市検索フィードが
    # 唯一の取得元になる。
    {"label": "サルバドール", "query": "Salvador Bahia", "gl": "BR", "hl": "pt-BR", "ceid": "BR:pt-419"},
    {"label": "メデジン", "query": "Medellín", "gl": "CO", "hl": "es-419", "ceid": "CO:es-419"},
    {"label": "グアヤキル", "query": "Guayaquil", "gl": "EC", "hl": "es-419", "ceid": "EC:es-419"},
    {"label": "バルパライソ", "query": "Valparaíso", "gl": "CL", "hl": "es-419", "ceid": "CL:es-419"},
    {"label": "グアテマラシティ", "query": "Ciudad de Guatemala", "gl": "GT", "hl": "es-419", "ceid": "GT:es-419"},
    # --- オセアニア ---
    {"label": "メルボルン", "query": "Melbourne", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "ブリスベン", "query": "Brisbane", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "クライストチャーチ", "query": "Christchurch", "gl": "NZ", "hl": "en-NZ", "ceid": "NZ:en"},
    # ===== 2026-07-07 追加分: 「知らない街のローカルな話題」を厚くするための二番手・地方都市 =====
    # --- 日本 ---
    {"label": "金沢", "query": "金沢", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "松山", "query": "松山 愛媛", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "鹿児島", "query": "鹿児島", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    {"label": "新潟", "query": "新潟", "gl": "JP", "hl": "ja", "ceid": "JP:ja"},
    # --- 東アジア ---
    {"label": "青島", "query": "青岛", "gl": "CN", "hl": "zh-Hans", "ceid": "CN:zh-Hans"},
    {"label": "大連", "query": "大连", "gl": "CN", "hl": "zh-Hans", "ceid": "CN:zh-Hans"},
    {"label": "台中", "query": "台中", "gl": "TW", "hl": "zh-Hant", "ceid": "TW:zh-Hant"},
    {"label": "大邱", "query": "대구", "gl": "KR", "hl": "ko", "ceid": "KR:ko"},
    # --- 東南アジア ---
    {"label": "バンドン", "query": "Bandung", "gl": "ID", "hl": "id", "ceid": "ID:id"},
    {"label": "ジョグジャカルタ", "query": "Yogyakarta", "gl": "ID", "hl": "id", "ceid": "ID:id"},
    {"label": "プーケット", "query": "Phuket", "gl": "TH", "hl": "th", "ceid": "TH:th"},
    {"label": "ダバオ", "query": "Davao", "gl": "PH", "hl": "en-PH", "ceid": "PH:en"},
    {"label": "ペナン", "query": "Penang", "gl": "MY", "hl": "en-MY", "ceid": "MY:en"},
    # --- 南アジア ---
    {"label": "チェンナイ", "query": "Chennai", "gl": "IN", "hl": "en-IN", "ceid": "IN:en"},
    {"label": "ラホール", "query": "Lahore", "gl": "PK", "hl": "en-PK", "ceid": "PK:en"},
    {"label": "キャンディ", "query": "Kandy Sri Lanka", "gl": "LK", "hl": "en-LK", "ceid": "LK:en"},
    # --- 中東 ---
    {"label": "アンタルヤ", "query": "Antalya", "gl": "TR", "hl": "tr", "ceid": "TR:tr"},
    {"label": "ハイファ", "query": "Haifa", "gl": "IL", "hl": "en-IL", "ceid": "IL:en"},
    {"label": "シャルジャ", "query": "Sharjah", "gl": "AE", "hl": "en-AE", "ceid": "AE:en"},
    # --- アフリカ ---
    {"label": "プレトリア", "query": "Pretoria", "gl": "ZA", "hl": "en-ZA", "ceid": "ZA:en"},
    {"label": "クマシ", "query": "Kumasi", "gl": "GH", "hl": "en-GH", "ceid": "GH:en"},
    {"label": "アルーシャ", "query": "Arusha", "gl": "TZ", "hl": "en-TZ", "ceid": "TZ:en"},
    {"label": "ダカール", "query": "Dakar", "gl": "SN", "hl": "fr", "ceid": "SN:fr"},
    {"label": "アビジャン", "query": "Abidjan", "gl": "CI", "hl": "fr", "ceid": "CI:fr"},
    # --- 西欧 ---
    {"label": "リヨン", "query": "Lyon", "gl": "FR", "hl": "fr", "ceid": "FR:fr"},
    {"label": "ナポリ", "query": "Napoli", "gl": "IT", "hl": "it", "ceid": "IT:it"},
    {"label": "セビリア", "query": "Sevilla", "gl": "ES", "hl": "es", "ceid": "ES:es"},
    {"label": "ハンブルク", "query": "Hamburg", "gl": "DE", "hl": "de", "ceid": "DE:de"},
    {"label": "リヴァプール", "query": "Liverpool", "gl": "GB", "hl": "en-GB", "ceid": "GB:en"},
    {"label": "ポルト", "query": "Porto", "gl": "PT", "hl": "pt-PT", "ceid": "PT:pt-150"},
    # --- 東欧・ロシア ---
    {"label": "カザン", "query": "Казань", "gl": "RU", "hl": "ru", "ceid": "RU:ru"},
    {"label": "クラクフ", "query": "Kraków", "gl": "PL", "hl": "pl", "ceid": "PL:pl"},
    {"label": "リヴィウ", "query": "Львів", "gl": "UA", "hl": "uk", "ceid": "UA:uk"},
    # --- 北米 ---
    {"label": "フィラデルフィア", "query": "Philadelphia", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "オースティン", "query": "Austin Texas", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "デンバー", "query": "Denver", "gl": "US", "hl": "en-US", "ceid": "US:en"},
    {"label": "カルガリー", "query": "Calgary", "gl": "CA", "hl": "en-CA", "ceid": "CA:en"},
    {"label": "モンテレイ", "query": "Monterrey", "gl": "MX", "hl": "es-419", "ceid": "MX:es-419"},
    # --- 中南米 ---
    {"label": "クリチバ", "query": "Curitiba", "gl": "BR", "hl": "pt-BR", "ceid": "BR:pt-419"},
    {"label": "ロサリオ", "query": "Rosario Argentina", "gl": "AR", "hl": "es-419", "ceid": "AR:es-419"},
    {"label": "カリ", "query": "Cali Colombia", "gl": "CO", "hl": "es-419", "ceid": "CO:es-419"},
    {"label": "アレキパ", "query": "Arequipa", "gl": "PE", "hl": "es-419", "ceid": "PE:es-419"},
    {"label": "プエブラ", "query": "Puebla", "gl": "MX", "hl": "es-419", "ceid": "MX:es-419"},
    # --- オセアニア ---
    {"label": "パース", "query": "Perth Australia", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "アデレード", "query": "Adelaide", "gl": "AU", "hl": "en-AU", "ceid": "AU:en"},
    {"label": "オークランド", "query": "Auckland", "gl": "NZ", "hl": "en-NZ", "ceid": "NZ:en"},
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
# また、この数字は「1回の実行(1時間ごと)で新しく採用したい件数」の目安であって、
# 地図の合計ピン数の目安ではない(合計は蓄積されるためMAX_PINS_TOTALに近づいていく)。
# 2026-07-07: 地図を見たユーザーから「アフリカ・南米(中南米)が少ない」との指摘を
# 受けて再配分した。北米・西欧・その他一部地域のノルマを減らし、その分を
# アフリカ北部・アフリカ南部東部・中南米に上乗せしている(合計は引き続き1000)。
# 2026-07-07: 各地域のノルマ枠のうち、どれくらいを「ローカルフィード(LOCAL_CITIES=
# 都市名検索。地方紙・地元の話題・お店の開店など、いわゆるトップニュース以外)」由来の
# 記事に優先的に割り当てるかの比率(0〜1)。残りは国別トップニュース(NEWS_COUNTRIES=
# トランプの言動・ホルムズ海峡・経済危機のような硬派な大ニュース)に確保される。
# どちらかの候補が足りない地域では、余った枠は自動的にもう一方で埋める(枠は無駄にしない)。
# 0.6 = ローカル6割・トップ4割を目安に選抜。もっとローカル寄りにしたければ0.7〜0.8へ、
# トップを厚くしたければ下げる。
LOCAL_SELECT_RATIO = 0.6

REGION_QUOTAS = {
    "日本": 90,
    "東アジア": 80,
    "東南アジア": 65,
    "南アジア": 65,
    "中東": 80,
    "アフリカ北部": 70,
    "アフリカ南部・東部": 150,
    "西欧": 90,
    "東欧・ロシア": 60,
    "北米": 90,
    "中南米": 110,
    "オセアニア": 50,
}

# ---------------------------------------------------------------------------
# ④ ピンの総数・ジャンル
# ---------------------------------------------------------------------------
MAX_PINS_TOTAL = 1000

# 記事をどれくらいの期間、地図に残し続けるか(時間)。記事のpublishedAt(RSSに無ければ
# 初めて取得した時刻)からこの時間を過ぎた記事は、次回更新時に地図から自動で消える。
# 1回の更新(1時間ごと)で取得できる新規記事は数百件程度のことが多いが、この蓄積の
# 仕組みのおかげで、地図全体としては常にMAX_PINS_TOTALに近いピンが世界中に
# 散らばっている状態を保てる(「毎回1000件フェッチし直す」必要はない)。
ITEM_MAX_AGE_HOURS = 48

# AIに判定させるジャンルの許容リスト。ここに無い値が返ってきたら"その他"に矯正する。
VALID_GENRES = ["テック", "経済", "エンタメ", "政治", "環境", "スポーツ", "科学", "事件・事故", "その他"]

# ジャンルごとのピンの色(フロントのTailwindカラーに合わせてある)。
GENRE_COLORS = {
    "テック": "#3b82f6",      # blue-500
    "経済": "#10b981",        # emerald-500
    "エンタメ": "#ec4899",    # pink-500
    "政治": "#ef4444",        # red-500
    "環境": "#14b8a6",        # teal-500(「経済」のemerald-500と紛らわしかったため変更)
    "スポーツ": "#f59e0b",    # amber-500
    "科学": "#8b5cf6",        # violet-500
    "事件・事故": "#64748b",  # slate-500
    "その他": "#9ca3af",      # gray-400
}

# 似たタイトルの記事(同じ出来事を別ソースが報じたもの)を重複ピンとして弾くための
# 類似度しきい値(0〜1、difflib.SequenceMatcher基準)。1に近いほど判定が厳しくなる。
DUPLICATE_TITLE_SIMILARITY_THRESHOLD = 0.85

# 同じ地名(=同じ座標)に複数のピンが重なる場合、地図上で視覚的に散らして見せる
# ためのずらし幅(緯度経度の度数、およそ0.03度=3km程度)。都市代表点の
# ジオコーディング自体が元々数百m〜数km程度の誤差を持つ大雑把なものなので、
# この処理は位置精度を実質的に悪化させるものではない
# (「東京」のピンが常に全く同じ1点に積み重なって見えるのを防ぐ目的)。
DUPLICATE_COORD_JITTER_DEGREES = 0.03

# 見出しにこれらのキーワード(部分一致・大文字小文字/全角半角区別なし)が含まれる
# 記事は、閲覧体験に配慮してピン化しない(完全なフィルタではなく簡易的な
# ブロックリスト方式)。過激・グロテスクな見出しをそのまま地図に載せないための
# 簡易ガード。必要に応じて増減させてよい。
SENSITIVE_KEYWORDS = [
    "死体", "遺体画像", "遺体の写真", "惨殺", "斬首", "轢死", "焼死体",
    "自殺の方法", "首吊り", "凄惨な現場",
    "beheading video", "graphic footage", "murder scene photo",
]

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

# 検索結果の表記を英語に揃えるための言語指定(同名地名の表記ゆれを減らす目的)。
NOMINATIM_ACCEPT_LANGUAGE = "en"

GEOCODE_CACHE_FILE = "geocode_cache.json"

# ---------------------------------------------------------------------------
# ⑥ 出力・その他
# ---------------------------------------------------------------------------
OUTPUT_FILE = "news_data.json"

# 記事のサムネイル画像について。
# Google News RSS自体には記事画像が含まれないため、まず記事の実際のページ(中継URLの
# リンク先)から og:image(SNSシェア用に各サイトが用意している画像)を軽量に取得しに行く。
# 取得できた場合はその実画像を使い、取得できなかった場合は「画像なし」として表示する
# (2026-07-05: 以前はここでランダムなプレースホルダー画像を出していたが、記事内容と
# 無関係な写真が出るのは誤解を招く、というユーザー指摘を受けて変更した)。
# なお、スポンサーピン(SPONSOR_PINS)はこことは別に、画像未指定時の穴埋め用として
# 引き続きこのプレースホルダーを使う。
PLACEHOLDER_IMAGE_BASE = "https://picsum.photos/seed"

# 記事本文ページからog:image画像を取りに行く際のタイムアウト秒数。
# 短めにして、遅い/応答しないサイトでパイプライン全体が長引きすぎないようにする。
REAL_IMAGE_FETCH_TIMEOUT_SEC = 4

# ページの先頭何バイトまで読んでog:imageを探すか。記事全文をダウンロードする必要は
# なく、通常<head>タグ内(ページ先頭付近)にog:imageがあるため、これで十分間に合う。
REAL_IMAGE_FETCH_MAX_BYTES = 65536

# og:image取得を何件同時並列で行うか。Nominatimジオコーディングと違い、取得先は
# 記事ごとに別々のニュースサイトなので並列化してもポリシー上の問題になりにくい。
# 新規記事が多い回でも、逐次実行で合計時間が伸びすぎてパイプライン全体が
# 60分(cronの実行間隔)を超えないようにするための対策。
REAL_IMAGE_FETCH_MAX_WORKERS = 10

# 2026-07-07: og:image取得が本番で0%成功だった原因は、Google News RSSのlinkが
# 実記事ではなく「JavaScriptで最終URLへ飛ぶ中継ページ」で、requestsのHTTPリダイレクト
# 追跡だけでは実記事に到達できず、中継ページ自体にog:imageが無いためだった
# (run #31のログでも失敗17件が全て no_og_image_tag)。そこで画像取得の前に
# Googleの内部エンドポイントで中継URLを実記事URLへ解決する処理を挟む。
# Trueで有効。Google側の仕様変更で解決が壊れた場合はFalseにすれば従来動作
# (画像なし)に戻せる。
RESOLVE_GOOGLE_NEWS_URLS = True

# 中継URLの解決(2リクエスト発生する)1回あたりのタイムアウト秒数。
GOOGLE_NEWS_RESOLVE_TIMEOUT_SEC = 8

# 自動更新の実行間隔(分)。.github/workflows/update.yml のcron設定(現在は毎時0分=60分間隔)
# と必ず合わせること。フロント側で「次回更新予定」を計算するために news_data.json に
# 埋め込む next_update_at の算出に使う。
UPDATE_INTERVAL_MINUTES = 60

# ---------------------------------------------------------------------------
# ⑦ スポンサーピン(将来のマネタイズ用の枠組み)
# ---------------------------------------------------------------------------
# 企業・個人からお金をもらって地図上に「広告ピン」を常時表示するための枠組み。
# ここにエントリを追加すると、次回のpipeline.py実行から自動的に地図に反映される
# (ニュースのように48時間で消えたりせず、ここに書いてある限り常に表示され続ける)。
# 表示上は必ず「PR」バッジが付き、広告であることが一目でわかるようにしてある
# (2023年施行のステマ規制=景品表示法対応。広告と明示しない表示は不可)。
# デフォルトは空(実際のスポンサーが決まったらここに追記する)。
#
# 書き方の例:
# SPONSOR_PINS = [
#     {
#         "name": "サンプル株式会社",              # ピンのタイトルに使われる
#         "place": "Tokyo, Japan",                 # 表示用の地名
#         "lat": 35.6812,                          # 緯度
#         "lng": 139.7671,                         # 経度
#         "message": "新商品発売中！詳しくはこちら。",  # AI要約欄に表示される文言
#         "url": "https://example.com",            # クリック時の遷移先
#         "imageUrl": "https://example.com/banner.jpg",  # 任意。省略可
#         "color": "#eab308",                      # 任意。省略時は金色になる
#     },
# ]
SPONSOR_PINS = []

# 注意: サイトタイトル/サブタイトル(「せかにゅ」「ニュースと地理」)や、
# ダークモード・検索・投げ銭リンクなどのフロント側設定は index.html 側に直接
# 書いてある(フロントは静的1ファイルでテンプレート化していないため)。
# 変更する場合は index.html の該当箇所(#site-title, SUPPORT_URL 等)を編集すること。
