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

# uv を前提にしている。代替経路は用意しない。
# 代替を持つと「uv が無い環境でだけ挙動が違う」状態が生まれ、
# 再現性を土台にしているこの基盤では、その状態自体が害になる。
if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'MSG'
uv が見つからない。このリポジトリは uv を前提にしている。

  macOS / Linux : curl -LsSf https://astral.sh/uv/install.sh | sh
  Homebrew      : brew install uv
  Windows       : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

入れ直してから、もう一度このスクリプトを実行する。
MSG
  exit 2
fi
echo "== uv: $(uv --version)"

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

# コンペのデータ置き場と提出物置き場。.gitignore でコミット対象から外してある。
mkdir -p data submissions
if [ ! -f data/README.md ]; then
  cat > data/README.md <<'MD'
# データ置き場

**コンペから受け取ったデータ**をここに置く。`.gitignore` で除外してある。

ほぼ確実に再配布禁止なので、リポジトリに入れない。
どこから取ってきたか、どう展開したかは `knowledge/operations.md` に書く。

自分で作った出力（提出物・中間生成物）はここではなく `submissions/` や
`output/` に置く。そちらは再配布禁止の対象ではない。
MD
fi
if [ ! -f submissions/README.md ]; then
  cat > submissions/README.md <<'MD'
# 提出物置き場

ここに置いたものは既定で `.gitignore` から外れている。
ただし**再配布禁止が理由ではない**。提出物はコンペのデータではなく、自分の予測。

除外しているのは、開催中の公開リポジトリに置くと多くのコンペで禁止されている
「チーム外への共有」に当たりうるため。リポジトリの可視性はテンプレート側から
判別できないので安全側に倒してある。
**private なリポジトリなら `.gitignore` から `submissions/` を外してよい。**

**`<実験ID>.csv` の名前で置く**（例: `exp0007.csv`）。
`uv run expctl lb exp0007 --public 0.8734` がその名前でファイルを探し、
sha256 と行数を `metrics.json` に記録する。

なぜ指紋を残すか。モデルの重みも提出物も git に入れないので、
これが無いと「LB 0.8734 を出したのはどのファイルか」を
実験を回し直すまで確かめられない。行数も見るのは、
途中で切れたファイルを出す事故がよくあるため。

Kaggle の Code Competition のようにノートブックが実行される形式なら、
ここは空のままでよい。`competition.yaml` の `submission.kind` を
`notebook` にすると、`expctl lb` が提出ファイルを探さなくなる。
MD
fi

# 依存を入れて、動くことを確かめる。
echo
echo "uv sync を実行する。"
uv sync
echo
if uv run expctl --help >/dev/null 2>&1; then
  echo "expctl が動くことを確認した（uv run expctl status で状態が見られる）。"
else
  echo "expctl を実行できなかった。uv sync の出力を確認する。" >&2
  exit 1
fi

echo
echo "次にやること: Claude Code を起動して /init-competition"
