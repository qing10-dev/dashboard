# 急跌候选池 · 云端自动更新仪表盘

这套包部署到 GitHub 后，**电脑关机也能自动更新**：
GitHub 的免费服务器在每个交易日 15:35（北京时间）自动抓最新行情 → 重算信号 → 更新看板网页 → 通过 GitHub Pages 对外发布。

## 一次性部署步骤（约10分钟）

1. **注册/登录 GitHub**（https://github.com）——注册需要你自己的邮箱收验证码，这一步只能你来做。
2. 右上角 `+` → `New repository` → 名字填 `dashboard`，选 **Public**（Pages 免费版要求公开仓库），不要勾选任何初始化选项 → Create。
3. 把本目录所有文件（含 `.github` 隐藏文件夹）上传到仓库。两种方式任选：
   - **网页上传**：仓库页点 `uploading an existing file`，把整个文件夹拖进去（kline_all.csv 51MB 也能传）。注意网页上传拖不进隐藏文件夹 `.github`，需要在本机执行：
     ```bash
     cd cloud_kit
     git init && git add -A && git commit -m "init"
     git branch -M main
     git remote add origin https://github.com/你的用户名/dashboard.git
     git push -u origin main
     ```
4. 开启 Pages：仓库 `Settings` → `Pages` → Source 选 `Deploy from a branch`，Branch 选 `main` / `(root)` → Save。
5. 等 1-2 分钟，访问 `https://你的用户名.github.io/dashboard/` 就是你的在线仪表盘。
6. 之后的每个交易日 15:35，GitHub 自动跑更新并刷新这个网址，你什么都不用做。

## 验证自动更新是否在工作

仓库页 → `Actions` 标签，应能看到 `daily-dashboard` 的运行记录（也可以手动点 `Run workflow` 测一次）。

## 本目录内容

| 文件 | 作用 |
|---|---|
| `daily_tracker.py` | 看板生成器（已改造为云端自包含版） |
| `kline_all.csv` | 14个月历史K线底仓数据（首次上传后由脚本自动增量维护） |
| `universe.csv` | 股票池（3055只主板票） |
| `.github/workflows/update.yml` | 定时任务定义（交易日15:35自动运行） |
| `requirements.txt` | Python 依赖 |

## 备注

- GitHub Actions 对免费账户每月有 2000 分钟额度，本任务每天跑约 5 分钟，绰绰有余。
- 如果腾讯行情接口偶尔抽风导致某天更新失败，Actions 会显示红色失败记录，数据不会损坏（下次自动重试）。
- 本仪表盘仅供研究参考，不构成个人投资建议。
