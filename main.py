from flask import Flask, request, jsonify
import datetime
import os

app = Flask(__name__)

# ============ 群聊消息存储（内存，重启清空）============
messages = []
avatars = {}
online = {}  # 在线成员: name -> 最后心跳时间

# ============ 时间设置（按房间独立）============
time_settings = {}

BEIJING_OFFSET = datetime.timedelta(hours=8)

# ============ 当前活跃房间（真人所在，AI 可查询决定是否跟随）============
active_room = {"room": "main", "password": ""}


def norm_room(room):
    """房间名规范化（去空格）"""
    return (room or "").strip()


def get_time_setting(room="main"):
    return time_settings.get(norm_room(room), {"mode": "real", "fixed_time": "19:00"})


def now_str(room="main"):
    now = datetime.datetime.now(datetime.timezone.utc) + BEIJING_OFFSET
    s = get_time_setting(room)
    if s.get("mode") == "fixed":
        return now.strftime("%Y-%m-%d") + " " + s.get("fixed_time", "19:00")
    return now.strftime("%Y-%m-%d %H:%M:%S")


# ============ 房间系统（客厅 + 小房间）============
rooms = {}
rooms["main"] = {"password": None, "creator": "系统", "created": now_str("main")}


def check_room_access(room, password):
    room = norm_room(room)
    if room in ("", "main"):
        return True
    info = rooms.get(room)
    if not info:
        return False
    if not info.get("password"):
        return True
    return (password or "") == info["password"]


def save_message(sender, content, role, room="main"):
    room = norm_room(room)
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
    room = norm_room(room)
    room_msgs = [m for m in messages if m.get("room", "main") == room]
    return room_msgs[-count:] if count < len(room_msgs) else room_msgs


def room_label(room):
    room = norm_room(room)
    return "客厅" if room == "main" else room


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
        room = norm_room(request.args.get("room", ""))
        password = request.args.get("password", "")
        if not room:
            room = active_room["room"]
            if not password:
                password = active_room.get("password", "")
        if not check_room_access(room, password):
            return jsonify({"error": "密码错误或房间不存在"}), 403
        count = request.args.get("count", 50, type=int)
        recent = get_room_messages(room, count)
        return jsonify({"messages": recent, "room": room})

    data = request.get_json(force=True)
    sender = data.get("sender", "匿名")
    content = data.get("content", "")
    role = data.get("role", "user")
    room = norm_room(data.get("room", ""))
    password = data.get("password", "")
    if not room:
        room = active_room["room"]
        if not password:
            password = active_room.get("password", "")
    if not check_room_access(room, password):
        return jsonify({"error": "密码错误或房间不存在"}), 403
    if not content.strip():
        return jsonify({"error": "消息不能为空"}), 400
    msg = save_message(sender, content, role, room)
    active_room["room"] = room
    active_room["password"] = password or ""
    return jsonify({"ok": True, "message": msg})


@app.route("/api/current_room", methods=["POST"])
def api_current_room():
    data = request.get_json(force=True)
    room = norm_room(data.get("room", "main"))
    password = data.get("password", "")
    if not check_room_access(room, password):
        return jsonify({"error": "无权限"}), 403
    active_room["room"] = room
    active_room["password"] = password or ""
    return jsonify({"ok": True, "room": active_room["room"]})


@app.route("/api/rooms", methods=["GET", "POST"])
def api_rooms():
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
    name = norm_room(data.get("name", ""))
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


@app.route("/api/rooms/delete", methods=["POST"])
def api_rooms_delete():
    """删除房间（需要房间密码验证，客厅不可删）"""
    data = request.get_json(force=True)
    name = norm_room(data.get("name", ""))
    password = data.get("password", "").strip() or None
    if not name:
        return jsonify({"error": "房间名不能为空"}), 400
    if name == "main":
        return jsonify({"error": "客厅不能删除"}), 400
    if name not in rooms:
        return jsonify({"error": "房间不存在"}), 404
    info = rooms[name]
    if info.get("password") and password != info["password"]:
        return jsonify({"error": "密码错误"}), 403
    del rooms[name]
    global messages
    messages = [m for m in messages if m.get("room", "main") != name]
    if name in time_settings:
        del time_settings[name]
    if active_room["room"] == name:
        active_room["room"] = "main"
        active_room["password"] = ""
    return jsonify({"ok": True})


@app.route("/api/rooms/join", methods=["POST"])
def api_rooms_join():
    data = request.get_json(force=True)
    name = norm_room(data.get("name", ""))
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
    if request.method == "GET":
        room = norm_room(request.args.get("room", "main"))
        return jsonify({"settings": get_time_setting(room), "room": room})
    data = request.get_json(force=True)
    room = norm_room(data.get("room", "main"))
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
    data = request.get_json(force=True)
    name = data.get("name", "")
    if name:
        online[name] = datetime.datetime.now()
    return jsonify({"ok": True})


@app.route("/api/online", methods=["GET"])
def api_online():
    now = datetime.datetime.now()
    active = [n for n, t in online.items() if (now - t).total_seconds() < 30]
    return jsonify({"online": active})


@app.route("/api/restore", methods=["POST"])
def api_restore():
    data = request.get_json(force=True)
    msgs = data.get("messages", [])
    room = norm_room(data.get("room", "main"))
    password = data.get("password", "")
    if not check_room_access(room, password):
        return jsonify({"error": "无权限"}), 403
    restored = 0
    for m in msgs:
        if not m.get("content"):
            continue
        save_message(m.get("sender", "匿名"), m["content"], m.get("role", "user"), room)
        restored += 1
    active_room["room"] = room
    active_room["password"] = password or ""
    return jsonify({"ok": True, "restored": restored})


