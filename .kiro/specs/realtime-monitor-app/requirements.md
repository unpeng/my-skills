# Requirements Document

## Introduction

本功能在现有 `semiconductor-monitor` 技能（`qstock` 命令行工具）之上，构建一套用
Python + tkinter 实现的本地桌面盯盘应用（以下称"盯盘应用"）。应用面向 588170 科创板
半导体 ETF，解决用户"盘中无法持续获得实时建议、只能反复手动提问"的痛点。

应用在交易时段按固定间隔（默认 60 秒，可配置）自动轮询实时行情，复用 `qstock`
现有的盯盘变量与技术指标计算能力，把技能文档（SKILL.md）中原本由 AI 解读的
分时段决策树（集合竞价情景 1-7、开盘情景 A-F、盘中监控、尾盘决策树）与买入/卖出/
止损信号规则**代码化**，按当前所处交易时段自动匹配并展示对应操作建议。当行情触及
关键价位或买卖/止损信号被触发时，应用通过应用内弹窗/高亮加声音主动提醒用户。

外盘（SOX 指数、北向资金、A50 期货）与半导体新闻数据通过用户配置的 OpenAI 格式
大模型接口获取（复刻技能中依赖大模型联网搜索的方式）。为使模型能真正联网获取实时
外盘与新闻数据而非仅凭自身知识作答，应用按 OpenAI 兼容的工具调用（tools /
function calling）机制向模型声明 WebSearch（按查询词联网搜索）与 WebFetch（按 URL
抓取网页内容）两个工具，并在模型请求调用工具时执行工具、回传结果，支持多轮工具调用
循环。由于模型不一定支持工具调用或联网能力，应用必须容错：对工具调用轮数设上限以避免
无限循环，不支持工具调用时回退到不带工具的直接问答，获取失败时如实提示而非编造数值。

应用支持在界面内录入/修改持仓信息并记录做 T、止损等实际操作，复用 `qstock` 现有的
本地持仓持久化与决策日志能力。所有数据处理均在本地完成（大模型调用除外），不额外
上传用户数据。

## Glossary

- **Monitor_App**: 本功能构建的 tkinter 本地桌面盯盘应用整体。
- **Quote_Poller**: Monitor_App 内负责按固定间隔轮询实时行情并触发一轮计算的组件。
- **Variable_Calculator**: 复用 `qstock` 的 `compute_monitor_variables`，计算昨收价、
  当前价、止损位、做 T 价位、关键价位、持仓盈亏及 RSI/MACD/KDJ/布林带等技术指标的组件。
- **Rule_Engine**: Monitor_App 内把 SKILL.md 的分时段决策树与买卖/止损信号规则代码化，
  依据当前交易时段与行情变量输出操作建议与触发信号的组件。
- **Alert_Manager**: Monitor_App 内负责在触发条件满足时以应用内弹窗/高亮加声音方式提醒
  用户的组件。
- **Position_Manager**: Monitor_App 内负责录入、修改、展示持仓信息并调用 `qstock` 本地
  持仓持久化（`.local/positions.json`）的组件。
- **Trade_Logger**: Monitor_App 内负责记录做 T、止损、减仓等操作并调用 `qstock` 决策
  日志（`log`）的组件。
- **LLM_Client**: Monitor_App 内按 OpenAI 格式调用用户配置的大模型接口，用于获取外盘与
  新闻研判的组件；具备向模型声明 Web_Tools、执行模型请求的工具调用并回传结果以驱动多轮
  工具调用循环的能力。
- **Web_Tools**: LLM_Client 向大模型声明的可用工具集合，包含 WebSearch 与 WebFetch 两个
  工具。
- **WebSearch**: Web_Tools 中的联网搜索工具，接受查询词，联网搜索后返回结果摘要。
- **WebFetch**: Web_Tools 中的网页抓取工具，接受 URL，抓取并返回该网页的内容。
- **工具调用循环**: LLM_Client 向模型声明 Web_Tools 后，模型在响应中请求调用工具、
  LLM_Client 执行工具并将结果回传给模型继续生成，如此往复直至模型给出最终研判或达到
  工具调用轮数上限的交互过程。
