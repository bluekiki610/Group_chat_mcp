from flask import Flask, request, jsonify
import datetime
import os

app = Flask(__name__)

# ============ 群聊消息存储（内存，重启清空）============
messages = []
avatars = {}
online = {}  # 在线成员: name -> 最后心跳时间


def save_message(sender, content, role):
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
    return msg


@app.route("/")
def index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "index.html 不存在", 500


@app.route("/api/messages", methods=["GET", "POST"])
def api_messages():
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
    msg = save_message(sender, content, role)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/avatar", methods=["GET", "POST"])
def api_avatar():
    """获取/保存头像（同步给所有人）"""
    if request.method == "GET":
        return jsonify({"avatars": avatars})
    data = request.get_json(force=True)
    name = data.get("name", "")
    image = data.get("image", "")
    if not name or not image:
        return jsonify({"error": "参数错误"}), 400
    avatars[name] = image
    if len(avatars) > 50:
        avatars.popitem()
    return jsonify({"ok": True, "name": name})


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    """网页在线心跳（谁打开网页谁在线）"""
    data = request.get_json(force=True)
    name = data.get("name", "")
    if name:
        online[name] = datetime.datetime.now()
    return jsonify({"ok": True})


@app.route("/api/online", methods=["GET"])
def api_online():
    """获取在线成员（30秒内有心跳）"""
    now = datetime.datetime.now()
    active = [n for n, t in online.items() if (now - t).total_seconds() < 30]
    return jsonify({"online": active})


@app.route("/api/restore", methods=["POST"])
def api_restore():
    """恢复历史消息（本地缓存回填，不触发AI）"""
    data = request.get_json(force=True)
    msgs = data.get("messages", [])
    restored = 0
    for m in msgs:
        if not m.get("content"):
            continue
        save_message(m.get("sender", "匿名"), m["content"], m.get("role", "user"))
        restored += 1
    return jsonify({"ok": True, "restored": restored})


@app.route("/api/remove_member", methods=["POST"])
def api_remove_member():
    """删除某个成员的所有消息和头像"""
    data = request.get_json(force=True)
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "参数错误"}), 400
    global messages
    before = len(messages)
    messages = [m for m in messages if m["sender"] != name]
    removed = before - len(messages)
    if name in avatars:
        del avatars[name]
    if name in online:
        del online[name]
    return jsonify({"ok": True, "removed": removed})


@app.route("/mcp", methods=["GET", "POST"])
def mcp():
    """MCP 协议入口（给 RikkaHub 的 AI 用）"""
    if request.method == "GET":
        return jsonify({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "3.0.0"},
            },
        })

    data = request.get_json(force=True)
    method = data.get("method", "")
    params = data.get("params", {})
    request_id = data.get("id", 1)

    if method == "initialize":
        return jsonify({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "3.0.0"},
            },
        })

    if method.startswith("notifications/"):
        return ("", 202)

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
                        "description": "【群聊】以群成员身份发送一条消息到群聊。sender填你的群昵称，role填assistant。",
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
                        "description": "【群聊】查看群聊最近的消息记录。",
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
            save_message(sender, content, role)
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
                "result": {"content": [{"type": "text", "text": "👥 群成员：亦言、黎深、小旭、秦彻"}]},
            })

    return jsonify({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
