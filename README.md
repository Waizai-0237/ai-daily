# AI 行业每日简报 (ai-daily)

> 自动化 AI 行业情报系统 · 每日定时抓取 → AI 主编筛选 → 多源整合 → 自动部署

**在线访问**：[https://waizai-0237.github.io/ai-daily/](https://waizai-0237.github.io/ai-daily/)

---

## ✨ 功能特性

- **多源抓取**：聚合 10+ 中英文 AI 行业 RSS 源（TechCrunch、The Verge、量子位、Wired 等）
- **智能聚类去重**：基于规则的无监督聚类，自动合并同一事件的多方报道
- **AI 主编筛选**：调用 DeepSeek API 对候选新闻进行重要性评分、深度摘要、趋势点评
- **多来源溯源**：每条新闻关联 2-3 个真实来源，杜绝 AI 幻觉链接
- **日报 + 归档 + 周报**：每日日报、历史归档、最近 7 天回顾三种视图
- **关键词检索**：前端实时搜索标题、摘要、来源
- **邮件推送**：每日通过 QQ 邮箱 SMTP 推送简报摘要
- **可视化**：分类环形图 + 星级评分 + 彩色分类徽章

## 🛠️ 技术栈

| 层次 | 技术 |
|---|---|
| 数据抓取 | Python + feedparser |
| 数据处理 | 规则聚类 + 关键词提取 |
| AI 分析 | DeepSeek API (deepseek-chat) |
| 前端渲染 | 原生 HTML + CSS + JavaScript |
| 自动化 | GitHub Actions (定时 + 手动触发) |
| 部署 | GitHub Pages |
| 邮件 | QQ 邮箱 SMTP |

## 📂 项目结构
ai-daily/
├── config/feeds.yaml # RSS 源配置
├── scripts/build.py # 主程序
├── docs/ # 生成的 HTML（GitHub Pages 根目录）
│ ├── index.html # 首页（今日日报）
│ ├── archive/ # 历史归档
│ └── weekly/ # 周报
├── .github/workflows/ # GitHub Actions 配置
└── CHANGELOG.md # 版本记录


## 🚀 版本演进

见 [CHANGELOG.md](CHANGELOG.md)。当前稳定版本：**V1.6**

## 📄 License

MIT