- **Settings_Store**: Monitor_App 内负责持久化用户配置（轮询间隔、大模型接口地址/密钥/
  模型名等）的组件。
- **交易时段**: 集合竞价（9:15-9:25）、开盘（9:30-10:00）、盘中（10:00-11:30、
  13:00-14:00）、尾盘（14:00-15:00）、非交易时段（其余时间及非交易日）。
- **关键价位**: 止损位、回本价、做 T 买入位、做 T 卖出位、昨收±2%/±2.5%/±4%、成本±2%/±4%
  等 Variable_Calculator 计算出的价格阈值。
- **价格来源**: `qstock` 返回的当前价数据来源标记，取值为 `realtime`（实时接口成功）、
  `kline_fallback`（回退到最近交易日 K 线收盘价）、`kline_only`（仅历史 K 线收盘价）。
- **标的代码**: 固定为 `588170`，本功能不支持切换其他标的。

## Requirements

### Requirement 1: 持仓信息录入与管理

**User Story:** 作为用户，我想在应用界面内录入并修改我的 588170 持仓信息，以便所有盯盘
计算基于我的实际持仓进行。

#### Acceptance Criteria

1. WHEN 用户在界面提交持仓数量、加权成本、可用资金与止损设定且各项均通过校验，THE Position_Manager SHALL 调用 `qstock` 持仓持久化能力将持仓信息保存到 `.local/positions.json`，并展示保存成功的确认提示。
2. WHEN Monitor_App 启动且 `.local/positions.json` 存在已保存的 588170 持仓信息，THE Position_Manager SHALL 读取并在界面展示持仓数量、加权成本、可用资金与止损设定。
3. IF 用户提交的持仓数量不是 1 至 1,000,000,000 的正整数份额，或加权成本不是 0.01 至 999,999.99 的正数，THEN THE Position_Manager SHALL 展示指明越界字段的输入错误提示并拒绝保存该持仓信息，且保留原有 `.local/positions.json` 内容不变。
4. IF 用户提交的可用资金不是 0 至 999,999,999.99（含 0）的非负数，THEN THE Position_Manager SHALL 展示输入错误提示并拒绝保存该持仓信息，且保留原有 `.local/positions.json` 内容不变。
5. THE Position_Manager SHALL 要求用户以最大亏损比例、最大亏损金额、直接指定止损价三种方式中恰好选择一种设定止损位。
6. WHEN 用户在界面修改并提交已存在的 588170 持仓信息且通过校验，THE Position_Manager SHALL 用新值覆盖 `.local/positions.json` 中对应标的的持仓信息。
7. IF 用户设定止损位所填数值越界（最大亏损比例不在 0.01% 至 100% 之间、最大亏损金额不为正数、或直接指定止损价不为正数或不低于加权成本），THEN THE Position_Manager SHALL 展示输入错误提示并拒绝保存该持仓信息。
8. IF Position_Manager 保存持仓信息到 `.local/positions.json` 失败，THEN THE Position_Manager SHALL 展示保存失败提示并保留用户已输入的内容。

### Requirement 2: 实时行情轮询

**User Story:** 作为用户，我想让应用按固定间隔自动获取最新行情，以便无需手动反复查询。

#### Acceptance Criteria

