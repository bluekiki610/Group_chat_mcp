from flask import Flask, request, jsonify
import datetime
import os

app = Flask(__name__)

# ============ 群聊消息存储 ============
messages = []


@app.route("/")
def index():
    """群聊网页界面（微信风格）"""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html 不存在，请确认已上传该文件", 500


@app.route("/api/messages", methods=["GET", "POST"])
def api_messages():
    """网页前端用的消息 API"""
    if request.method == "GET":
        count = request.args.get("count", 50, type=int)
        recent = messages[-count:] if count < len(messages) else messages
        return jsonify({"messages": recent})

    data = request.get_json(force=True)
    sender = data.get("sender", "匿名")
    content = data.get("content", "")
    role = data.get("role", "user")
    if not content.strip():
        return jsonify({"error": "消息不能为空"}), 400
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = {
        "id": len(messages) + 1,
        "sender": sender,
        "content": content,
        "role": role,
        "time": timestamp,
    }
    messages.append(msg)
    if len(messages) > 500:
        messages.pop(0)
    return jsonify({"ok": True, "message": msg})


@app.route("/mcp", methods=["GET", "POST"])
def mcp():
    """MCP 协议入口（给 RikkaHub 的 AI 用）"""
    if request.method == "GET":
        return jsonify({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "2.0.0"},
            },
        })

    data = request.get_json(force=True)
    method = data.get("method", "")
    params = data.get("params", {})
    request_id = data.get("id", 1)

    # 初始化握手
    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "2.0.0"},
            },
        })

    # 通知类消息
    if method.startswith("notifications/"):
        return ("", 202)

    # Ping
    if method == "ping":
        return jsonify({"jsonrpc": "2.0", "id": request_id, "result": {}})

    if method == "tools/list":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "group_send_message",
                        "description": "【群聊】以群成员身份发送一条消息到群聊。你在群里发言必须用这个工具。sender填你的群昵称，role填assistant。真人（user）的消息由他们自己或他们的助手发送。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者群昵称"},
                                "content": {"type": "string", "description": "消息内容"},
                                "role": {"type": "string", "description": "user=真人, assistant=AI助手", "enum": ["user", "assistant"]},
                            },
                            "required": ["sender", "content"],
                        },
                    },
                    {
                        "name": "group_get_messages",
                        "description": "【群聊】查看群聊最近的消息记录。每次准备发言前，先调用这个工具看看群里最新的聊天内容，了解讨论进展，再决定说什么。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "count": {"type": "number", "description": "获取最近多少条，默认20"},
                            },
                        },
                    },
                    {
                        "name": "group_get_members",
                        "description": "【群聊】查看群聊成员列表。",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        })

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name == "group_send_message":
            sender = tool_args.get("sender", "匿名")
            content = tool_args.get("content", "")
            role = tool_args.get("role", "user")
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = {
                "id": len(messages) + 1,
                "sender": sender,
                "content": content,
                "role": role,
                "time": timestamp,
            }
            messages.append(msg)
            if len(messages) > 500:
                messages.pop(0)
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": f"✅ 已发送到群聊：{sender}：{content[:30]}"}]},
            })

        elif tool_name == "group_get_messages":
            count = tool_args.get("count", 20)
            recent = messages[-count:] if count < len(messages) else messages
            if not recent:
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "📭 群里暂时还没有消息"}]},
                })
            result = "📋 群聊消息记录\n" + "─" * 30 + "\n"
            for msg in recent:
                emoji = "🤖" if msg["role"] == "assistant" else "👤"
                result += f"{emoji} {msg['sender']} ({msg['time']}):\n  {msg['content']}\n\n"
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]},
            })

        elif tool_name == "group_get_members":
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "👥 群成员：张三、张三的助手、李四、李四的助手"}]},
            })

    return jsonify({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

