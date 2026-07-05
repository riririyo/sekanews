# せかにゅ - ニュースと地理

ニュースを取得 → AIで要約・ジャンル・地名を判定 → 緯度経度に変換 → フロント用JSONを生成します。
`index.html` はこのJSONを読むだけでピンが立ちます。1時間ごとに自動更新、最大1000件のピンに対応。

## 何が入っているか

| ファイル | 役割 |
|---|---|
| `pipeline.py` | 本体。①取得→②AI→③重複除去/地域選抜→④座標変換→⑤JSON出力を一気に実行 |
| `config.py` | 設定。基本ここの数字をいじるだけ。地域ノルマ・色・更新頻度・ピン上限など |
| `index.html` | フロント(地図)。`news_data.json` を fetch してピンを立てる。1000件でも重くならないようクラスタリング対応 |
| `news_data.json` | 出力。フロントが読むファイル(実行すると生成される) |
| `geocode_cache.json` | 地名→座標のキャッシュ(自動生成。消しても再生成される) |
| `requirements.txt` | Python依存(requestsのみ) |
| `.github/workflows/update.yml` | GitHub Actionsで1時間ごとに自動実行し、gh-pagesに公開するワークフロー |

## 準備(最初の1回だけ)

1. **Groqの無料APIキーを取る**
   https://console.groq.com にGoogleアカウントで登録 → API Keys でキー発行(1日14,000回無料)。

2. **キーを環境変数に入れる(ローカル実行の場合)**
   ```
   export GROQ_API_KEY=gsk_あなたのキー
   ```
   (Windowsは `set GROQ_API_KEY=gsk_...`)

3. **config.py の User-Agent を自分用に書き換える**
   `NOMINATIM_USER_AGENT` にアプリ名とメールアドレスを入れる(Nominatimの利用規約で必須。
   現状 `rimocon.rimocon.rimocon@gmail.com` になっているので、公開する際は自分のメールに変更すること)。

## ローカルで動かす

```
pip install -r requirements.txt
python3 pipeline.py
```

