# ============================================================
#  リポジトリ作成から公開・毎日自動更新まで、これ1本で終わらせるスクリプト
#
#    .\setup.ps1                       # 既定のリポジトリ名で公開
#    .\setup.ps1 -RepoName my-site     # 名前を指定
#    .\setup.ps1 -SkipCrawl            # 初回巡回を省略（テンプレート修正後の再実行用）
#
#  事前に `gh auth login` だけ済ませておくこと。
#
#  【このスクリプトは公開操作をします】
#    public なリポジトリを作り、GitHub Pages でサイトを世界に公開します。
#    実行前に config/offers.yaml の中身を確認してください。
# ============================================================

param(
    # 公開リポジトリ名。そのまま公開URLになる。
    # 追跡対象の商標をURLに含めないこと。独立したサイトだと分かる名前にする。
    [string]$RepoName = "kiji",
    [switch]$SkipCrawl
)

$ErrorActionPreference = "Stop"
# PowerShell 7.4+ は既定で「外部コマンドの非0終了」も例外にする。
# このスクリプトは git/gh の終了コードを自前で判定しているので無効化しておく
$PSNativeCommandUseErrorActionPreference = $false
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

function Step($n, $msg) { Write-Host "`n[$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg)     { Write-Host "    !   $msg" -ForegroundColor Yellow }
function Fail($msg)     { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

# ---- 1. 前提の確認 ------------------------------------------
Step 1 "前提を確認"

gh auth status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "GitHub CLI が未認証です。先に 'gh auth login' を実行してください。" }

$owner = (gh api user --jq .login)
$repo  = $RepoName
$siteUrl = "https://$owner.github.io/$repo/"
Ok "公開URL: $siteUrl"

# ---- 2. 公開URLを設定に書き込む -----------------------------
Step 2 "config/site.yaml の base_url を更新"

# base_url はサイト内の全リンク・sitemap・canonical の基準になる。
# ここがずれると全リンクが壊れるので、実際の公開URLで必ず上書きする。
$sitePath = "config/site.yaml"
$content = Get-Content $sitePath -Raw -Encoding UTF8
$updated = $content -replace '(?m)^(\s*base_url:\s*).*$', "`${1}$siteUrl"
if ($updated -ne $content) {
    Set-Content $sitePath -Value $updated -Encoding UTF8 -NoNewline
    Ok "base_url = $siteUrl"
} else {
    Ok "base_url は既に正しい値です"
}

# ---- 3. 設定の検証 ------------------------------------------
Step 3 "設定を検証"

python -c @'
import sys, pathlib
sys.path.insert(0, ".")
from src.catalog import load_catalog, load_site, ConfigError
try:
    site = load_site(pathlib.Path("config/site.yaml"))
    cat = load_catalog(pathlib.Path("config/offers.yaml"))
except ConfigError as e:
    print(f"設定エラー: {e}"); sys.exit(1)
verified = sum(1 for o in cat.offers if o.verified)
paid = sum(1 for o in cat.offers if o.is_monetized)
print(f"案件 {len(cat.offers)}件 / 比較 {len(cat.comparisons)}組 / "
      f"実際に試した {verified}件 / 収益リンク設定済み {paid}件")
if paid == 0:
    print("注意: 収益リンクが1件も設定されていません。公開しても収入は発生しません。")
if verified == 0:
    print("注意: 実際に試した案件が0件です。全ページに「未検証」と表示されます。")
'@
if ($LASTEXITCODE -ne 0) { Fail "config/offers.yaml に問題があります。上のメッセージを確認してください。" }
Ok "設定は妥当です"

# ---- 4. 回帰テスト ------------------------------------------
Step 4 "回帰テスト"

python -m pytest tests/ -q
if ($LASTEXITCODE -ne 0) { Fail "テストが落ちています。直してから再実行してください。" }
Ok "テスト通過"

# ---- 5. 生成と検証 ------------------------------------------
Step 5 "サイトを生成して検証"

if ($SkipCrawl) {
    Warn "巡回をスキップしました"
    python main.py --render
} else {
    Write-Host "    公式ページを2秒間隔で読むので少し時間がかかります..." -ForegroundColor DarkGray
    python main.py
}
if ($LASTEXITCODE -ne 0) { Fail "生成に失敗しました。上のログを確認してください。" }

python main.py --verify
if ($LASTEXITCODE -ne 0) { Fail "生成物の検証に失敗しました。上の ERROR を解消してから再実行してください。" }
Ok "生成と検証を通過"

# ---- 6. push ------------------------------------------------
Step 6 "GitHubへpush"

if (-not (Test-Path ".git")) { git init -q; git branch -M main }
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { git commit -q -m "記事型アフィリエイトサイト" }

git remote get-url origin 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    # Pages を無料で使うには public である必要がある
    gh repo create $repo --public --source=. --remote=origin --push
} else {
    git push -u origin main
}
if ($LASTEXITCODE -ne 0) { Fail "push に失敗しました。" }
Ok "$owner/$repo"

# ---- 7. ワークフローに書き込み権限を与える -------------------
Step 7 "Actions の権限を設定"

# ここが最重要。既定が read-only のままだと、ワークフロー側で contents: write と
# 書いても昇格できず、毎日の料金履歴がリポジトリに残らない。
# 履歴が残らない = このサイトの唯一の資産が永久に溜まらない、ということ。
gh api -X PUT "repos/$owner/$repo/actions/permissions/workflow" `
    -f default_workflow_permissions=write `
    -F can_approve_pull_request_reviews=false 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "権限の自動設定に失敗しました。手動で設定してください:"
    Warn "  Settings → Actions → General → Workflow permissions → Read and write"
} else {
    Ok "ワークフローが料金履歴を書き戻せるようになりました"
}

# ---- 8. Pages を有効化 --------------------------------------
Step 8 "GitHub Pages を有効化"

# 未設定なら POST、設定済みなら PUT。どちらか片方しか成功しないので順に試す
gh api -X POST "repos/$owner/$repo/pages" -f build_type=workflow 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    gh api -X PUT "repos/$owner/$repo/pages" -f build_type=workflow 2>$null | Out-Null
}
Ok "ソース: GitHub Actions"

# ---- 9. 初回デプロイ ----------------------------------------
Step 9 "初回デプロイを実行"

gh workflow run daily.yml --repo "$owner/$repo"
Ok "ワークフローを起動しました"

Write-Host "`n========================================" -ForegroundColor Yellow
Write-Host " セットアップ完了" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host " 公開URL : $siteUrl"
Write-Host " 進捗確認: gh run watch --repo $owner/$repo"
Write-Host " 以降は毎日 JST 07:00 に自動で料金を確認・更新します。"
Write-Host ""
Write-Host " 次にやること（この順でしか進めません）:" -ForegroundColor Yellow
Write-Host "   1. A8 の 登録情報 → サイト情報の登録・修正 で、このサイトを副サイトとして登録"
Write-Host "      → A8 の提携申請はサイト単位。新しいサイトには既存の提携が引き継がれません"
Write-Host "   2. 副サイトを選んで提携申請し、承認されたリンクを config/offers.yaml に貼る"
Write-Host "   3. Google Search Console に $siteUrl を登録して sitemap.xml を送信"
Write-Host "   4. 実際に使って config/offers.yaml の verdict を埋める"
Write-Host "      → 空のままだと全ページに「未検証」と出て順位も下がります"
Write-Host ""
