# 临空市项目须知（重要！每次改动前先读！）

## ⚠️ 血泪教训（勿再犯）

1. **MCP 必须手写协议端点，绝不使用 mcp / fastmcp 包！**
   - 用 `@app.api_route("/mcp", methods=["GET", "POST"])` 手写 JSON-RPC
   - mcp 官方新版有 task group bug；fastmcp 有路径/挂载坑；旧 mcp 有协议版本坑
   - 手写方式（参考 main.py 末尾 mcp_endpoint）：GET 返回初始化信息，POST 处理 initialize/tools/list/tools/call

2. **requirements.txt 只装：fastapi / uvicorn / pydantic**（不要加 mcp、fastmcp！）

3. **地图底图是静态文件**：图片放项目 `images/` 文件夹，部署时记得一起上传
   （main.py 已有 `app.mount("/images", StaticFiles(...))`）

4. **数据兼容**：data.json 加载后会自动 sanitize_data() 规整旧数据，别删

5. **部署后确认**：
   - 地址栏访问 `https://域名/mcp` 应显示 JSON-RPC 内容
   - 网页 `/api/health` 正常

## ✅ 当前版本（v46.8，黄金版）
- 手写 MCP（4 工具：group_send / group_query / group_write / group_access）
- 功能：多房间 / 地图 / 建筑 / 便签日记剧情 / 权限 / 经济工作 / 短信 / 铃铛 / 备份
- 部署：Zeabur（linkong.zeabur.app）

## 🔧 改动规范
- 只在黄金版基础上做小改动，不推翻重写
- 改动后测试：网页 + rikkahub MCP 都要测
- 重要改动前先备份 main.py 到本地
