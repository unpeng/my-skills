# Implementation Plan: realtime-monitor-app（588170 本地桌面盯盘应用）

## Overview

（概述）

采用 **Python + tkinter（标准库）+ Hypothesis** 实现，构建在既有 `qstock` 包之上，
新代码放在与 `qstock/` 同级的 `app/` 包中（见设计"代码放置位置"）。实现顺序为：先搭
应用骨架与共享数据模型，再自底向上实现各**纯逻辑/持久化组件**（Settings_Store、
Position_Manager、Trade_Logger、Variable_Calculator、Rule_Engine、Alert_Manager），
每个组件紧跟其属性测试与单元测试；随后实现**调度层**（Quote_Poller）、**大模型接入**
（LLM_Client）与 **UI 层**（Monitor_App），最后由 `main_app.py` 组装启动并统一跑测试。

复用约定：`app/main_app.py` 启动时 `sys.path.insert(0, <技能根>/qstock)`；各封装组件
`from strategy.monitor import compute_monitor_variables, MonitorInputError`、
`from strategy.position_store import save_position/load_position/clear_position/append_decision_log/read_decision_log`。

属性测试统一使用 Hypothesis，每个属性测试 `@settings(max_examples=100)`（至少 100 次
迭代），并在测试体内以注释
`# Feature: realtime-monitor-app, Property {number}: {property_text}` 标注。带 `*`
的子任务为可选测试任务。

## Tasks

- [ ] 1. 搭建 app 包骨架与共享数据模型
  - 在 `skills/semiconductor-monitor/app/` 下创建 `__init__.py`
  - 创建 `app/models.py`：定义 `TradingSession` 枚举（集合竞价/开盘/盘中/尾盘/非交易时段）
    与共享 dataclass：`RoundResult`、`SessionAdvice`、`Signal`、`Alert`、`PositionInput`、
    `PositionForm`、`ValidationResult`、`SaveResult`、`AddResult`、`LogEntry`、`LLMConfig`、
    `LLMResult`（字段对齐设计"Data Models"章节）
  - 在 `app/__init__.py` 中封装把 `<技能根>/qstock` 加入 `sys.path` 的复用辅助函数，供各
    组件导入 qstock 能力
  - _Requirements: 3.1, 6.1, 7.6_

- [ ] 2. 实现配置持久化 Settings_Store
  - [ ] 2.1 实现 `app/settings_store.py`
    - 读写 `<技能根>/.local/app_settings.json`（缺省时创建默认配置）
    - `get_interval`/`set_interval`（校验 [5,3600] 整数，默认 60）、`get_llm_config`/
      `set_llm_config`、`is_sound_enabled`、`is_disclaimer_acknowledged`/确认写回
    - _Requirements: 2.2, 2.3, 2.4, 6.6, 7.1, 9.3_
  - [ ]* 2.2 编写轮询间隔校验属性测试 `app/tests/test_prop_18_interval.py`
    - **Property 18: 轮询间隔校验**
    - **Validates: Requirements 2.3, 2.4**
  - [ ]* 2.3 编写 Settings_Store 单元测试 `app/tests/test_settings_store.py`
    - 覆盖默认间隔 60（2.2）、LLM 三项配置往返持久化（7.1）、免责声明确认位往返（9.3）
    - _Requirements: 2.2, 7.1, 9.3_

- [ ] 3. 实现持仓管理 Position_Manager
  - [ ] 3.1 实现 `app/position_manager.py`
    - `validate` 校验持仓数量/加权成本/可用资金/止损三选一及各边界；`save` 通过校验后调
      `save_position` 覆盖写入，捕获写盘异常返回失败结果；`load` 启动回填
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_
  - [ ]* 3.2 编写持仓数量与成本校验属性测试 `app/tests/test_prop_19_position.py`
    - **Property 19: 持仓数量与成本校验**
    - **Validates: Requirements 1.3**
  - [ ]* 3.3 编写可用资金校验属性测试 `app/tests/test_prop_20_cash.py`
    - **Property 20: 可用资金校验**
    - **Validates: Requirements 1.4**
  - [ ]* 3.4 编写止损三选一与边界属性测试 `app/tests/test_prop_21_stoploss.py`
    - **Property 21: 止损设定三选一与数值边界**
    - **Validates: Requirements 1.5, 1.7**
  - [ ]* 3.5 编写 Position_Manager 单元测试 `app/tests/test_position_manager.py`
    - 覆盖保存成功（1.1）、启动回填（1.2）、覆盖写入（1.6）、写盘失败保留输入（1.8）
    - _Requirements: 1.1, 1.2, 1.6, 1.8_

