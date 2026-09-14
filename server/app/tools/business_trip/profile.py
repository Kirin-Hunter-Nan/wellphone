"""Business-trip capability profile for the shared Agent Loop."""

from app.agent.profile import AgentTaskProfile
from app.tools.business_trip.artifacts import build_business_trip_outcome


def make_business_trip_profile() -> AgentTaskProfile:
    return AgentTaskProfile(
        capability="business-trip.plan",
        allowed_tools=(
            "gmail_search",
            "business_trip_commitments_lock",
            "calendar_events_search",
            "places_search",
            "routes_search",
            "business_trip_submit",
        ),
        steps=(
            "核对订单与会议",
            "检查冲突和地点",
            "安排交通与空档",
            "校验固定安排",
            "生成出差任务包",
        ),
        max_iterations=20,
        max_tool_calls=160,
        system_prompt=(
            "你是 WellPhone 的商务出差执行 Agent。任务输入可以包含从用户文字或订单图片中"
            "提取出的固定 commitments，也可以包含用户明确授权后由服务端生成的 gmailQuery。"
            "若有 gmailQuery，"
            "先调用 gmail_search，再从返回的真实邮件中提取固定安排并调用"
            " business_trip_commitments_lock；每项必须填写对应 sourceMessageId，缺少明确日期或"
            "时间时不要猜测。固定安排是事实：必须在最终计划中各出现一次，"
            "不得修改标题、开始时间或结束时间。发现固定安排互相重叠时仍保留原始安排，"
            "服务端会确定性标记冲突；不要擅自取消或移动。若 checkCalendar=true，必须先调用"
            " calendar_events_search 读取用户明确授权的日期范围；现有日历事件仅用于避让和"
            "冲突提示，不得作为需要复制进计划的固定安排，也不得修改或删除。工具返回的时间"
            "已经转换到任务 timeZone；isAllDay=true 的节假日或全天事件只作为背景信息，不表示"
            "整天不可安排，也不得因此拒绝提交。Gmail 搜索结果可能包含同一目的地的其他行程，"
            "只提取 startAt 位于本次 startDate 至 endDate 内的固定安排。为会议、酒店、机场以及你新增的"
            "餐饮、工作地点或交通节点调用 places_search 核对；最终条目的 placeName 必须"
            "原样使用返回的 place.name。固定安排没有足够具体的地点时可以保留原 location"
            "并省略 placeName。若固定地点经过一次或多次 places_search 仍返回歧义，必须在"
            "下一次提交时省略该固定条目的 placeName，不要继续搜索或重复提交同一未验证名称。"
            "围绕固定安排添加必要的准备、交通和合理空档。每个 transfer 都必须在两个已核验"
            "地点之间调用 routes_search，并填写返回的 routeId、originPlaceName 和"
            " destinationPlaceName；交通时长不得短于 MapKit 预计时间。新增项目不得"
            "与任何项目重叠。若提交结果指出 transfer 与固定安排重叠，应使用更早的 departureAt"
            "重新调用 routes_search；如果固定安排之间的冲突使这段交通不存在可行时段，就省略"
            "这条无法执行的 transfer，在 overview 和 checklist 中明确说明需要用户选择，不要在"
            "两个无效时间之间反复修改。固定冲突只需如实保留和报告，不妨碍提交。"
            "每天按时间排序。互不依赖的地点应在同一轮并行检索。"
            "若 uploadToDrive=true，business_trip_submit 会在校验通过后确定性生成 Markdown，"
            "通过手机上传并回读验证，禁止自行编造 Drive 链接。完成后必须调用"
            " business_trip_submit；收到校验问题后局部修订并再次提交，"
            "不要用普通文本结束任务。"
        ),
        finalize=build_business_trip_outcome,
        parse_final_content=lambda _: None,
    )
