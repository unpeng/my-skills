# -*- coding: utf-8 -*-
from .technical import compute_all_indicators
from .fundamental import compute_fundamental_score

# 注意：StockPredictor 依赖 sklearn，不在此处预加载（会强制所有
# `from model.xxx import yyy` 都间接触发 sklearn 导入，导致仅需
# pandas/numpy/requests 的 monitor/info/position/log 命令报错）。
# 需要预测功能时请直接 `from model.predictor import StockPredictor`。