- [ ] 4. 实现交易记录 Trade_Logger
  - [ ] 4.1 实现 `app/trade_logger.py`
    - `add` 校验操作类型 ∈ {做T买入,做T卖出,止损,减仓} 且价格>0 后调 `append_decision_log`；
      `list` 调 `read_decision_log(code="588170", limit=50)` 按时间由早到晚返回最多 50 条
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - [ ]* 4.2 编写交易记录输入校验属性测试 `app/tests/test_prop_22_tradelog_validate.py`
    - **Property 22: 交易记录输入校验**
    - **Validates: Requirements 8.2, 8.3**
  - [ ]* 4.3 编写交易记录展示排序与截断属性测试 `app/tests/test_prop_23_tradelog_list.py`
    - **Property 23: 交易记录展示排序与截断**
    - **Validates: Requirements 8.4**
  - [ ]* 4.4 编写 Trade_Logger 单元测试 `app/tests/test_trade_logger.py`
    - 覆盖保存成功提示（8.1）、无记录空状态（8.5）
    - _Requirements: 8.1, 8.5_

- [ ] 5. 实现盯盘变量计算封装 Variable_Calculator
  - [ ] 5.1 实现 `app/variable_calculator.py`
    - `calculate(position, timeout=10.0)`：把持仓/止损设定转参并调 `compute_monitor_variables`，
      施加 10 秒超时；从指标结果补取 `macd_hist` 末两根派生 `macd_hist_prev`/`macd_hist_curr`；
      成功封装 `RoundResult(ok=True, vars=..., price_source=...)`，异常/超时/`MonitorInputError`
      兜底封装 `RoundResult(ok=False, error=...)`（不侵入 qstock 契约）
    - _Requirements: 3.1_
  - [ ]* 5.2 编写 Variable_Calculator 单元测试 `app/tests/test_variable_calculator.py`
    - mock `compute_monitor_variables`：验证成功封装与派生字段、超时按失败处理、
      参数错误兜底为 error 结果
    - _Requirements: 3.1_

- [ ] 6. 实现 Rule_Engine 交易时段判定
  - [ ] 6.1 实现 `app/rule_engine.py` 的 `TradingSession` 与 `classify_session`
    - 按设计时间边界把全天映射为五类互斥且穷尽时段；非交易日与时段间隙归 `NON_TRADING`
    - _Requirements: 4.5_
  - [ ]* 6.2 编写时段判定属性测试 `app/tests/test_prop_01_session.py`
    - **Property 1: 交易时段判定互斥且穷尽**
    - **Validates: Requirements 4.5**

- [ ] 7. 实现 Rule_Engine 分时段决策树（`session_advice`）
  - [ ] 7.1 在 `app/rule_engine.py` 实现 `session_advice`
    - 用有序区间边界列表实现集合竞价情景 1-7、开盘情景 A-F、尾盘决策树（互斥且穷尽），
      盘中展示做 T 机会与关键价位突破建议、做 T 可买份数复用 `做T可用资金上限`/`做T可买份数`；
      关键价位含 None 时返回 `data_available=False` 不出情景建议
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.6, 4.7_
  - [ ]* 7.2 编写集合竞价情景属性测试 `app/tests/test_prop_02_call_auction.py`
    - **Property 2: 集合竞价情景互斥且穷尽**
    - **Validates: Requirements 4.1**
  - [ ]* 7.3 编写开盘情景属性测试 `app/tests/test_prop_03_opening.py`
    - **Property 3: 开盘情景互斥且穷尽**
    - **Validates: Requirements 4.2**
  - [ ]* 7.4 编写尾盘决策树属性测试 `app/tests/test_prop_04_closing.py`
    - **Property 4: 尾盘决策树分支互斥且穷尽**
    - **Validates: Requirements 4.3**
  - [ ]* 7.5 编写做 T 可买份数属性测试 `app/tests/test_prop_05_t_shares.py`
    - **Property 5: 做 T 可买份数计算正确**
    - **Validates: Requirements 4.6**
  - [ ]* 7.6 编写数据不足降级属性测试 `app/tests/test_prop_06_data_unavailable.py`
    - **Property 6: 数据不足时降级不产出情景建议**
    - **Validates: Requirements 4.7**
  - [ ]* 7.7 编写盘中做 T 建议单元测试 `app/tests/test_rule_engine_intraday.py`
    - 验证盘中做 T 机会与关键价位突破建议文本（4.4）
    - _Requirements: 4.4_

