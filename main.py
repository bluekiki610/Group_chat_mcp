from flask import Flask, request, jsonify
import datetime
import os

app = Flask(__name__)

# ============ 群聊消息存储（内存，重启清空）============
messages = []
avatars = {}
online = {}  # 在线成员: name -> 最后心跳时间

# ============ 时间设置（按房间独立）============
# room -> {"mode": "real"|"fixed", "fixed_time": "19:00"}
time_settings = {}

BEIJING_OFFSET = datetime.timedelta(hours=8)


def get_time_setting(room="main"):
    return time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})


def now_str(room="main"):
    """生成消息时间（默认北京时间；每个房间可独立固定剧情时间）"""
    now = datetime.datetime.now(datetime.timezone.utc) + BEIJING_OFFSET
    s = get_time_setting(room)
    if s.get("mode") == "fixed":
        return now.strftime("%Y-%m-%d") + " " + s.get("fixed_time", "19:00")
    return now.strftime("%Y-%m-%d %H:%M:%S")


# ============ 房间系统（客厅 + 小房间）============
rooms = {}
rooms["main"] = {"password": None, "creator": "系统", "created": now_str("main")}


def check_room_access(room, password):
    """检查房间访问权限（客厅无需密码）"""
    if room in ("", "main"):
        return True
    info = rooms.get(room)
    if not info:
        return False
    if not info.get("password"):
        return True
    return (password or "") == info["password"]


def save_message(sender, content, role, room="main"):
    timestamp = now_str(room)
    msg = {
        "id": len(messages) + 1,
        "sender": sender,
        "content": content,
        "role": role,
        "time": timestamp,
        "room": room,
    }
    messages.append(msg)
    if len(messages) > 1000:
        messages.pop(0)
    return msg


def get_room_messages(room, count):
    room_msgs = [m for m in messages if m.get("room", "main") == room]
    return room_msgs[-count:] if count < len(room_msgs) else room_msgs


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
        room = request.args.get("room", "main")
        password = request.args.get("password", "")
        if not check_room_access(room, password):
            return jsonify({"error": "密码错误或房间不存在"}), 403
        count = request.args.get("count", 50, type=int)
        recent = get_room_messages(room, count)
        return jsonify({"messages": recent, "room": room})

    data = request.get_json(force=True)
    sender = data.get("sender", "匿名")
    content = data.get("content", "")
    role = data.get("role", "user")
    room = data.get("room", "main")
    password = data.get("password", "")
    if not check_room_access(room, password):
        return jsonify({"error": "密码错误或房间不存在"}), 403
    if not content.strip():
        return jsonify({"error": "消息不能为空"}), 400
    msg = save_message(sender, content, role, room)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/rooms", methods=["GET", "POST"])
def api_rooms():
    """房间列表 / 创建房间"""
    if request.method == "GET":
        lst = []
        for name, info in rooms.items():
            lst.append({
                "name": name,
                "has_password": bool(info.get("password")),
                "creator": info.get("creator", ""),
                "created": info.get("created", ""),
            })
        return jsonify({"rooms": lst})

    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    password = data.get("password", "").strip() or None
    creator = data.get("creator", "匿名")
    if not name:
        return jsonify({"error": "房间名不能为空"}), 400
    if name == "main":
        return jsonify({"error": "不能使用该房间名"}), 400
    if name in rooms:
        return jsonify({"error": "房间已存在"}), 400
    rooms[name] = {"password": password, "creator": creator, "created": now_str(name)}
    return jsonify({"ok": True, "room": {"name": name, "has_password": bool(password)}})


@app.route("/api/rooms/join", methods=["POST"])
def api_rooms_join():
    """验证密码并加入房间"""
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    password = data.get("password", "").strip() or None
    if name == "main":
        return jsonify({"ok": True, "room": {"name": "main", "has_password": False}})
    if name not in rooms:
        return jsonify({"error": "房间不存在"}), 404
    info = rooms[name]
    if info.get("password") and password != info["password"]:
        return jsonify({"error": "密码错误"}), 403
    return jsonify({"ok": True, "room": {"name": name, "has_password": bool(info.get("password"))}})


