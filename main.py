import os
import datetime
from typing import Dict, List
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()

# ------------------- 数据存储 -------------------
rooms: Dict[str, dict] = {"main": {"has_password": False, "password": "", "creator": "system"}}
messages: Dict[str, List[dict]] = {}
avatars: Dict[str, str] = {}
online_users: Dict[str, str] = {}  # name -> room
online_times: Dict[str, datetime.datetime] = {}  # name -> 最后心跳时间
time_settings: Dict[str, dict] = {}
active_room: dict = {"room": "main", "password": ""}  # 真人当前所在房间（AI 自动跟随）

# ------------------- 辅助函数 -------------------
def norm_room(room: str) -> str:
    return (room or "").strip()


def room_label(room: str) -> str:
    room = norm_room(room)
    return "客厅" if room == "main" else room


def get_current_time(room: str = "main") -> str:
    settings = time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})
    now = datetime.datetime.now().astimezone()
    if settings.get("mode") == "fixed":
        return now.strftime("%Y-%m-%d") + " " + settings.get("fixed_time", "19:00")
    return now.strftime("%Y-%m-%d %H:%M:%S")


def get_room_password(room: str) -> str:
    return rooms.get(room, {}).get("password", "")


def room_exists(room: str) -> bool:
    return room in rooms


def is_room_locked(room: str) -> bool:
    return rooms.get(room, {}).get("has_password", False)


def check_room_access(room: str, password: str) -> bool:
    room = norm_room(room)
    if room in ("", "main"):
        return True
    if not room_exists(room):
        return False
    if not is_room_locked(room):
        return True
    return (password or "") == get_room_password(room)


def save_entry(sender: str, content: str, role: str, room: str = "main") -> dict:
    room = norm_room(room)
    entry = {
        "sender": sender,
        "content": content,
        "role": role,
        "time": get_current_time(room),
        "room": room,
    }
    if room not in messages:
        messages[room] = []
    messages[room].append(entry)
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    # 发消息 = 活跃，更新 AI 跟随的房间
    active_room["room"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return entry


# ------------------- API 模型 -------------------
class Message(BaseModel):
    sender: str
    content: str
    role: str = "user"
    room: str = "main"
    password: str = ""


class RoomCreate(BaseModel):
    name: str
    password: str = ""
    creator: str = "匿名"


class RoomJoin(BaseModel):
    name: str
    password: str = ""


class RoomDelete(BaseModel):
    name: str
    password: str = ""


class Heartbeat(BaseModel):
    name: str
    room: str = "main"


class CurrentRoom(BaseModel):
    room: str
    password: str = ""


class TimeSettings(BaseModel):
    mode: str
    fixed_time: str = ""
    room: str = "main"


class AvatarUpload(BaseModel):
    name: str
    image: str


class RemoveMember(BaseModel):
    name: str
    room: str = "main"
    password: str = ""


class RestoreMessages(BaseModel):
    messages: List[Dict]
    room: str = "main"
    password: str = ""


# ------------------- API 端点 -------------------
@app.post("/api/messages")
async def send_message(msg: Message):
    room = norm_room(msg.room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, msg.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if not msg.content.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    entry = save_entry(msg.sender, msg.content, msg.role, room)
    return {"ok": True, "time": entry["time"]}


@app.get("/api/messages")
async def get_messages(count: int = 200, room: str = "main", password: str = ""):
    room = norm_room(room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, password):
        raise HTTPException(status_code=403, detail="密码错误")
    msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))
    if len(msgs) > count:
        msgs = msgs[-count:]
    return {"messages": msgs, "room": room}