1. WHILE Monitor_App 处于运行状态且轮询已启用，THE Quote_Poller SHALL 每经过 Settings_Store 中配置的轮询间隔（触发时间误差不超过 ±1 秒）触发一轮行情获取与计算。
2. THE Settings_Store SHALL 将轮询间隔的默认值设为 60 秒。
3. WHEN 用户在设置界面提交介于 5 秒至 3600 秒（含 5 秒与 3600 秒两个边界）的整数轮询间隔，THE Settings_Store SHALL 持久化该轮询间隔并使 Quote_Poller 在下一轮使用该间隔。
4. IF 用户提交的轮询间隔为非整数、小于 5 秒或大于 3600 秒，THEN THE Settings_Store SHALL 展示指示输入值超出 5 至 3600 秒允许范围的错误提示，并保留原轮询间隔不变。
5. WHEN 用户在界面点击停止轮询，THE Quote_Poller SHALL 在当前进行中的一轮完成后停止后续周期性行情获取。
6. WHEN 用户在界面点击立即刷新，THE Quote_Poller SHALL 立即触发一轮行情获取与计算。
7. IF 某一轮行情获取失败或超过 10 秒未返回数据，THEN THE Quote_Poller SHALL 展示指示本轮获取失败的提示、保留上一轮成功获取的行情数据，并在下一个轮询间隔继续尝试获取。
8. WHILE 某一轮行情获取与计算尚未完成，IF 用户点击立即刷新，THEN THE Quote_Poller SHALL 忽略本次立即刷新请求直至当前轮次完成。

### Requirement 3: 盯盘变量与技术指标计算展示

**User Story:** 作为用户，我想在界面上看到当前价、关键价位、持仓盈亏与技术指标，以便掌握
盘面全貌。

#### Acceptance Criteria

1. WHEN Quote_Poller 触发一轮行情获取，THE Variable_Calculator SHALL 调用 `qstock` 的 `compute_monitor_variables` 计算 588170 的盯盘变量与技术指标并返回结果（成功时为变量字典，失败时为含错误标识的结果）。
2. WHEN Variable_Calculator 返回不含错误标识的计算结果，THE Monitor_App SHALL 在 1 秒内在界面展示当前价、昨收价、止损位、回本价、做 T 买入位、做 T 卖出位、持仓市值、浮动盈亏、盈亏比例与距回本比例。
3. WHEN Variable_Calculator 返回不含错误标识的计算结果，THE Monitor_App SHALL 在界面展示 RSI、MACD、KDJ、布林带指标值。
4. IF Variable_Calculator 返回的某项技术指标值为 None（数据不足），THEN THE Monitor_App SHALL 将该指标值字段的展示内容替换为文本"数据不足暂不可用"。
5. WHEN Variable_Calculator 返回的价格来源为 `kline_fallback` 或 `kline_only`，THE Monitor_App SHALL 在当前价旁标注文本提示当前价可能非实时并提示用户核实实时行情。
6. IF Variable_Calculator 返回结果的 `_数据质量_检测到拆分跳空` 为 True，THEN THE Monitor_App SHALL 在界面展示 `_数据质量_说明` 的原文内容。
7. WHEN Variable_Calculator 返回的价格来源为 `realtime`，THE Monitor_App SHALL 展示当前价且不显示任何"当前价可能非实时"的标注。
8. IF Variable_Calculator 返回含错误标识的结果（如无法获取历史数据），THEN THE Monitor_App SHALL 在界面展示指示数据获取失败的错误提示，并保留上一轮成功展示的数值不将其清空。

### Requirement 4: 分时段操作建议

**User Story:** 作为用户，我想让应用根据当前所处交易时段自动展示对应的操作建议，以便获得
与技能一致的分时段指导。

#### Acceptance Criteria

