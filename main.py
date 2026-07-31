from flask import Flask, request, jsonify
import datetime

app = Flask(__name__)

# ============ 群聊消息存储 ============
messages = []

@app.route("/mcp", methods=["GET", "POST"])
def mcp():
    if request.method == "GET":
        return jsonify({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "1.0.0"}
            }
        })
    
    data = request.get_json(force=True)
    method = data.get("method", "")
    params = data.get("params", {})
    request_id = data.get("id", 1)
    
    # ===== 初始化握手（RikkaHub 连接时的第一步）=====
    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "1.0.0"}
            }
        })
    
    # ===== 通知类消息（如 notifications/initialized，不需要回复）=====
    if method.startswith("notifications/"):
        return ("", 202)
    
    # ===== Ping 心跳检测 =====
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
                        "description": "【群聊】发送一条消息到群聊",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者名字"},
                                "content": {"type": "string", "description": "消息内容"},
                                "role": {"type": "string", "description": "角色: user或assistant", "enum": ["user", "assistant"]}
                            },
                            "required": ["sender", "content"]
                        }
                    },
                    {
                        "name": "group_get_messages",
                        "description": "【群聊】获取最近消息",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "count": {"type": "number", "description": "获取最近多少条，默认20"}
                            }
                        }
                    },
                    {
                        "name": "group_get_members",
                        "description": "【群聊】获取成员列表",
                        "inputSchema": {"type": "object", "properties": {}}
                    }
                ]
            }
        })
    
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        
        if tool_name == "group_send_message":
            sender = tool_args.get("sender", "匿名")
            content = tool_args.get("content", "")
            role = tool_args.get("role", "user")
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            messages.append({"sender": sender, "content": content, "role": role, "time": timestamp})
            if len(messages) > 200:
                messages.pop(0)
            return jsonify({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": f"✅ {sender} 发送成功"}]}
            })
        
        elif tool_name == "group_get_messages":
            count = tool_args.get("count", 20)
            recent = messages[-count:] if count < len(messages) else messages
            if not recent:
                return jsonify({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "📭 暂无消息"}]}})
            result = "📋 群聊消息记录\n" + "─" * 30 + "\n"
            for msg in recent:
                emoji = "🤖" if msg["role"] == "assistant" else "👤"
                result += f"{emoji} {msg['sender']} ({msg['time']}):\n  {msg['content']}\n\n"
            return jsonify({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result}]}})
        
        elif tool_name == "group_get_members":
            return jsonify({
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": "👥 群聊成员\n" + "─" * 20 + "\n发过消息的人都会显示在这里"}]}
            })
    
    return jsonify({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