- [ ] 8. 实现 Rule_Engine 买入/卖出/止损信号（`evaluate_signals`）
  - [ ] 8.1 在 `app/rule_engine.py` 实现 `evaluate_signals`
    - 买入/卖出条件集合（含 `macd_hist_prev/curr` 跨周期条件）、止损（当前价<止损位）、
      放量下跌止损（今量>20日均量×1.5 且当日跌幅>3%）；比较前先判 None，None 条件既不计成立
      也不计可参与；某类可参与数<2 不生成、买卖需≥2 成立才生成
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [ ]* 8.2 编写买入信号属性测试 `app/tests/test_prop_07_buy.py`
    - **Property 7: 买入信号生成当且仅当成立条件数达标**
    - **Validates: Requirements 5.1, 5.7**
  - [ ]* 8.3 编写卖出信号属性测试 `app/tests/test_prop_08_sell.py`
    - **Property 8: 卖出信号生成当且仅当成立条件数达标**
    - **Validates: Requirements 5.2, 5.7**
  - [ ]* 8.4 编写止损信号属性测试 `app/tests/test_prop_09_stoploss.py`
    - **Property 9: 止损信号生成当且仅当当前价低于止损位**
    - **Validates: Requirements 5.3**
  - [ ]* 8.5 编写放量下跌止损属性测试 `app/tests/test_prop_10_volume_stoploss.py`
    - **Property 10: 放量下跌止损信号生成条件**
    - **Validates: Requirements 5.4**
  - [ ]* 8.6 编写缺失值稳健性属性测试 `app/tests/test_prop_11_none_robust.py`
    - **Property 11: 缺失值稳健性（None 不影响其余判断）**
    - **Validates: Requirements 5.5, 5.6**

- [ ] 9. Checkpoint - 确保纯逻辑核心测试通过
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. 实现声音与提醒 Alert_Manager
  - [ ] 10.1 实现 `app/sound.py` 跨平台提示音
    - 按 `sys.platform` 选择 `winsound`（Windows）/`afplay`（macOS）/`paplay`/`aplay`（Linux）
      后端，`SoundPlayer.play()` ≤3 秒；任何后端异常静默降级为仅视觉，不影响主流程
    - _Requirements: 6.2, 6.6_
  - [ ] 10.2 实现 `app/alert_manager.py`
    - `process(signals, vars, now)`：信号提醒含类型/代码 588170/触发价/时间；当前价首次达到或
      穿越止损位/做 T 买入位/做 T 卖出位时提醒并标明价位类型与价格；以指纹
      `(信号类型, 关联关键价格)` 去重（不变不重复、变化再提醒）；按声音开关决定是否 `play`
    - _Requirements: 6.1, 6.3, 6.4, 6.5, 6.2, 6.6_
  - [ ]* 10.3 编写提醒内容要素属性测试 `app/tests/test_prop_12_alert_content.py`
    - **Property 12: 提醒内容包含必需要素**
    - **Validates: Requirements 6.1**
  - [ ]* 10.4 编写首次触及关键价位属性测试 `app/tests/test_prop_13_first_touch.py`
    - **Property 13: 首次触及关键价位才提醒**
    - **Validates: Requirements 6.3**
  - [ ]* 10.5 编写信号去重属性测试 `app/tests/test_prop_14_dedup.py`
    - **Property 14: 信号提醒去重（指纹不变不重复、变化必提醒）**
    - **Validates: Requirements 6.4, 6.5**
  - [ ]* 10.6 编写声音开关单元测试 `app/tests/test_alert_manager.py`
    - mock `SoundPlayer`：验证开启时播放一次、关闭时仅视觉提醒（6.2/6.6）
    - _Requirements: 6.2, 6.6_

