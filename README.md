# 闲鱼瑞幸礼品卡 24小时云端监控

GitHub Actions 免费 24 小时监控，每 15 分钟扫描一次"瑞幸 礼品卡"，命中 ≤8 折通过 Server酱推送微信。

## 部署步骤（一次性，约10分钟）

1. **建仓库**：GitHub 上新建一个**私有**仓库（如 `xianyu-monitor`），把本目录所有文件推上去（注意 `.github` 是隐藏文件夹，需 `git add .github` 或 `git add -A`）。

2. **配置 Secrets**：仓库 → Settings → Secrets and variables → Actions → New repository secret
   - `SCT_SENDKEY`：你的 Server酱 SendKey（已在使用：SCT4335***）
   - `XY_COOKIE`：可选。不填则无登录态（行情仅粗筛参考）；填了可恢复登录态效果

3. **获取 Cookie（可选）**：本地浏览器登录 goofish.com → F12 → Network → 刷新 → 任一请求 → Request Headers → 复制 `Cookie:` 后面的整串，粘贴到 `XY_COOKIE`

4. **首次测试**：仓库 → Actions → Xianyu Luckin Card Monitor → Run workflow，看是否收到微信推送

5. **启用定时**：仓库 Actions 页面如果提示 scheduled workflows 已禁用，点 Enable 即可。

## 注意事项

- GitHub Actions 的 cron 实际执行会有 5~20 分钟排队延迟，属正常
- **免费额度**：公有仓库无限；私有仓库每月 2000 分钟（每轮约 2 分钟 ≈ 每月 5760 分钟，超出后**需将仓库改为 Public** 或降低频率）。建议跑通后改为 Public 仓库（脚本不含敏感信息，SendKey 在 Secrets 中）
- **Cookie 有效性**：闲鱼 Cookie 一般几天~几周过期；过期时微信会收到"Cookie已过期"提醒，重新导出更新 Secret 即可
- 无 Cookie 时搜索结果以推荐流为主，礼品卡命中少——命中即价值的粗筛定位
- GitHub 不允许同一仓库超过 60 天无活动后自动停用 cron，定期有 commit（每轮报告）不会停

## 文件说明

- `scan.py`：扫描+解析+推送脚本
- `.github/workflows/monitor.yml`：定时工作流
