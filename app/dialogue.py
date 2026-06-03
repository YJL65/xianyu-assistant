from __future__ import annotations

import re

from .llm import LlmError, OpenAICompatibleClient, fallback_summary
from .models import AssistantDecision, Listing, NeedState


FIELD_LABELS = {
    "goal": "主要想解决什么问题或达到什么效果",
    "deliverable": "最终需要交付什么内容",
    "deadline": "期望什么时候完成",
    "budget": "大概预算范围",
    "delivery_method": "线上交付还是本地/到店沟通",
    "materials": "目前已有的资料、链接、图片或账号信息是否齐全",
}


class IntakeAssistant:
    def __init__(self, llm: OpenAICompatibleClient) -> None:
        self.llm = llm

    def handle(
        self,
        listing: Listing,
        history: list[tuple[str, str]],
        state: NeedState,
    ) -> AssistantDecision:
        latest_buyer_text = next(
            (text for role, text in reversed(history) if role == "buyer"),
            "",
        )

        availability_reply = availability_question_reply(latest_buyer_text)
        if availability_reply:
            return AssistantDecision(reply=availability_reply, should_notify=False)

        updates = fallback_extract(latest_buyer_text, listing)
        try:
            updates.update(self.llm.extract_need_state(listing, history, state))
        except LlmError:
            pass
        state.merge(updates)

        missing = state.missing_fields()
        if not missing:
            state.completed = True
            summary = self._summary(listing, history, state)
            return AssistantDecision(
                reply="好的，我这边先帮你把需求记录下来，稍后会联系你确认细节。",
                should_notify=not state.notified,
                summary=summary,
            )

        question = self._question(listing, history, state, missing)
        return AssistantDecision(reply=question, should_notify=False)

    def _question(
        self,
        listing: Listing,
        history: list[tuple[str, str]],
        state: NeedState,
        missing: list[str],
    ) -> str:
        try:
            drafted = self.llm.draft_question(listing, history, state, missing)
        except LlmError:
            drafted = ""
        if drafted:
            return drafted

        first = FIELD_LABELS[missing[0]]
        if len(missing) > 1:
            second = FIELD_LABELS[missing[1]]
            return f"方便再说下{first}，以及{second}吗？我先帮你记录清楚。"
        return f"方便再说下{first}吗？我先帮你记录清楚。"

    def _summary(
        self,
        listing: Listing,
        history: list[tuple[str, str]],
        state: NeedState,
    ) -> str:
        try:
            return self.llm.summarize(listing, history, state)
        except LlmError:
            return fallback_summary(state)


def fallback_extract(text: str, listing: Listing) -> dict[str, str]:
    updates: dict[str, str] = {}
    compact = text.strip()
    if not compact:
        return updates

    if looks_like_goal(compact):
        updates["goal"] = compact

    if any(word in compact for word in ("做", "设计", "开发", "修", "写", "剪辑", "搭建", "优化", "配置")):
        updates.setdefault("deliverable", compact)

    deadline = extract_deadline(compact)
    if deadline:
        updates["deadline"] = deadline

    budget = extract_budget(compact)
    if budget:
        updates["budget"] = budget

    if any(word in compact for word in ("线上", "远程", "微信", "飞书", "腾讯会议", "网盘")):
        updates["delivery_method"] = compact
    elif any(word in compact for word in ("同城", "上门", "到店", "本地", "面谈")):
        updates["delivery_method"] = compact

    if any(word in compact for word in ("资料", "文档", "链接", "图片", "截图", "账号", "素材", "代码", "需求文档")):
        updates["materials"] = compact

    if listing.title and any(word in compact for word in ("这个", "你这个", "服务", "商品")):
        updates.setdefault("goal", f"咨询商品：{listing.title}")

    return updates


def availability_question_reply(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return ""
    patterns = (
        "还有吗",
        "还在吗",
        "在吗",
        "有吗",
        "宝贝还在",
        "东西还在",
        "服务还在",
    )
    if any(pattern in normalized for pattern in patterns):
        return "\u4f60\u6709\u4ec0\u4e48\u9700\u6c42\uff1f"
    return ""


def looks_like_goal(text: str) -> bool:
    return bool(
        len(text) >= 4
        and any(word in text for word in ("想", "需要", "咨询", "能不能", "可以", "帮我", "我有"))
    )


def extract_deadline(text: str) -> str:
    patterns = [
        r"(今天|明天|后天|本周|这周|周末|月底|下周|尽快|越快越好)",
        r"(\d{1,2}\s*[月/.-]\s*\d{1,2}\s*[日号]?)",
        r"(\d+\s*(天|小时|周)内)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def extract_budget(text: str) -> str:
    patterns = [
        r"(\d+(?:\.\d+)?\s*[kK千万]?\s*[-到~]\s*\d+(?:\.\d+)?\s*[kK千万]?\s*元?)",
        r"(预算\s*[：:]?\s*\d+(?:\.\d+)?\s*[kK千万]?\s*元?)",
        r"(\d+(?:\.\d+)?\s*[kK千万]?\s*元左右)",
        r"(预算\s*[：:]?\s*[一二两三四五六七八九十百千万]+\s*元?左右?)",
        r"([一二两三四五六七八九十百千万]+\s*元?左右)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""