1. WHILE 当前时间处于 09:15:00 至 09:25:59 的集合竞价时段，THE Rule_Engine SHALL 依据竞价价格与关键价位将当前状态归入集合竞价情景 1 至情景 7 中且仅归入其中一个（各情景互斥且穷尽覆盖全部竞价价格与关键价位关系），并展示该情景对应建议。
2. WHILE 当前时间处于 09:30:00 至 09:59:59 的开盘时段，THE Rule_Engine SHALL 依据开盘价与关键价位将当前状态归入开盘情景 A 至情景 F 中且仅归入其中一个（各情景互斥且穷尽），并展示该情景对应建议。
3. WHILE 当前时间处于 14:00:00 至 14:59:59 的尾盘时段，THE Rule_Engine SHALL 依据当前价与加权成本、止损位的关系将当前状态归入尾盘决策树的唯一一个分支（各分支互斥且穷尽），并展示该分支对应建议。
4. WHILE 当前时间处于 10:00:00 至 11:29:59 或 13:00:00 至 13:59:59 的盘中时段，THE Rule_Engine SHALL 依据当前价与关键价位展示做 T 机会与关键价位突破的建议。
5. WHILE 当前时间处于集合竞价、开盘、盘中、尾盘四类时段之外的非交易时段（含 09:26:00 至 09:29:59、11:30:00 至 12:59:59、15:00:00 及之后、以及非交易日全天），THE Monitor_App SHALL 展示盘前研判信息且不匹配盘中决策树建议。
6. WHERE Rule_Engine 生成做 T 建议，THE Rule_Engine SHALL 按"做 T 可用资金上限 = 可用资金 × 80%"与"做 T 可买份数 = 向下取整（做 T 可用资金上限 ÷ 做 T 买入位）"计算并展示做 T 可买份数。
7. IF 当前时段所需的行情数据不足或所依赖的关键价位为空，THEN THE Rule_Engine SHALL 不展示该时段对应的操作建议，并展示提示信息说明数据不可用。

### Requirement 5: 买入/卖出/止损信号判断

**User Story:** 作为用户，我想让应用按量化阈值自动判断买卖与止损信号，以便及时发现操作时机
和风险。

#### Acceptance Criteria

1. WHEN 一轮计算完成且当前可参与判断的买入条件中有两个及以上成立，THE Rule_Engine SHALL 生成买入信号提示，其中各买入条件定义为：RSI 值小于 30、KDJ-J 值小于 0、当前价小于或等于布林带下轨值、上一周期 MACD 柱值小于 0 且当前周期 MACD 柱值大于或等于 0。
2. WHEN 一轮计算完成且当前可参与判断的卖出条件中有两个及以上成立，THE Rule_Engine SHALL 生成卖出信号提示，其中各卖出条件定义为：RSI 值大于 70、KDJ-J 值大于 100、当前价大于或等于布林带上轨值、上一周期 MACD 柱值大于 0 且当前周期 MACD 柱值小于或等于 0、回本距离绝对值小于 2%（回本距离绝对值 =｜(当前价 − 持仓成本价) ÷ 持仓成本价｜× 100%）。
3. WHEN 一轮计算完成且当前价小于止损位，THE Rule_Engine SHALL 生成止损信号提示。
4. WHEN 一轮计算完成且当日成交量大于 20 日平均成交量的 1.5 倍且当日跌幅大于 3%（当日跌幅 = (前一交易日收盘价 − 当前价) ÷ 前一交易日收盘价 × 100%），THE Rule_Engine SHALL 生成放量下跌止损信号提示。
5. WHERE 参与某类信号判断的某项技术指标值为 None（数据不足），THE Rule_Engine SHALL 将该项条件排除出该类信号的成立计数，且不将其计为成立也不计为不成立。
6. IF 某类信号判断所需的比较基准值（持仓成本价、止损位、前一交易日收盘价或 20 日平均成交量）为 None 或无法获取，THEN THE Rule_Engine SHALL 跳过依赖该基准值的对应条件判断，并保留其余可参与条件的判断结果。
7. IF 一轮计算完成后某类信号的可参与判断条件数量少于两个，THEN THE Rule_Engine SHALL 不生成该类信号提示。

### Requirement 6: 触发提醒

**User Story:** 作为用户，我想在关键价位或买卖/止损信号被触发时收到主动提醒，以便盘中不用
一直盯着屏幕。

#### Acceptance Criteria

