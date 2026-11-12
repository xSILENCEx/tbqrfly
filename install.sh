#!/usr/bin/env bash
set -euo pipefail

REPO="${TBQRFLY_REPO:-xSILENCEx/tbqrfly}"
REF="${TBQRFLY_REF:-main}"
HOME_DIR="${TBQRFLY_HOME:-$HOME/.tbqrfly}"
BIN_DIR="${TBQRFLY_BIN:-$HOME/.local/bin}"

echo "==> tbqrfly installer"
echo "    repo:  ${REPO}@${REF}"
echo "    home:  ${HOME_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未找到 python3，请先安装 Python ≥ 3.9" >&2
  exit 2
fi

PY_VER="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
PY_OK="$(python3 - <<'PY'
import sys
print(0 if sys.version_info >= (3, 9) else 1)
PY
)"
if [[ "${PY_OK}" != "0" ]]; then
  echo "错误: 需要 Python ≥ 3.9，当前为 ${PY_VER}" >&2
  exit 2
fi
echo "✓ python3 ${PY_VER}"

mkdir -p "${HOME_DIR}" "${BIN_DIR}"
VENV="${HOME_DIR}/venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "==> 创建 venv"
  python3 -m venv "${VENV}"
fi

echo "==> 安装 tbqrfly"
"${VENV}/bin/pip" install --upgrade pip >/dev/null
if ! "${VENV}/bin/pip" install --upgrade "git+https://github.com/${REPO}.git@${REF}"; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "${SCRIPT_DIR}/pyproject.toml" ]]; then
    echo "==> GitHub 安装失败，回退本地目录: ${SCRIPT_DIR}"
    "${VENV}/bin/pip" install --upgrade "${SCRIPT_DIR}"
  else
    echo "错误: 安装失败" >&2
    exit 1
  fi
fi

ln -sfn "${VENV}/bin/tbqrfly" "${BIN_DIR}/tbqrfly"
echo "✓ 已安装: ${BIN_DIR}/tbqrfly"

case ":$PATH:" in
  *":${BIN_DIR}:"*) ;;
  *)
    echo
    echo "请将以下内容加入 shell 配置后重新打开终端:"
    echo "  export PATH=\"${BIN_DIR}:\$PATH\""
    ;;
esac

echo
echo "完成。常用命令:"
echo "  tbqrfly init"
echo "  tbqrfly doctor"
echo "  tbqrfly run"
echo "  tbqrfly update"