@app.route("/api/time_settings", methods=["GET", "POST"])
def api_time_settings():
    """获取/设置某个房间的时间模式（管理员用，按房间独立）"""
    if request.method == "GET":
        room = request.args.get("room", "main")
        return jsonify({"settings": get_time_setting(room), "room": room})
    data = request.get_json(force=True)
    room = data.get("room", "main")
    mode = data.get("mode", "real")
    if mode not in ("real", "fixed"):
        return jsonify({"error": "mode 参数错误"}), 400
    s = time_settings.setdefault(room, {"mode": "real", "fixed_time": "19:00"})
    s["mode"] = mode
    ft = data.get("fixed_time", "")
    if ft:
        s["fixed_time"] = ft
    return jsonify({"ok": True, "settings": s, "room": room})


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
    room = data.get("room", "main")
    password = data.get("password", "")
    if not check_room_access(room, password):
        return jsonify({"error": "无权限"}), 403
    restored = 0
    for m in msgs:
        if not m.get("content"):
            continue
        save_message(m.get("sender", "匿名"), m["content"], m.get("role", "user"), room)
        restored += 1
    return jsonify({"ok": True, "restored": restored})


@app.route("/api/remove_member", methods=["POST"])
def api_remove_member():
    """删除某个成员在本房间的所有消息和头像"""
    data = request.get_json(force=True)
    name = data.get("name", "")
    room = data.get("room", "main")
    password = data.get("password", "")
    if not name:
        return jsonify({"error": "参数错误"}), 400
    if not check_room_access(room, password):
        return jsonify({"error": "无权限"}), 403
    global messages
    before = len(messages)
    messages = [m for m in messages if not (m["sender"] == name and m.get("room", "main") == room)]
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
                "serverInfo": {"name": "GroupChat", "version": "4.0.0"},
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
                "serverInfo": {"name": "GroupChat", "version": "4.0.0"},
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
                        "description": "【群聊】以群成员身份发送一条消息到群聊。sender填你的群昵称，role填assistant。room填房间名（默认main=客厅），如果房间有密码需要填password。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者群昵称"},
                                "content": {"type": "string", "description": "消息内容"},
                                "role": {"type": "string", "description": "user=真人, assistant=AI助手", "enum": ["user", "assistant"]},
                                "room": {"type": "string", "description": "房间名，默认main（客厅）"},
                                "password": {"type": "string", "description": "房间密码（有密码的房间需要）"},
                            },
                            "required": ["sender", "content"],
                        },
                    },
                    {
                        "name": "group_get_messages",
                        "description": "【群聊】查看群聊最近的消息记录。room填房间名（默认main=客厅），有密码的房间需要填password。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "count": {"type": "number", "description": "获取最近多少条，默认20"},
                                "room": {"type": "string", "description": "房间名，默认main（客厅）"},
                                "password": {"type": "string", "description": "房间密码（有密码的房间需要）"},
                            },
                        },
                    },
                    {
                        "name": "group_get_rooms",
                        "description": "【群聊】查看所有房间列表。",
                        "inputSchema": {"type": "object", "properties": {}},
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
            room = tool_args.get("room", "main")
            password = tool_args.get("password", "")
            if not check_room_access(room, password):
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "❌ 房间不存在或密码错误：" + room}]},
                })
            save_message(sender, content, role, room)
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": f"✅ 已发送到群聊[{room}]：{sender}：{content[:30]}"}]},
            })

        elif tool_name == "group_get_messages":
            count = tool_args.get("count", 20)
            room = tool_args.get("room", "main")
            password = tool_args.get("password", "")
            if not check_room_access(room, password):
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "❌ 房间不存在或密码错误：" + room}]},
                })
            recent = get_room_messages(room, count)
            if not recent:
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "📭 这个房间暂时还没有消息"}]},
                })
            result = f"📋 群聊消息记录 [{room}]\n" + "─" * 30 + "\n"
            for msg in recent:
                emoji = "🤖" if msg["role"] == "assistant" else "👤"
                result += f"{emoji} {msg['sender']} ({msg['time']}):\n  {msg['content']}\n\n"
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]},
            })

        elif tool_name == "group_get_rooms":
            lst = []
            for name, info in rooms.items():
                lst.append(name + ("（🔒有密码）" if info.get("password") else "（公开）"))
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "🏠 房间列表：" + ("、".join(lst) if lst else "暂无")}]},
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
