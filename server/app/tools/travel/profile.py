"""Travel capability profile for the shared Agent loop."""

from app.agent.profile import AgentTaskProfile
from app.tools.travel.artifacts import build_travel_outcome


def make_travel_profile() -> AgentTaskProfile:
    return AgentTaskProfile(
        capability="travel.plan",
        allowed_tools=("places_search", "itinerary_submit"),
        steps=("理解旅行目标", "自主检索与规划", "校验并修订", "生成最终行程"),
        max_iterations=12,
        max_tool_calls=112,
        system_prompt=(
            "你是 WellPhone 的旅行规划 Agent，运行在一个有预算上限的工具循环中。"
            "理解用户日期、目的地、同行者、节奏和偏好后，自主决定搜索哪些地点。"
            "每一个进入行程的具体地点都必须先调用 places_search 核对。"
            "互不依赖的地点应在同一轮并行发起多个 places_search，避免无意义的逐个等待。"
            "不要虚构营业时间、票价或地图核对结果。按地理邻近性组织每天的安排，"
            "为移动和休息保留合理时间。完成后必须调用 itinerary_submit；如果它返回"
            "校验问题，依据 Observation 局部修订并再次提交。不要直接用普通文本结束任务。"
        ),
        finalize=build_travel_outcome,
        parse_final_content=lambda _: None,
    )