- [ ] 11. 实现后台轮询 Quote_Poller
  - [ ] 11.1 实现 `app/quote_poller.py`
    - `threading.Thread(daemon=True)` + `stop_event`/`refresh_event`/`busy` 标志；
      `refresh_event.wait(timeout=interval)` 计时；`start`/`stop`（当前轮完成后退出）/
      `request_refresh`（忙时返回 False）；每轮 `try/except` 调 Variable_Calculator 入队
      `RoundResult`，单轮失败/超时不退出循环、下一间隔重试
    - _Requirements: 2.1, 2.5, 2.6, 2.7, 2.8, 10.2, 10.5_
  - [ ]* 11.2 编写轮询控制集成测试 `app/tests/test_quote_poller_control.py`
    - mock calculator + 短间隔：验证间隔触发误差（2.1）、停止（2.5）、立即刷新（2.6）、
      忙时忽略（2.8）；1–3 个代表性用例，不做 100 次迭代
    - _Requirements: 2.1, 2.5, 2.6, 2.8_
  - [ ]* 11.3 编写单轮超时与失败重试集成测试 `app/tests/test_quote_poller_retry.py`
    - mock 抛异常/超时：验证入队 error 结果、循环继续重试、进程不终止（2.7/10.2/10.5）
    - _Requirements: 2.7, 10.2, 10.5_

- [ ] 12. 实现外盘/新闻大模型接入 LLM_Client
  - [ ] 12.1 实现 `app/llm_client.py`（含 `WebTools` 与配置门控/失败判定辅助）
    - `WebTools.web_search`/`web_fetch`；`fetch_market_briefing` 在后台线程跑工具调用循环，
      声明 WebSearch/WebFetch，`max_tool_rounds=8`，整体 30 秒超时；不支持工具时回退为无 tools
      直接问答；提供 `config_complete(cfg)` 门控与 `is_briefing_failure(text)`（是否含 SOX/
      北向/A50 任一）；结果封装 `LLMResult`，绝不写入持仓/止损
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12_
  - [ ]* 12.2 编写工具调用轮数上限属性测试 `app/tests/test_prop_24_tool_rounds.py`
    - **Property 24: 工具调用循环轮数上限**
    - **Validates: Requirements 7.5**
  - [ ]* 12.3 编写 LLM 配置完整性门控属性测试 `app/tests/test_prop_25_config_gate.py`
    - **Property 25: LLM 配置完整性门控**
    - **Validates: Requirements 7.8, 7.9**
  - [ ]* 12.4 编写外盘研判失败判定属性测试 `app/tests/test_prop_26_briefing_failure.py`
    - **Property 26: 外盘研判失败判定**
    - **Validates: Requirements 7.10**
  - [ ]* 12.5 编写 LLM_Client 单元测试 `app/tests/test_llm_client.py`
    - mock HTTP/WebTools：验证 WebSearch/WebFetch 分派（7.3/7.4）、成功展示带不确定标记（7.6）、
      不支持工具回退（7.7）、返回内容不落盘隔离（7.12）
    - _Requirements: 7.3, 7.4, 7.6, 7.7, 7.12_
  - [ ]* 12.6 编写 LLM 请求与超时集成测试 `app/tests/test_llm_client_timeout.py`
    - mock HTTP：验证请求体含 tools 声明（7.2）、整体 30 秒超时终止（7.11）
    - _Requirements: 7.2, 7.11_

