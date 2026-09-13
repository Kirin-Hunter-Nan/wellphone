# WellPhone 上海出差完整测试包

此包以 **2026-09-13（周日，Asia/Shanghai）** 为测试基准，“下周”固定指 **2026-09-16 至 2026-09-18**。所有姓名、订单号、票号、价格、联系人、商户与邮箱均为模拟数据；机场、酒店及会议地点使用真实存在的公共地点，方便地图核对。

## 包内内容

- `documents/`：去程机票、酒店确认、固定会议确认、返程机票，共 4 份带文字层 PDF。
- `gmail/`：6 封 RFC 822 `.eml` 邮件，覆盖航班、酒店、3 场会议与临时改期；附件包含 PDF/ICS。
- `calendar/`：4 个会议版本及 1 个需预先导入的既有冲突事件。
- `photos/`：出租车票据、餐饮票据及一张内容相同但图像文件不同的重复出租车票据。
- `TEST-PROMPT.txt`：完整自然语言测试任务。
- `EXPECTED-RESULTS.md`：解析、冲突、修订、去重和费用汇总的期望结果。

## 推荐准备顺序

1. 将 `calendar/99-existing-calendar-conflict.ics` 单独导入测试机的系统日历。
2. 用 Apple Mail 打开 `gmail/*.eml`，再转发到用于 WellPhone OAuth 测试的 Gmail 账号。首次测试可暂不发送 `06-partner-workshop-reschedule-notice.eml`。
3. 把 `photos/*.png` 保存到测试机相册。三张均应保留，用于验证重复票据去重。
4. 在 WellPhone 输入 `TEST-PROMPT.txt` 中的完整任务。
5. 首次规划完成后，再把 `06-partner-workshop-reschedule-notice.eml` 发到 Gmail，然后重新执行/修订任务。

## 搜索建议

自然输入使用“下周上海出差”即可；Agent 可在内部将日期换算为 2026-09-14 至 2026-09-20，并组合“上海、航班、酒店、会议、改期”等词进行 Gmail 检索。测试输入本身不要求用户手写 Gmail 的 `after:` / `before:` 搜索语法。

## 隐私和安全

本包不含真实个人订单或联系方式，`.invalid` 邮箱无法投递。所有 PDF 和图片都带有测试声明，不可用于真实出行、入住或报销。