1. WHEN Rule_Engine 生成买入、卖出或止损信号，THE Alert_Manager SHALL 在 2 秒内通过应用内弹窗或界面高亮展示该信号，且展示内容至少包含信号类型（买入/卖出/止损）、标的名称或代码、触发价格与触发时间。
2. WHEN Rule_Engine 生成买入、卖出或止损信号且声音提醒处于开启状态，THE Alert_Manager SHALL 在 2 秒内播放一次时长不超过 3 秒的提示音。
3. WHEN 当前价首次达到或穿越某一关键价位（止损位、做 T 买入位或做 T 卖出位之一），THE Alert_Manager SHALL 在 2 秒内提醒用户当前价已触及该关键价位，并在提醒内容中标明所触及的关键价位类型与对应价格。
4. WHEN 同一标的的同一信号在连续多轮计算中持续满足触发条件且信号状态未发生变化（信号类型与其关联的关键价格均与上一次提醒完全相同），THE Alert_Manager SHALL 仅在该信号首次满足触发条件时提醒一次，并在信号状态保持不变的后续计算轮次中不再重复提醒。
5. WHEN 已提醒过的信号发生状态变化（信号类型改变，或其关联的关键价格发生变化），THE Alert_Manager SHALL 再次向用户发出提醒。
6. IF 用户在设置中关闭声音提醒，THEN THE Alert_Manager SHALL 在触发信号时仅展示弹窗或界面高亮而不播放提示音。

### Requirement 7: 外盘与新闻大模型接入

**User Story:** 作为用户，我想让应用通过我配置的大模型接口并借助联网搜索与网页抓取工具
获取外盘与半导体新闻研判，以便在应用内看到基于实时联网数据、与技能一致的盘前环境信息。

#### Acceptance Criteria

1. THE Settings_Store SHALL 提供大模型接口地址、API 密钥、模型名称三项配置项，并将用户填写的值持久化保存，使其在应用重启后仍可读取。
2. WHEN 用户在界面点击获取外盘与新闻信息，THE LLM_Client SHALL 按 OpenAI 格式调用用户配置的大模型接口，在请求中向模型声明 WebSearch 与 WebFetch 两个可用工具，请求 SOX 指数、北向资金、A50 期货与半导体新闻的研判，并对本次获取设置最长 30 秒的整体响应等待上限。
3. WHEN 模型在响应中请求调用 WebSearch，THE LLM_Client SHALL 按模型给出的查询词联网搜索、将搜索结果摘要回传给模型，并驱动模型继续生成。
4. WHEN 模型在响应中请求调用 WebFetch，THE LLM_Client SHALL 按模型给出的 URL 抓取该网页内容、将抓取内容回传给模型，并驱动模型继续生成。
5. THE LLM_Client SHALL 将单次外盘与新闻获取的工具调用循环轮数限制在不超过 8 轮，并在达到该上限时停止继续调用工具、要求模型基于已有信息给出最终研判。
6. WHEN LLM_Client 在 30 秒内成功返回外盘与新闻研判内容，THE Monitor_App SHALL 在界面展示该研判内容，并附加"不确定信息，仅供参考"标记及本次获取的时间。
7. IF 用户配置的模型不支持工具调用（tools / function calling），THEN THE LLM_Client SHALL 回退到不向模型声明工具的直接问答方式发起调用，且 THE Monitor_App SHALL 展示指示本次研判未使用联网工具的提示。
8. IF 大模型接口配置项（接口地址、密钥、模型名称）中任一项为空，THEN THE Monitor_App SHALL 阻止发起调用并提示用户先完成大模型接口配置。
9. WHERE 大模型接口配置项（接口地址、密钥、模型名称）均已填写完整，THE Monitor_App SHALL 不展示大模型接口未配置完整的提示。
10. IF LLM_Client 调用返回错误、连接失败，或返回内容不包含 SOX 指数、北向资金、A50 期货中的任一可识别外盘数据项，THEN THE Monitor_App SHALL 展示指示"外盘/新闻数据获取失败"的提示，并原样展示模型的原始返回内容供用户核实，同时保留界面上已有的其他数据不变。
11. IF LLM_Client 本次获取（含工具调用循环）超过 30 秒仍未返回最终研判，THEN THE Monitor_App SHALL 终止本次请求并展示指示请求超时的提示，且不影响界面上已有的其他数据。
12. THE LLM_Client SHALL 自身不将模型返回的内容或工具返回的内容写入本地持仓记录或止损计算，仅将其作为不确定信息在界面展示。