@app.post("/api/restore")
async def restore_messages(data: RestoreMessages):
    room = norm_room(data.room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if room not in messages:
        messages[room] = []
    existing = set()
    for m in messages[room]:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        existing.add(key)
    for m in data.messages:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        if key not in existing:
            messages[room].append(m)
            existing.add(key)
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    active_room["room"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return {"ok": True}


@app.get("/api/rooms")
async def list_rooms():
    room_list = []
    for name, info in rooms.items():
        room_list.append({
            "name": name,
            "has_password": info.get("has_password", False),
            "creator": info.get("creator", "system")
        })
    return {"rooms": room_list}


@app.post("/api/rooms")
async def create_room(data: RoomCreate):
    name = norm_room(data.name)
    if not name:
        raise HTTPException(status_code=400, detail="房间名不能为空")
    if name in rooms:
        raise HTTPException(status_code=400, detail="房间已存在")
    rooms[name] = {
        "name": name,
        "has_password": bool(data.password),
        "password": data.password,
        "creator": data.creator
    }
    messages[name] = []
    return {"ok": True, "room": name}


@app.post("/api/rooms/join")
async def join_room(data: RoomJoin):
    name = norm_room(data.name)
    if not name or name not in rooms:
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name):
        raise HTTPException(status_code=403, detail="密码错误")
    return {"ok": True, "room": name}


@app.post("/api/rooms/delete")
async def delete_room(data: RoomDelete):
    name = norm_room(data.name)
    if name == "main":
        raise HTTPException(status_code=403, detail="不能删除公共客厅")
    if name not in rooms:
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name):
        raise HTTPException(status_code=403, detail="密码错误")
    del rooms[name]
    if name in messages:
        del messages[name]
    if name in time_settings:
        del time_settings[name]
    for user, room in list(online_users.items()):
        if room == name:
            online_users[user] = "main"
    if active_room["room"] == name:
        active_room["room"] = "main"
        active_room["password"] = ""
    return {"ok": True}


@app.post("/api/current_room")
async def set_current_room(data: CurrentRoom):
    room = norm_room(data.room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    active_room["room"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return {"ok": True, "room": room}


@app.post("/api/heartbeat")
async def heartbeat(data: Heartbeat):
    if data.name:
        online_users[data.name] = data.room or "main"
        online_times[data.name] = datetime.datetime.now()
        return {"ok": True}
    return {"ok": False}


@app.get("/api/online")
async def get_online():
    now = datetime.datetime.now()
    online_list = []
    for name, room in online_users.items():
        last = online_times.get(name)
        if last and (now - last).total_seconds() < 30:
            online_list.append({"name": name, "room": room})
    return {"online": online_list}


@app.post("/api/avatar")
async def upload_avatar(data: AvatarUpload):
    if data.name:
        avatars[data.name] = data.image
        return {"ok": True}
    return {"ok": False}


@app.get("/api/avatar")
async def get_avatars():
    return {"avatars": avatars}


@app.get("/api/time_settings")
async def get_time_settings(room: str = "main"):
    room = norm_room(room) or "main"
    settings = time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})
    return {"settings": settings}


@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = norm_room(data.room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}
    return {"settings": time_settings[room]}


@app.post("/api/remove_member")
async def remove_member(data: RemoveMember):
    room = norm_room(data.room) or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if room in messages:
        messages[room] = [m for m in messages[room] if m["sender"] != data.name]
    if data.name in online_users:
        online_users[data.name] = "main"
    return {"ok": True}


# ------------------- MCP 端点（给 RikkaHub 的 AI 用） -------------------
def tool_result(rid, text: str):
    return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}}


@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    if request.method == "GET":
        return {
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "12.0.0"},
            },
        }
    try:
        body = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
    method = body.get("method", "")
    params = body.get("params", {})
    rid = body.get("id", 1)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "GroupChat", "version": "12.0.0"},
            },
        }
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "tools": [
                    {
                        "name": "group_send_to_living_room",
                        "description": "【群聊】以群成员身份发送一条消息到【客厅】（招待客人用）。如果你想留在客厅招待客人、不跟随真人去小房间，就使用这个工具。参数只有sender/content/role，不需要room。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者群昵称"},
                                "content": {"type": "string", "description": "消息内容"},
                                "role": {"type": "string", "description": "assistant=AI助手", "enum": ["user", "assistant"]},
                            },
                            "required": ["sender", "content"],
                        },
                    },
                    {
                        "name": "group_send_message",
                        "description": "【群聊】以群成员身份发送一条消息到真人(管理员)当前所在的房间（自动跟随）。如果你想留在客厅招待客人，请改用 group_send_to_living_room。",
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
                        "description": "【群聊】查看真人(管理员)当前在哪个房间。",
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
        }
    if method == "tools/call":
        return await handle_tool_call(rid, params)
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}


