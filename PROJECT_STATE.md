# 项目状态记录

## 当前状态
- 版本：V1.7（2026-10-08）
- 2026-10-08：完成周报功能五步改造
  - 新增 data/daily/ 目录，每日保存 summary_data.json
  - 新增 PROMPT_WEEKLY、summarize_weekly、render_weekly_html、render_weekly_summary
  - 周一自动触发周报生成（10月12日首次触发，覆盖4天数据）
  - 10月19日起周报为完整7天

## 外部依赖
- DeepSeek API Key（GitHub Secrets: LLM_API_KEY）
- QQ 邮箱授权码（GitHub Secrets: QQ_EMAIL_AUTH_CODE）
- 收件邮箱（Variables: MAIL_TO = 1051414186@qq.com）

## 关键决策
- 用 Python 规则聚类而不是纯 AI 聚类（省钱 + 可控）
- 用 QQ SMTP 而不是 Resend/Brevo（海外 SaaS 封锁 GitHub IP）
- 归档 + 周报用 JSON 数据源，不解析 HTML（结构化更可靠）

## 已知问题
- 国内 RSS 源（36氪、机器之心、新智元）不稳定，已下线
- Resend/Brevo 在 GitHub Actions 环境下 403，已弃用

## 待办
- [ ] 游戏行业日报（暂缓）
- [ ] 3 个月后考虑数据入库

## 下周待验证
- [ ] 10月12日：第一份周报是否正常生成
- [ ] W41 周报的排版、内容质量是否达标
