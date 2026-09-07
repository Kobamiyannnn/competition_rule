#!/usr/bin/env bash
# clone 直後に一度だけ実行する。テンプレートの git をコンペ用に付け替える。
#
#   bash tools/bootstrap.sh            origin を template に改名し、履歴は残す
#   bash tools/bootstrap.sh --fresh    履歴を捨てて git init からやり直す
#
# GitHub を使うなら、テンプレートを Settings → Template repository にして
# 「Use this template」で始める方が簡単。このスクリプトは clone した場合の受け皿。

set -euo pipefail

FRESH=0
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "知らない引数: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
root="$PWD"

if [ ! -d .git ]; then
  echo "git リポジトリではない。clone したディレクトリで実行する。" >&2
  exit 2
fi

normalize() {
  printf '%s' "$1" \
    | sed -E 's#^(https?://|ssh://)?(git@)?[^/:]+[:/]##; s#\.git$##; s#/$##' \
    | tr '[:upper:]' '[:lower:]'
}

template_url="$(tr -d '[:space:]' < .template-source 2>/dev/null || true)"
origin_url="$(git remote get-url origin 2>/dev/null || true)"

echo "== テンプレート: ${template_url:-（不明）}"
echo "== いまの origin: ${origin_url:-（未設定）}"
echo

if [ "$FRESH" = "1" ]; then
  echo "履歴を捨てて作り直す。"
  rm -rf .git
  git init -q
  git add -A
  git commit -q -m "コンペ用リポジトリを作成（competition_rule テンプレートより）"
  echo "  新しい履歴でコミットした。origin は未設定。"
  echo "  自分のリポジトリを作って: git remote add origin <URL>"
else
  if [ -n "$origin_url" ] && [ -n "$template_url" ] \
     && [ "$(normalize "$origin_url")" = "$(normalize "$template_url")" ]; then
    if git remote get-url template >/dev/null 2>&1; then
      git remote remove template
    fi
    git remote rename origin template
    echo "origin を template に改名した。"
    echo "  以降 origin は未設定なので、コンペ用のリポジトリを作って:"
    echo "    git remote add origin <URL>   （private を推奨）"
    echo "  テンプレートの更新を取り込むときは:"
    echo "    git pull template main"
  else
    echo "origin はテンプレートを指していない。付け替えはしない。"
  fi
fi

# テンプレートへの誤プッシュを止める hook を有効にする。
# .git/hooks は clone されないので、追跡下の tools/githooks を使う。
git config core.hooksPath tools/githooks
echo
echo "pre-push hook を有効にした（core.hooksPath=tools/githooks）。"
echo "  テンプレート宛のプッシュは止まる。"

# コンペのデータ置き場。.gitignore でコミット対象から外してある。
mkdir -p data
if [ ! -f data/README.md ]; then
  cat > data/README.md <<'MD'
# データ置き場

ここに置いたものは `.gitignore` でコミット対象から外れている。

コンペのデータはほぼ確実に再配布禁止なので、リポジトリに入れない。
どこから取ってきたか、どう展開したかは `knowledge/operations.md` に書く。
MD
fi

echo
echo "次にやること: Claude Code を起動して /init-competition"