@app.route("/api/remove_member", methods=["POST"])
def api_remove_member():
    data = request.get_json(force=True)
    name = data.get("name", "")
    room = norm_room(data.get("room", "main"))
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
    if request.method == "GET":
        return jsonify({
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "8.0.0"},
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
                "serverInfo": {"name": "GroupChat", "version": "8.0.0"},
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
                        "description": "【群聊】以群成员身份发送一条消息。默认发到客厅(main)招待客人；如果决定去小房间，请指定room和password（真人所在房间会自动授权）。可以用group_get_messages/group_get_room_status先了解各房间情况再决定。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者群昵称"},
                                "content": {"type": "string", "description": "消息内容"},
                                "role": {"type": "string", "description": "user=真人, assistant=AI助手", "enum": ["user", "assistant"]},
                                "room": {"type": "string", "description": "可选，房间名（默认main=客厅）"},
                                "password": {"type": "string", "description": "可选，房间密码（真人所在房间不需要）"},
                            },
                            "required": ["sender", "content"],
                        },
                    },
                    {
                        "name": "group_get_messages",
                        "description": "【群聊】查看群聊消息。不带room时返回所有房间总览（含客厅和各个小房间最近消息），由你判断该参与哪个房间；带room时只返回该房间消息（真人所在房间会自动授权）。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "count": {"type": "number", "description": "每个房间获取最近多少条，默认10"},
                                "room": {"type": "string", "description": "可选，指定房间名"},
                                "password": {"type": "string", "description": "可选，房间密码（真人所在房间不需要）"},
                            },
                        },
                    },
                    {
                        "name": "group_get_room_status",
                        "description": "【群聊】查看每个房间的活跃情况（消息数、最后发言），用于判断该留在客厅招待客人还是去小房间。",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "group_get_current_room",
                        "description": "【群聊】查看真人(管理员)当前在哪个房间。如果他们在小房间，你可以选择跟随或留在客厅招待客人。",
                        "inputSchema": {"type": "object", "properties": {}},
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
            room = norm_room(tool_args.get("room", "")) or "main"
            password = tool_args.get("password", "")
            if room != "main" and not password and active_room["room"] == room:
                password = active_room.get("password", "")
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
                "result": {"content": [{"type": "text", "text": f"✅ 已发送到群聊[{room_label(room)}]：{sender}：{content[:30]}"}]},
            })

        elif tool_name == "group_get_messages":
            count = tool_args.get("count", 10)
            room = norm_room(tool_args.get("room", ""))
            password = tool_args.get("password", "")

            if not room:
                # ===== 总览模式：所有房间最近消息（真人所在房间自动授权）=====
                lines = ["📋 群聊总览（各房间最近消息）：", "─" * 30]
                for rname, info in rooms.items():
                    locked = bool(info.get("password"))
                    if locked and password != info["password"] and rname != active_room["room"]:
                        lines.append(f"\n🔒 [{room_label(rname)}]（有密码，未授权查看内容）")
                        continue
                    rmsgs = get_room_messages(rname, count)
                    if not rmsgs:
                        lines.append(f"\n🏠 [{room_label(rname)}] 暂无消息")
                        continue
                    lines.append(f"\n🏠 [{room_label(rname)}] 最近 {len(rmsgs)} 条：")
                    for msg in rmsgs:
                        emoji = "🤖" if msg["role"] == "assistant" else "👤"
                        lines.append(f"  {emoji} {msg['sender']} ({msg['time']}): {msg['content'][:50]}")
                lines.append("\n💡 提示：看完总览后，决定参与哪个房间（留在客厅招待客人，或去小房间）。")
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": "\n".join(lines)}]},
                })

            # ===== 指定房间模式（真人所在房间自动授权）=====
            if room != "main" and not password and active_room["room"] == room:
                password = active_room.get("password", "")
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
                    "result": {"content": [{"type": "text", "text": f"📭 房间[{room_label(room)}]暂时还没有消息"}]},
                })
            result = f"📋 群聊消息记录 [{room_label(room)}]\n" + "─" * 30 + "\n"
            for msg in recent:
                emoji = "🤖" if msg["role"] == "assistant" else "👤"
                result += f"{emoji} {msg['sender']} ({msg['time']}):\n  {msg['content']}\n\n"
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]},
            })

        elif tool_name == "group_get_room_status":
            lines = ["📊 各房间活跃情况："]
            for name, info in rooms.items():
                rmsgs = [m for m in messages if m.get("room", "main") == name]
                last = rmsgs[-1] if rmsgs else None
                lock = "🔒" if info.get("password") else ""
                if last:
                    lines.append(f"  {lock}[{room_label(name)}] {len(rmsgs)}条消息 | 最后发言：{last['sender']} {last['time']}")
                else:
                    lines.append(f"  {lock}[{room_label(name)}] 暂无消息")
            lines.append("\n💡 根据各房间活跃度决定：客厅有人就在客厅招待，朋友在小房间就去小房间。")
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": "\n".join(lines)}]},
            })

        elif tool_name == "group_get_current_room":
            cur = active_room["room"]
            label = room_label(cur)
            has_pwd = "（有密码）" if active_room.get("password") else ""
            return jsonify({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": f"🏠 真人(管理员)当前在：{label}{has_pwd}。\n你可以选择：① 跟随去{label}参与 ② 留在客厅招待客人。"}]},
            })

        elif tool_name == "group_get_rooms":
            lst = []
            for name, info in rooms.items():
                lst.append(room_label(name) + ("（🔒有密码）" if info.get("password") else "（公开）"))
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