### Requirement 8: 交易记录

**User Story:** 作为用户，我想在应用内记录做 T、止损等实际操作，以便日后复盘。

#### Acceptance Criteria

1. WHEN 用户在界面提交一条操作记录（操作类型取值为 做T买入、做T卖出、止损、减仓之一，且成交价格为大于 0 的数值），THE Trade_Logger SHALL 调用 `qstock` 决策日志能力将该记录追加保存到本地日志文件，并在 2 秒内展示保存成功的确认提示。
2. IF 用户提交的操作记录缺少操作类型或缺少成交价格，THEN THE Trade_Logger SHALL 展示输入错误提示并拒绝保存该记录，且不写入本地日志文件。
3. IF 用户提交的操作记录的操作类型不在 做T买入、做T卖出、止损、减仓之内，或成交价格不是大于 0 的数值，THEN THE Trade_Logger SHALL 展示输入错误提示并拒绝保存该记录，且不写入本地日志文件。
4. WHEN 用户在界面查看交易记录，THE Trade_Logger SHALL 读取并按时间由早到晚展示 588170 已保存的最多 50 条操作记录，每条含时间戳、操作类型与成交价格。
5. WHEN 用户在界面查看交易记录且 588170 无已保存记录，THE Trade_Logger SHALL 展示暂无交易记录的空状态提示。

### Requirement 9: 免责声明常驻展示

**User Story:** 作为用户，我想在界面上始终看到风险提示，以便清楚应用输出不构成投资建议。

#### Acceptance Criteria

1. WHILE Monitor_App 处于运行状态，THE Monitor_App SHALL 在主界面底部常驻展示内容为"以上仅供参考，不构成投资建议，市场有风险，操作需自行判断"的提示条，且完整文本不被截断。
2. WHILE Monitor_App 处于运行状态，THE Monitor_App SHALL 在窗口尺寸变化、主界面内容滚动或界面视图切换时保持底部提示条持续可见，不被其他界面元素遮挡。
3. WHEN 用户首次启动 Monitor_App，THE Monitor_App SHALL 展示包含上述免责声明全文的确认提示，并要求用户执行一次显式确认操作后方可进入主界面。
4. IF 用户在首次启动的免责声明确认提示中未执行确认操作，THEN THE Monitor_App SHALL 保持在确认提示界面且不进入主界面。

### Requirement 10: 行情获取错误处理

**User Story:** 作为用户，我想让应用在行情或数据获取失败时给出明确提示，以便我知道数据链路
出现问题。

#### Acceptance Criteria

1. IF Variable_Calculator 因无法获取历史数据而返回错误，THEN THE Monitor_App SHALL 在界面显示错误提示信息，指示历史数据获取失败。
2. IF 一轮行情获取因网络异常失败，THEN THE Quote_Poller SHALL 在下一个轮询周期重新发起该轮行情获取，且不终止 Monitor_App 运行进程。
3. WHEN Variable_Calculator 返回的价格来源为 `kline_only`，THE Monitor_App SHALL 在界面显示提示信息，指示当前价可能非最新。
4. IF Variable_Calculator 因无法获取历史数据而返回错误，THEN THE Monitor_App SHALL 保留并继续展示上一轮成功计算的结果，且不清空或覆盖已有展示数据。
5. WHILE 连续多个轮询周期的行情获取因网络异常持续失败，THE Quote_Poller SHALL 在每个后续轮询周期继续重试直至获取成功，且保持 Monitor_App 运行不终止。
6. WHEN Variable_Calculator 返回的价格来源为 `kline_only`，THE Monitor_App SHALL 提示用户在做出决策前核实实时行情。