async def handle_tool_call(rid, params: dict):
    name = params.get("name", "")
    args = params.get("arguments", {})

    try:
        if name in ("group_send_message", "group_send_to_living_room"):
            sender = args.get("sender", "匿名")
            content = args.get("content", "")
            role = args.get("role", "assistant")
            if not content:
                return tool_result(rid, "❌ 消息不能为空")
            if name == "group_send_to_living_room":
                room = "main"
            else:
                room = active_room["room"]
                if not check_room_access(room, ""):
                    return tool_result(rid, f"❌ 房间不存在或密码错误：{room}")
            save_entry(sender, content, role, room)
            note = f"✅ 已发送到群聊[{room_label(room)}]：{sender}：{content[:30]}"
            if room == "main":
                note += "（你发到了客厅，招待客人中）"
            return tool_result(rid, note)

        elif name == "group_get_messages":
            count = int(args.get("count", 10))
            room = norm_room(args.get("room", ""))
            password = args.get("password", "")
            if not room:
                lines = ["📋 群聊总览（各房间最近消息）：", "─" * 30]
                for rname in rooms.keys():
                    locked = is_room_locked(rname)
                    if locked and password != get_room_password(rname) and rname != active_room["room"]:
                        lines.append(f"\n🔒 [{room_label(rname)}]（有密码，未授权查看内容）")
                        continue
                    rmsgs = sorted(messages.get(rname, []), key=lambda x: x.get("time", ""))[-count:]
                    if not rmsgs:
                        lines.append(f"\n🏠 [{room_label(rname)}] 暂无消息")
                        continue
                    lines.append(f"\n🏠 [{room_label(rname)}] 最近 {len(rmsgs)} 条：")
                    for msg in rmsgs:
                        emoji = "🤖" if msg.get("role") == "assistant" else "👤"
                        lines.append(f"  {emoji} {msg.get('sender')} ({msg.get('time')}): {str(msg.get('content'))[:50]}")
                lines.append("\n💡 提示：想留在客厅招待客人→用 group_send_to_living_room；想跟随真人→用 group_send_message。")
                return tool_result(rid, "\n".join(lines))
            if not check_room_access(room, password):
                return tool_result(rid, f"❌ 房间不存在或密码错误：{room}")
            rmsgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))[-count:]
            if not rmsgs:
                return tool_result(rid, f"📭 房间[{room_label(room)}]暂时还没有消息")
            result = f"📋 群聊消息记录 [{room_label(room)}]\n" + "─" * 30 + "\n"
            for msg in rmsgs:
                emoji = "🤖" if msg.get("role") == "assistant" else "👤"
                result += f"{emoji} {msg.get('sender')} ({msg.get('time')}):\n  {msg.get('content')}\n\n"
            return tool_result(rid, result)

        elif name == "group_get_room_status":
            lines = ["📊 各房间活跃情况："]
            for rname in rooms.keys():
                rmsgs = messages.get(rname, [])
                last = rmsgs[-1] if rmsgs else None
                lock = "🔒" if is_room_locked(rname) else ""
                if last:
                    lines.append(f"  {lock}[{room_label(rname)}] {len(rmsgs)}条消息 | 最后发言：{last.get('sender')} {last.get('time')}")
                else:
                    lines.append(f"  {lock}[{room_label(rname)}] 暂无消息")
            lines.append("\n💡 根据各房间活跃度决定：客厅有人就在客厅招待，朋友在小房间就去小房间。")
            return tool_result(rid, "\n".join(lines))

        elif name == "group_get_current_room":
            cur = active_room["room"]
            label = room_label(cur)
            has_pwd = "（有密码）" if active_room.get("password") else ""
            return tool_result(rid, f"🏠 真人(管理员)当前在：{label}{has_pwd}。\n想跟随就用 group_send_message，想留客厅招待就用 group_send_to_living_room。")

        elif name == "group_get_rooms":
            lst = []
            for rname in rooms.keys():
                lst.append(room_label(rname) + ("（🔒有密码）" if is_room_locked(rname) else "（公开）"))
            return tool_result(rid, "🏠 房间列表：" + ("、".join(lst) if lst else "暂无"))

        elif name == "group_get_members":
            return tool_result(rid, "👥 群成员：亦言、黎深、小旭、秦彻")
    except Exception as e:
        return tool_result(rid, f"❌ 工具调用出错：{e}")

    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}


# ------------------- 启动 -------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