- [ ] 13. 实现主窗口与编排 Monitor_App
  - [ ] 13.1 实现 `app/monitor_app.py`
    - 构建 tkinter 主窗口与各视图（持仓/变量指标/时段建议/信号/外盘新闻/交易记录/设置）；
      `root.after(200,...)` 消费队列取 `RoundResult`/`LLMResult`；持有 `last_good_result`
      失败轮不清空；首启免责声明门（确认前不进入主界面）；底部 `pack(side=BOTTOM,fill=X)`
      常驻免责声明条；抽出纯函数辅助 `format_indicator`（None→"数据不足暂不可用"）、
      `price_source_annotation`（realtime 无标注 / kline_fallback / kline_only）、
      `choose_display_result`（失败轮取最近成功轮）供属性测试
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 9.1, 9.2, 9.3, 9.4, 10.1, 10.3, 10.4, 10.6_
  - [ ]* 13.2 编写价格来源标注属性测试 `app/tests/test_prop_15_price_source.py`
    - **Property 15: 价格来源标注映射**
    - **Validates: Requirements 3.5, 3.7, 10.3, 10.6**
  - [ ]* 13.3 编写指标 None 渲染属性测试 `app/tests/test_prop_16_none_render.py`
    - **Property 16: 指标 None 值渲染映射**
    - **Validates: Requirements 3.4**
  - [ ]* 13.4 编写失败轮保留展示属性测试 `app/tests/test_prop_17_keep_last_good.py`
    - **Property 17: 失败轮保留最近成功轮展示值**
    - **Validates: Requirements 3.8, 10.4**
  - [ ]* 13.5 编写 Monitor_App 单元测试 `app/tests/test_monitor_app.py`
    - 验证首启免责声明门（9.1/9.3/9.4）、历史数据失败提示（10.1）、拆分跳空说明展示（3.6）
    - _Requirements: 3.6, 9.1, 9.3, 9.4, 10.1_
  - [ ]* 13.6 编写底部提示条 smoke 测试 `app/tests/test_monitor_app_smoke.py`
    - 视图切换时底部提示条常驻可见（9.2）、间隔默认 60（2.2）
    - _Requirements: 9.2, 2.2_

- [ ] 14. 组装入口 main_app.py
  - [ ] 14.1 实现 `app/main_app.py`
    - 注入 `<技能根>/qstock` 到 `sys.path`；实例化 Settings_Store/Position_Manager/
      Trade_Logger/Variable_Calculator/Rule_Engine/Alert_Manager/Quote_Poller/LLM_Client
      并装配进 `MonitorApp`，创建 `tk.Tk()` 后 `MonitorApp(root, deps).run()` 启动
    - _Requirements: 1.2, 2.1, 9.3_

- [ ] 15. Final checkpoint - 运行全部属性测试与单元测试
  - 运行 `python3 -m pytest app/tests/`（属性测试 `@settings(max_examples=100)`），确保
    Property 1–26 属性测试与全部单元/集成/smoke 测试可执行并通过；如有失败先定位修复
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- 带 `*` 的子任务为可选测试任务，可为快速 MVP 跳过；核心实现子任务不得标记为可选。
- 每个任务标注对应需求（`_Requirements: x.y_`）与属性（`**Validates: Requirements ...**`），
  保证可追溯。
- Property 1–26 每条对应一个独立属性测试子任务，紧邻其被测实现放置以尽早发现错误。
- Checkpoint（任务 9、15）用于增量校验；纯逻辑核心（Rule_Engine）先于 UI/调度完成并验证。
- 所有属性测试使用 Hypothesis 且 `@settings(max_examples=100)`，测试体内含
  `# Feature: realtime-monitor-app, Property {number}: {property_text}` 注释。
- 复用 qstock（`compute_monitor_variables`、`position_store` 系列）不改动其对外契约；
  新增配置存于技能根 `.local/app_settings.json`（已被 .gitignore 覆盖）。
- 本清单仅含编码相关任务（写代码/写测试/跑测试），不含部署、用户验收、收集反馈等非编码活动。

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1", "6.1", "10.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "3.3", "3.4", "3.5", "4.2", "4.3", "4.4", "5.2", "6.2", "7.1", "10.2", "11.1", "12.1"] },
    { "id": 3, "tasks": ["7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "8.1", "10.3", "10.4", "10.5", "10.6", "11.2", "11.3", "12.2", "12.3", "12.4", "12.5", "12.6"] },
    { "id": 4, "tasks": ["8.2", "8.3", "8.4", "8.5", "8.6", "13.1"] },
    { "id": 5, "tasks": ["13.2", "13.3", "13.4", "13.5", "13.6", "14.1"] }
  ]
}
```
