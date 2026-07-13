# -*- coding: utf-8 -*-
"""LLM_Client：外盘/新闻大模型接入（直接问答，不做联网工具调用）。

背景与设计取舍：
  - 曾尝试两种联网方案：① 本地 WebSearch/WebFetch 工具调用循环（抓取 DuckDuckGo 等
    搜索引擎），实测搜索引擎的反爬/地区拦截导致抓取长期失败或返回空结果；
    ② 模型服务端自带的 `web_search` 工具（如 MiMo），需账号额外开通付费插件，
    且不同网关（按量 API / Token Plan）的开通状态不一致，稳定性不可控。
  - 为避免应用长期卡在不可用的联网通道上、拖慢获取速度，现改为**直接问答**：
    不声明任何工具，请求模型基于自身知识直接给出研判，并在结果中明确标注
    "数据可能非最新、仅供参考"，交由用户自行判断与核实（需求 7.6/7.10/7.12）。
  - 若未来该网络环境下有可用的联网方式，可重新引入工具调用；接口
    （``fetch_market_briefing`` / ``LLMResult`` 字段）保持不变，UI 侧无需改动。

隔离约束（需求 7.12）：本模块**不导入** ``position_store``、**不写入**任何持仓记录或止损
计算，模型返回内容仅作为不确定信息经 ``LLMResult`` 向上层传递、在界面展示。
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Callable, Optional

import requests

from .models import LLMConfig, LLMResult

# --- 常量 -----------------------------------------------------------------

# 单次外盘/新闻获取的整体响应等待上限（秒）。
DEFAULT_OVERALL_TIMEOUT = 30.0
# 单次 HTTP 调用超时（秒）。
DEFAULT_HTTP_TIMEOUT = 25.0

# 需求 7.10：可识别外盘数据项的关键词分组（任一命中即视为"包含该项"）。
# 三组分别对应 SOX 指数、北向资金、A50 期货。
_BRIEFING_KEYWORD_GROUPS = (
    ("SOX", "费城半导体", "费半", "半导体指数"),   # SOX 指数
    ("北向", "北上资金", "陆股通"),                # 北向资金
    ("A50",),                                     # A50 期货
)

# 直接问答提示词：不提任何联网工具，要求模型基于自身知识直接给出研判，
# 不要输出"我将搜索/请稍候"之类占位语；并明确标注数据可能非最新。
_SYSTEM_PROMPT = (
    "你是一名 A 股半导体板块的盘前研判助手。当前无法联网获取实时数据，"
    "请直接基于你已有的知识作答，不要声称你在搜索、也不要输出"
    "“我将为您搜索/请稍候”之类的占位内容。请直接、简明地给出研判，"
    "并在开头说明数据可能非最新、仅供参考。"
)
_USER_PROMPT = (
    "请就今日盘前外盘与半导体新闻环境，直接给出你所知的最接近的研判，"
    "覆盖以下四项并给出具体判断：1) SOX 费城半导体指数；2) 北向资金（陆股通）流向；"
    "3) A50 期货走势；4) 近期半导体行业重要新闻。请勿输出占位语，直接给结论。"
)


# --- 异常 -----------------------------------------------------------------


class LLMHttpError(Exception):
    """大模型接口调用返回非成功状态或连接失败时抛出（需求 7.10）。"""


# --- LLM_Client ------------------------------------------------------------


class LLMClient:
    """按 OpenAI 格式调用大模型获取外盘/新闻研判（直接问答，不做工具调用）。

    结果统一封装为 ``LLMResult``；本类不做任何持仓/止损写入（需求 7.12）。
    """

    def __init__(
        self,
        get_config: Callable[[], LLMConfig],
        *,
        overall_timeout: float = DEFAULT_OVERALL_TIMEOUT,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        **_ignored,
    ) -> None:
        """初始化。

        Args:
            get_config: 读取当前大模型三项配置的回调（通常为
                ``SettingsStore.get_llm_config``）。
            overall_timeout: 单次获取整体超时秒数。
            http_timeout: 单次 HTTP 调用超时秒数。
            **_ignored: 兼容旧版构造参数（如 ``web_tools``/``max_tool_rounds``），
                接受但不再使用，避免调用方需要同步改动。
        """
        self._get_config = get_config
        self.overall_timeout = overall_timeout
        self._http_timeout = http_timeout

    # --- 门控与失败判定（纯逻辑辅助） -------------------------------------

    @staticmethod
    def config_complete(cfg: LLMConfig) -> bool:
        """大模型三项配置是否均已填写完整（需求 7.8/7.9 门控）。

        三项（接口地址/API 密钥/模型名称）全部非空返回 True；存在任一为空返回 False。
        """
        return bool(cfg) and cfg.is_complete()

    @staticmethod
    def is_briefing_failure(text: Optional[str]) -> bool:
        """外盘研判失败判定（需求 7.10）。

        当返回内容不包含 SOX 指数、北向资金、A50 期货中的**任一**可识别外盘数据项时
        （即三项一个都没命中），判定为失败返回 True；否则返回 False。空内容视为失败。
        """
        if not text or not text.strip():
            return True
        lowered = text.lower()
        for group in _BRIEFING_KEYWORD_GROUPS:
            for keyword in group:
                if keyword.lower() in lowered:
                    # 命中任一分组即说明包含可识别外盘数据项，非失败。
                    return False
        return True

    # --- 对外主流程 -------------------------------------------------------

    def fetch_market_briefing(self) -> LLMResult:
        """获取外盘与新闻研判（直接问答，整体超时兜底）。

        在独立工作线程中执行单次问答，并对整体施加 ``overall_timeout`` 上限；
        超时返回 ``status="timeout"``。配置不完整时直接返回 ``status="error"``
        （UI 侧还应按 ``config_complete`` 提前门控，见需求 7.8）。

        Returns:
            封装本次获取结果的 ``LLMResult``。``used_tools`` 恒为 ``False``、
            ``tool_rounds`` 恒为 0（不再进行联网工具调用，见模块顶部说明）。
        """
        fetched_at = datetime.now()
        cfg = self._get_config()

        # 需求 7.8：配置任一为空则不发起调用。
        if not self.config_complete(cfg):
            return LLMResult(
                status="error",
                briefing_text=None,
                raw_response="大模型接口未配置完整，请先填写接口地址、API 密钥与模型名称。",
                used_tools=False,
                tool_rounds=0,
                fetched_at=fetched_at,
            )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._run_briefing, cfg, fetched_at)
            return future.result(timeout=self.overall_timeout)
        except concurrent.futures.TimeoutError:
            # 需求 7.11：整体超过上限未返回最终研判，终止本次请求并提示超时。
            return LLMResult(
                status="timeout",
                briefing_text=None,
                raw_response=None,
                used_tools=False,
                tool_rounds=0,
                fetched_at=fetched_at,
            )
        finally:
            # 不等待遗留任务，避免超时后仍被阻塞。
            executor.shutdown(wait=False)

    def start_async(self, result_queue) -> "concurrent.futures.Future":
        """在后台线程运行 ``fetch_market_briefing`` 并把 ``LLMResult`` 放入队列。

        供 Monitor_App 以"后台线程 + 队列回主线程"的方式驱动外盘/新闻获取，与行情
        轮询互不阻塞（见设计线程模型）。返回 ``Future`` 便于调用方按需等待/取消。
        """
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _worker() -> None:
            result = self.fetch_market_briefing()
            result_queue.put(result)

        future = executor.submit(_worker)
        executor.shutdown(wait=False)
        return future

    # --- 内部实现 -----------------------------------------------------------

    def _run_briefing(self, cfg: LLMConfig, fetched_at: datetime) -> LLMResult:
        """执行一次直接问答，返回 ``LLMResult``（在工作线程内运行）。"""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PROMPT},
        ]
        try:
            message = self._chat(messages, cfg)
        except (LLMHttpError, requests.RequestException) as exc:
            return self._error_result(str(exc), fetched_at=fetched_at)

        text = message.get("content") or ""
        return LLMResult(
            status="fallback_no_tools",
            briefing_text=text,
            raw_response=text,
            used_tools=False,
            tool_rounds=0,
            fetched_at=fetched_at,
        )

    def _chat(self, messages: list, cfg: LLMConfig) -> dict:
        """单次 OpenAI ``/chat/completions`` 调用（不声明工具），返回助手消息字典。

        Args:
            messages: 对话消息列表。
            cfg: 当前大模型配置。

        Returns:
            解析出的助手消息字典（含 ``content``）。

        Raises:
            LLMHttpError: 非成功响应或响应体解析失败（需求 7.10）。
        """
        url = self._chat_url(cfg.base_url)
        payload = {"model": cfg.model, "messages": messages}
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=self._http_timeout)

        if resp.status_code != 200:
            raise LLMHttpError(f"接口返回错误（HTTP {resp.status_code}）：{self._safe_body(resp)}")

        try:
            data = resp.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMHttpError(f"接口响应解析失败：{exc}；原始返回：{self._safe_body(resp)}")
        if not isinstance(message, dict):
            raise LLMHttpError("接口响应缺少有效的 message 字段。")
        return message

    @staticmethod
    def _chat_url(base_url: str) -> str:
        """由配置的接口地址拼出 ``/chat/completions`` 端点，兼容是否带尾部路径。"""
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @staticmethod
    def _safe_body(resp) -> str:
        """安全读取响应体文本，异常时返回占位说明。"""
        try:
            return resp.text
        except Exception:  # noqa: BLE001
            return "<无法读取响应体>"

    # --- 结果封装 ---------------------------------------------------------

    @staticmethod
    def _error_result(message: str, *, fetched_at: datetime) -> LLMResult:
        """构造错误结果：原样携带错误/原始返回供 UI 展示（需求 7.10）。"""
        return LLMResult(
            status="error",
            briefing_text=None,
            raw_response=message,
            used_tools=False,
            tool_rounds=0,
            fetched_at=fetched_at,
        )
