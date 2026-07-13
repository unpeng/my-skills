#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# run_app.sh —— 588170 盯盘助手启动脚本
#
# 职责：
#   1. 在候选 Python 中挑选一个"带 tkinter 且 Tk 版本 >= 8.6"的解释器。
#      （macOS 系统自带 Python 的 Tk 常为 8.5，存在窗口初次显示白屏的
#      已知缺陷，见 app/monitor_app.py 的 _force_repaint 注释；优先避开。）
#   2. 检查 pandas / numpy / requests 是否已安装，缺失则尝试用该 Python
#      的 pip 安装 qstock/requirements.txt（失败不阻断，因为很多环境
#      已自带这三个包）。
#   3. 用选定的 Python 启动 app/main_app.py。
#
# 用法：
#   ./run_app.sh
#   （或 bash run_app.sh；首次使用需 chmod +x run_app.sh）
#
# 可选环境变量：
#   PYTHON_BIN  显式指定使用的 Python 解释器路径，跳过自动探测。
#     例：PYTHON_BIN=/opt/homebrew/bin/python3.13 ./run_app.sh

set -euo pipefail

# 脚本所在目录即技能根目录，所有相对路径均基于此解析，与调用者的当前
# 工作目录无关（cd 到别处执行本脚本也能正常工作）。
SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SKILL_ROOT"

# ----------------------------------------------------------------------
# 第一步：探测可用的 Python 解释器（要求：能 import tkinter，且 Tk >= 8.6）
# ----------------------------------------------------------------------

# 探测某个候选 Python 是否满足"可用 tkinter + Tk>=8.6"，满足则把其
# TkVersion 打印到 stdout，供下面排序挑选最优候选；不满足则不输出。
_check_py() {
  local py="$1"
  command -v "$py" >/dev/null 2>&1 || return 1
  "$py" -c '
import sys
try:
    import tkinter
except Exception:
    sys.exit(1)
ver = tkinter.TkVersion
if ver < 8.6:
    sys.exit(1)
print(ver)
' 2>/dev/null
}

CANDIDATES=(
  "${PYTHON_BIN:-}"
  "/opt/homebrew/bin/python3.13"
  "/opt/homebrew/bin/python3.12"
  "/opt/homebrew/bin/python3.11"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
  "python3"
  "python"
)

PY=""
FALLBACK_PY=""
for cand in "${CANDIDATES[@]}"; do
  [ -z "$cand" ] && continue
  ver="$(_check_py "$cand" || true)"
  if [ -n "$ver" ]; then
    PY="$cand"
    echo "使用 Python: $PY (Tk $ver)"
    break
  fi
  # 记录第一个"至少能 import tkinter"的候选，作为最终兜底（哪怕 Tk<8.6）。
  if [ -z "$FALLBACK_PY" ] && command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c "import tkinter" >/dev/null 2>&1; then
      FALLBACK_PY="$cand"
    fi
  fi
done

if [ -z "$PY" ]; then
  if [ -n "$FALLBACK_PY" ]; then
    PY="$FALLBACK_PY"
    echo "⚠️  未找到 Tk>=8.6 的 Python，回退使用 $PY（Tk 版本较旧，" >&2
    echo "   macOS 上可能出现窗口初次显示白屏的已知缺陷）。" >&2
    echo "   建议：brew install python-tk@3.13 后重新运行本脚本。" >&2
  else
    echo "❌ 未找到任何可用的 Python + tkinter 组合，无法启动 GUI。" >&2
    echo "   请安装 Python3 及 tkinter 支持，例如：" >&2
    echo "     brew install python-tk@3.13" >&2
    exit 1
  fi
fi

# ----------------------------------------------------------------------
# 第二步：检查/安装依赖（失败不阻断，很多环境已自带这三个包）
# ----------------------------------------------------------------------
if ! "$PY" -c "import pandas, numpy, requests" >/dev/null 2>&1; then
  echo "检测到缺少依赖，尝试安装 qstock/requirements.txt ..."
  "$PY" -m pip install -r qstock/requirements.txt -q || \
    echo "⚠️  依赖安装失败，若 pandas/numpy/requests 已存在可忽略此警告。" >&2
fi

# ----------------------------------------------------------------------
# 第三步：启动应用
# ----------------------------------------------------------------------
echo "启动 588170 盯盘助手 ..."
exec "$PY" app/main_app.py
