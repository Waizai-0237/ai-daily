# 项目状态记录

## 当前状态
- 版本：V1.6（2026-10-04）
- 阶段：AI 行业日报稳定运营，开始搭建周报
- 下一步：完成周报五步改造，测试周一自动生成

## 外部依赖
- DeepSeek API Key（GitHub Secrets: LLM_API_KEY）
- QQ 邮箱授权码（GitHub Secrets: QQ_EMAIL_AUTH_CODE）
- 收件邮箱（Variables: MAIL_TO = 1051141130@qq.com）

## 关键决策
- 用 Python 规则聚类而不是纯 AI 聚类（省钱 + 可控）
- 用 QQ SMTP 而不是 Resend/Brevo（海外 SaaS 封锁 GitHub IP）
- 归档 + 周报用 JSON 数据源，不解析 HTML（结构化更可靠）

## 已知问题
- 国内 RSS 源（36氪、机器之心、新智元）不稳定，已下线
- Resend/Brevo 在 GitHub Actions 环境下 403，已弃用

## 待办
- [ ] 周报五步改造（进行中）
- [ ] 游戏行业日报（暂缓）
- [ ] 3 个月后考虑数据入库