これで `news_data.json` が出来ます。地図を確認するには、`index.html` を直接ダブルクリックせず
(file:// だと fetch がブロックされる)、簡易サーバー経由で開く。

```
python3 -m http.server 8000
```

ブラウザで `http://localhost:8000/index.html` を開く。

## ネットに公開する(無料)

**GitHub Actions → gh-pagesブランチ → GitHub Pages** の構成。ポイントは、自動更新の
コミットは `gh-pages` ブランチだけに(1回ごとに1コミットで)入り、`main` ブランチのソース
コードの履歴は一切汚れないこと。

1. このフォルダの中身(`pipeline.py`, `config.py`, `index.html`, `requirements.txt`,
   `.github/workflows/update.yml`, `.gitignore`)をそのままGitHubリポジトリのルートに置いてpush。
   (`news_data.json` はサンプルのままで良い。初回のActions実行で本物に置き換わる)
2. リポジトリの **Settings → Secrets and variables → Actions** で
   `GROQ_API_KEY` という名前のRepository secretを追加(自分のGroqキーを入れる)。
3. **Settings → Actions → General → Workflow permissions** を
   「Read and write permissions」にする(gh-pagesブランチへのpushに必要)。
4. リポジトリの **Actions** タブ → `Update News Data` → `Run workflow` で1回手動実行する。
   成功すると `gh-pages` ブランチが自動生成される。
5. **Settings → Pages** で Source を「Deploy from a branch」、Branch を `gh-pages` / `(root)`
   に設定して保存。
   → `https://ユーザー名.github.io/リポジトリ名/` で公開される。

これで1時間ごとに GitHub Actions が `pipeline.py` を実行し、`gh-pages` ブランチを
1コミットで置き換え → GitHub Pages が最新版を配信、という流れがサーバー代0円で回る。
public リポジトリなら GitHub Actions の実行時間は無制限無料(private の場合は月2,000分まで無料)。

(Cloudflare Pagesを使いたい場合は、`peaceiris/actions-gh-pages` のステップを
Cloudflare Pagesのデプロイアクションに差し替えればOK)

## よくいじる設定(config.py)

- **地域バランス**: `REGION_QUOTAS` の数字。「アフリカ南部・東部」を増やせばアフリカが濃くなる。
  対応する国コードは `REGION_QUERIES` 側(GDELTのsourcecountryフィルタ、FIPS 10-4形式)。
  ノルマの合計が実質的なピン数の目安(既定は合計1000)。ニュースが少ない地域は未達になる
  ことがあるが、その分は他地域の記事で自動的に埋め合わされる。
- **ピンの総数**: `MAX_PINS_TOTAL`(既定1000)。
- **更新の対象時間**: `GDELT_TIMESPAN`(更新間隔に合わせる。1時間ごとなら "75min" 前後が安全。
  cronの遅延を見込んで少し広めにしてある)。
- **AIモデル差し替え**: `AI_MODEL`。将来Groqを離れGeminiにする時はここと `pipeline.py` の
  `GROQ_ENDPOINT` / `call_groq_batch()` を変更する。
- **ジャンルと色**: `VALID_GENRES` / `GENRE_COLORS`。ここに無いジャンルは自動的に「その他」に矯正される。
- **重複記事の判定の厳しさ**: `DUPLICATE_TITLE_SIMILARITY_THRESHOLD`(0〜1、大きいほど厳しい)。

## 設計上まもっていること

- **地名が取れない記事はピンにしない**(AIが `place=""` を返す、もしくは座標が取れなければ除外)。
- **AIは見出ししか読んでいない、と明示する**: 本文を取得していないため、AIには「見出しから
  読み取れる範囲だけを日本語で言い換える」よう指示し、具体的な数字や引用を捏造させない。
  フロント側にも `aiSummaryIsHeadlineOnly` フラグで「見出しのみからの推測要約」と明示している。
- **記事の実画像を優先**: GDELTの`socialimage`が取得できた記事はそれを使い、無ければ
  プレースホルダー画像(picsum.photos)にフォールバックする。
- **似たタイトルの重複記事を除去**: 同じ出来事を複数ソースが報じた場合、タイトルの類似度で
  重複ピンを弾く(`difflib`ベース、完全ではないが実用上十分)。
- **地名の表記揺れに強いキャッシュ**: ジオコーディング前に地名を正規化(NFKC正規化+大文字小文字統一)
  してからキャッシュキーにすることでヒット率を上げている。失敗した地名はキャッシュに残さず、
  次回また試すようにしている。
- **Nominatimは1秒1回を厳守**(`NOMINATIM_DELAY_SEC=1.1`)+キャッシュで二度引きしない。
- **AIの返りを検証**(不正なジャンル/地域は「その他」に矯正、または集計対象から除外)。
- **AIコストはユーザー数に無関係**(更新1回につき1バッチ処理のみ。全ユーザーは同じJSONを見る)。
- **XSS対策**: ニュースのタイトル・要約・地名はすべてエスケープしてから画面に挿入。
  URLや画像URLも `http`/`https` 以外のスキームを弾いてから使用する。

## 既知の制約・トレードオフ(あえてやっていないこと)

- **Tailwind CSSはCDN版(JIT)のまま**。本番のベストプラクティスはビルド済みCSSだが、
  ビルドステップを増やすと「無料でパパっと動かす」という趣旨から外れるため見送っている。
  気になる場合はTailwind CLIでのビルドステップをActionsに追加するとよい。
- **本文は取得していない**。記事本文まで読みに行けばAI要約の精度は上がるが、各サイトの
  利用規約・スクレイピング可否の確認や取得時間の増大が発生するため、見出しのみ+正直な
  「見出しのみ要約です」という開示で対応している。
- **重複除去は完全ではない**。タイトルの文字列類似度による簡易判定なので、表現が大きく
  違う同一事件の記事は重複として残ることがある。

## トラブルシューティング

- `[ERROR] GROQ_API_KEY が設定されていません` → 環境変数 or GitHub Secretsの設定を確認。
- `news_data.json` が0件/少なすぎる → `GDELT_TIMESPAN` を広げる(例: "75min" → "180min")か、
  `REGION_QUOTAS` / `REGION_QUERIES` を見直す。ニュース自体が少ない時間帯もある。
- 地図にサンプルデータしか出ない → `index.html` を file:// で開いていないか確認。ローカルサーバー
  経由 or デプロイ後のURLで開くこと。
- 画面左下に「更新が長時間止まっている」と出る → GitHub Actionsの実行履歴でエラーを確認
  (GroqやGDELTの障害、レート制限超過などが典型的な原因)。
