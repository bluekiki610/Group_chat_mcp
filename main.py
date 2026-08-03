from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
from datetime import datetime, timedelta, timezone
import os

app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  根路径返回 HTML 页面（聊天界面 + 家园地图）
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def get_index():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>index.html 未找到，请确保文件已上传</h1>", status_code=404)

# ============================================================
#  数据存储（全部在内存）
# ============================================================
rooms: Dict[str, Dict] = {
    "main": {"name": "main", "has_password": False, "password": "", "creator": "system"}
}
messages: Dict[str, List[Dict]] = {"main": []}
avatars: Dict[str, str] = {}
online_users: Dict[str, str] = {}  # 用户名 -> 房间名
online_times: Dict[str, float] = {}  # 用户名 -> 最后心跳时间
time_settings: Dict[str, Dict] = {}  # 房间 -> {"mode": "real"|"fixed", "fixed_time": "19:00"}
active_room: Dict[str, str] = {"current": "main", "password": ""}  # 真人当前房间 + 密码（AI 自动跟随/授权）

# ----- 家园数据 -----
home_areas: List[str] = ["湖畔", "森林", "花园", "山顶"]  # 固定区域
houses: Dict[str, Dict] = {}  # house_id -> {"name","emoji","area","created"}
house_rooms: Dict[str, List[str]] = {}  # house_id -> [房间名...]
room_bg: Dict[str, str] = {}  # 聊天房间 -> 背景图（base64/url）
notes: Dict[str, List[Dict]] = {}  # 房间 -> [{"author","text","time"}]
diaries: Dict[str, List[Dict]] = {}  # 房间 -> [{"author","text","time"}]
house_seq: int = 0

# ============================================================
#  辅助函数
# ============================================================
def get_current_time(room: str = "main") -> str:
    """根据房间的时间设置返回当前显示时间（北京时间，完整日期时间）"""
    settings = time_settings.get(room, {"mode": "real"})
    now = datetime.now(timezone.utc) + timedelta(hours=8)  # 固定北京时间（UTC+8）
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
    room = clean_room_name(room)
    if room in ("", "main"):
        return True
    if not room_exists(room):
        return False
    if not is_room_locked(room):
        return True
    return (password or "") == get_room_password(room)


def save_entry(sender: str, content: str, role: str, room: str = "main") -> dict:
    room = clean_room_name(room)
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
    active_room["current"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return entry


def get_all_rooms() -> List[Dict]:
    result = []
    for name, info in rooms.items():
        result.append({
            "name": name,
            "has_password": info.get("has_password", False),
            "creator": info.get("creator", "system")
        })
    return result


def get_online_members() -> List[Dict]:
    now = time.time()
    to_remove = []
    for name, last_time in online_times.items():
        if now - last_time > 30:
            to_remove.append(name)
    for name in to_remove:
        if name in online_users:
            del online_users[name]
        if name in online_times:
            del online_times[name]
    result = []
    for name, room in online_users.items():
        result.append({"name": name, "room": room})
    return result


def clean_room_name(room: str) -> str:
    return room.strip() if room else "main"


# ============================================================
#  API 模型（Pydantic）
# ============================================================
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


# ----- 家园模型 -----
class HouseCreate(BaseModel):
    name: str
    emoji: str
    area: str


class HouseDelete(BaseModel):
    house_id: str


class HouseRoomCreate(BaseModel):
    house_id: str
    name: str


class HouseRoomDelete(BaseModel):
    house_id: str
    room: str


class RoomBg(BaseModel):
    room: str
    image: str


class NoteItem(BaseModel):
    room: str
    author: str
    text: str


# ============================================================
#  API 端点（网页用）
# ============================================================

# ----- 消息相关 -----
@app.post("/api/messages")
async def send_message(msg: Message):
    room = clean_room_name(msg.room)
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
    room = clean_room_name(room)
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
    room = clean_room_name(data.room)
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
    active_room["current"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return {"ok": True}


# ----- 房间管理 -----
@app.get("/api/rooms")
async def list_rooms():
    return {"rooms": get_all_rooms()}


@app.post("/api/rooms")
async def create_room(data: RoomCreate):
    name = data.name.strip()
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
    name = data.name.strip()
    if not name or name not in rooms:
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name):
        raise HTTPException(status_code=403, detail="密码错误")
    return {"ok": True, "room": name}


@app.post("/api/rooms/delete")
async def delete_room(data: RoomDelete):
    name = data.name.strip()
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
    if active_room["current"] == name:
        active_room["current"] = "main"
        active_room["password"] = ""
    return {"ok": True}


@app.post("/api/current_room")
async def set_current_room(data: CurrentRoom):
    room = clean_room_name(data.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    active_room["current"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    return {"ok": True, "room": room}


# ----- 在线状态 -----
@app.post("/api/heartbeat")
async def heartbeat(data: Heartbeat):
    if data.name:
        online_users[data.name] = data.room or "main"
        online_times[data.name] = time.time()
        return {"ok": True}
    return {"ok": False}


@app.get("/api/online")
async def get_online():
    return {"online": get_online_members()}


# ----- 头像 -----
@app.post("/api/avatar")
async def upload_avatar(data: AvatarUpload):
    if data.name:
        avatars[data.name] = data.image
        return {"ok": True}
    return {"ok": False}


@app.get("/api/avatar")
async def get_avatars():
    return {"avatars": avatars}


# ----- 时间设置 -----
@app.get("/api/time_settings")
async def get_time_settings(room: str = "main"):
    room = clean_room_name(room)
    settings = time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})
    return {"settings": settings}


@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = clean_room_name(data.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if data.mode not in ("real", "fixed"):
        raise HTTPException(status_code=400, detail="mode 必须为 real 或 fixed")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}
    return {"settings": time_settings[room]}


# ----- 删除成员 -----
@app.post("/api/remove_member")
async def remove_member(data: RemoveMember):
    room = clean_room_name(data.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if room in messages:
        messages[room] = [m for m in messages[room] if m["sender"] != data.name]
    if data.name in online_users:
        online_users[data.name] = "main"
    return {"ok": True}


# ============================================================
#  家园 API（地图/房子/房间/背景/纸条/日记）
# ============================================================
@app.get("/api/home")
async def get_home():
    return {"areas": home_areas, "houses": houses, "house_rooms": house_rooms, "room_bg": room_bg}


@app.post("/api/home/house")
async def create_house(data: HouseCreate):
    global house_seq
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="房子名不能为空")
    if data.area not in home_areas:
        raise HTTPException(status_code=400, detail="区域不存在")
    house_seq += 1
    hid = f"h{house_seq}"
    houses[hid] = {"name": data.name.strip(), "emoji": data.emoji or "🏠", "area": data.area, "created": get_current_time()}
    house_rooms[hid] = []
    return {"ok": True, "house_id": hid}


@app.post("/api/home/house/delete")
async def delete_house(data: HouseDelete):
    hid = data.house_id
    if hid not in houses:
        raise HTTPException(status_code=404, detail="房子不存在")
    for r in house_rooms.get(hid, []):
        if r in rooms:
            del rooms[r]
        if r in messages:
            del messages[r]
        if r in room_bg:
            del room_bg[r]
        if r in notes:
            del notes[r]
        if r in diaries:
            del diaries[r]
    del houses[hid]
    if hid in house_rooms:
        del house_rooms[hid]
    return {"ok": True}


@app.post("/api/home/room")
async def create_house_room(data: HouseRoomCreate):
    name = data.name.strip()
    if data.house_id not in houses:
        raise HTTPException(status_code=404, detail="房子不存在")
    if not name:
        raise HTTPException(status_code=400, detail="房间名不能为空")
    if name in rooms:
        raise HTTPException(status_code=400, detail="房间已存在")
    rooms[name] = {"name": name, "has_password": False, "password": "", "creator": "home"}
    messages[name] = []
    house_rooms[data.house_id].append(name)
    return {"ok": True, "room": name}


@app.post("/api/home/room/delete")
async def delete_house_room(data: HouseRoomDelete):
    hid, room = data.house_id, data.room
    if hid not in houses:
        raise HTTPException(status_code=404, detail="房子不存在")
    if room in house_rooms.get(hid, []):
        house_rooms[hid].remove(room)
    if room in rooms:
        del rooms[room]
    if room in messages:
        del messages[room]
    if room in room_bg:
        del room_bg[room]
    return {"ok": True}


@app.post("/api/home/bg")
async def set_room_bg(data: RoomBg):
    room = clean_room_name(data.room)
    room_bg[room] = data.image
    return {"ok": True}


@app.get("/api/notes")
async def get_notes(room: str = "main"):
    room = clean_room_name(room)
    return {"notes": notes.get(room, [])}


@app.post("/api/notes")
async def add_note(data: NoteItem):
    room = clean_room_name(data.room)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    if room not in notes:
        notes[room] = []
    notes[room].append({"author": data.author or "匿名", "text": data.text, "time": get_current_time(room)})
    if len(notes[room]) > 100:
        notes[room] = notes[room][-100:]
    return {"ok": True}


@app.get("/api/diaries")
async def get_diaries(room: str = "main"):
    room = clean_room_name(room)
    return {"diaries": diaries.get(room, [])}


@app.post("/api/diaries")
async def add_diary(data: NoteItem):
    room = clean_room_name(data.room)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    if room not in diaries:
        diaries[room] = []
    diaries[room].append({"author": data.author or "匿名", "text": data.text, "time": get_current_time(room)})
    if len(diaries[room]) > 100:
        diaries[room] = diaries[room][-100:]
    return {"ok": True}


# ============================================================
#  MCP 接口（JSON-RPC 2.0）- RikkaHub 连接用
# ============================================================
def log(msg: str):
    print(f"[MCP] {msg}", flush=True)


@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    if request.method == "GET":
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "GroupChat", "version": "18.0.0"},
            },
        })

    try:
        body = await request.json()
    except Exception:
        log("⚠️ 请求不是合法 JSON")
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
        )

    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")
    log(f"收到请求: method={method}, id={request_id}")

    if method == "initialize":
        log("→ 处理 initialize")
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "GroupChat", "version": "18.0.0"},
            },
        })

    if isinstance(method, str) and method.startswith("notifications/"):
        log(f"→ 处理通知 {method}（返回空 202）")
        return Response(status_code=202)

    if method == "ping":
        log("→ 处理 ping")
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {}})

    if method == "tools/list":
        log("→ 处理 tools/list")
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "group_send_to_living_room",
                        "description": "发送消息到客厅（公共区域）。当用户要求你留在客厅招待客人时使用此工具。注意：此工具没有 room 参数，永远发到客厅。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者名字，通常填你的名字"},
                                "content": {"type": "string", "description": "要发送的消息内容"},
                                "role": {"type": "string", "description": "角色，填 'assistant'"}
                            },
                            "required": ["sender", "content"]
                        }
                    },
                    {
                        "name": "group_send_message",
                        "description": "发送消息到真人当前所在的房间（自动跟随）。AI 默认使用此工具，会自动跟随真人切换房间。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sender": {"type": "string", "description": "发送者名字，通常填你的名字"},
                                "content": {"type": "string", "description": "要发送的消息内容"},
                                "role": {"type": "string", "description": "角色，填 'assistant'"},
                                "room": {"type": "string", "description": "可选，指定房间名。如果不填，自动发送到真人当前所在房间"}
                            },
                            "required": ["sender", "content"]
                        }
                    },
                    {
                        "name": "group_get_messages",
                        "description": "获取聊天记录。可以获取所有房间的总览，也可以获取指定房间的详细消息。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string", "description": "可选，指定房间名。不填则返回所有房间的总览"},
                                "count": {"type": "integer", "description": "每个房间获取的消息数量，默认 10"}
                            }
                        }
                    },
                    {
                        "name": "group_get_room_status",
                        "description": "获取所有房间的活跃状态（在线人数、最近消息时间等），用于 AI 决定去哪个房间串门。",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "group_get_current_room",
                        "description": "查询真人当前在哪个房间，用于 AI 判断是否要跟过去。",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "group_get_rooms",
                        "description": "获取所有房间列表（含密码状态）。",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "group_get_members",
                        "description": "获取在线成员列表及他们所在的房间。",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "group_get_home_status",
                        "description": "获取家园概况：有哪些区域、房子、房子里的房间。",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "group_write_note",
                        "description": "在某个房间留下一张纸条（小惊喜/留言）。例如在真人的卧室留一张暖心纸条。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string", "description": "要写纸条的房间名"},
                                "content": {"type": "string", "description": "纸条内容"},
                                "sender": {"type": "string", "description": "你的名字"}
                            },
                            "required": ["room", "content"]
                        }
                    },
                    {
                        "name": "group_write_diary",
                        "description": "在某个房间写下日记（记录心情/心事，只有翻开日记本才能看到）。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string", "description": "写日记的房间名"},
                                "content": {"type": "string", "description": "日记内容"},
                                "sender": {"type": "string", "description": "你的名字"}
                            },
                            "required": ["room", "content"]
                        }
                    },
                    {
                        "name": "group_get_notes",
                        "description": "查看某个房间里别人留下的纸条。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string", "description": "房间名"}
                            },
                            "required": ["room"]
                        }
                    },
                    {
                        "name": "group_get_diaries",
                        "description": "查看某个房间的日记。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "room": {"type": "string", "description": "房间名"}
                            },
                            "required": ["room"]
                        }
                    }
                ]
            }
        })

    if method == "tools/call":
        tool_name = params.get("name") or ""
        arguments = params.get("arguments", {})

        KNOWN_TOOLS = ["group_send_to_living_room", "group_send_message", "group_get_messages",
                       "group_get_room_status", "group_get_current_room", "group_get_rooms", "group_get_members",
                       "group_get_home_status", "group_write_note", "group_write_diary",
                       "group_get_notes", "group_get_diaries"]
        if tool_name not in KNOWN_TOOLS:
            for known in KNOWN_TOOLS:
                if tool_name.endswith(known):
                    log(f"→ 工具名兼容转换: {tool_name} -> {known}")
                    tool_name = known
                    break
        log(f"→ 处理 tools/call: {tool_name}")

        if tool_name == "group_send_to_living_room":
            return await mcp_send_to_living_room(arguments, request_id)
        elif tool_name == "group_send_message":
            return await mcp_send_message(arguments, request_id)
        elif tool_name == "group_get_messages":
            return await mcp_get_messages(arguments, request_id)
        elif tool_name == "group_get_room_status":
            return await mcp_get_room_status(request_id)
        elif tool_name == "group_get_current_room":
            return await mcp_get_current_room(request_id)
        elif tool_name == "group_get_rooms":
            return await mcp_get_rooms(request_id)
        elif tool_name == "group_get_members":
            return await mcp_get_members(request_id)
        elif tool_name == "group_get_home_status":
            return await mcp_get_home_status(request_id)
        elif tool_name == "group_write_note":
            return await mcp_write_note(arguments, request_id)
        elif tool_name == "group_write_diary":
            return await mcp_write_diary(arguments, request_id)
        elif tool_name == "group_get_notes":
            return await mcp_get_notes(arguments, request_id)
        elif tool_name == "group_get_diaries":
            return await mcp_get_diaries(arguments, request_id)
        else:
            return JSONResponse(content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}
            })

    log(f"→ 未知方法: {method}")
    return JSONResponse(content={
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method '{method}' not found"}
    })


# ============================================================
#  MCP 工具实现
# ============================================================
async def mcp_send_to_living_room(args: dict, request_id):
    sender = args.get("sender", "助手")
    content = args.get("content", "")
    role = args.get("role", "assistant")
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": "❌ 消息不能为空"}]}})
    save_entry(sender, content, role, "main")
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": f"✅ 已发送到客厅：{content}"}]}})


async def mcp_send_message(args: dict, request_id):
    sender = args.get("sender", "助手")
    content = args.get("content", "")
    role = args.get("role", "assistant")
    room = args.get("room")
    password = ""
    if not room:
        room = active_room.get("current", "main")
        password = active_room.get("password", "")
    else:
        room = clean_room_name(room)
        if active_room.get("current") == room:
            password = active_room.get("password", "")
    if not room_exists(room):
        room = "main"
    if is_room_locked(room) and password != get_room_password(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」有密码，未授权发送（真人不在该房间）。"}]}})
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": "❌ 消息不能为空"}]}})
    save_entry(sender, content, role, room)
    room_label = "客厅" if room == "main" else room
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": f"✅ 已发送到「{room_label}」：{content}"}]}})


async def mcp_get_messages(args: dict, request_id):
    room = args.get("room")
    count = args.get("count", 10)
    if room:
        room = clean_room_name(room)
        if not room_exists(room):
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                        "error": {"code": -32000, "message": f"房间 '{room}' 不存在"}})
        msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))[-count:]
        if not msgs:
            text = f"📭 房间「{room}」暂无消息"
        else:
            text = f"📋 房间「{room}」最近 {len(msgs)} 条消息：\n" + "\n".join(
                [f"{m['sender']}: {m['content']} ({m['time']})" for m in msgs]
            )
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": text}]}})
    else:
        result_text = "📋 所有房间消息总览：\n"
        for room_name, msgs in messages.items():
            room_label = "客厅" if room_name == "main" else room_name
            if msgs:
                last_msgs = sorted(msgs, key=lambda x: x.get("time", ""))[-count:]
                result_text += f"\n🏠 {room_label}（{len(last_msgs)} 条）：\n"
                for m in last_msgs:
                    result_text += f"  {m['sender']}: {m['content']} ({m['time']})\n"
            else:
                result_text += f"\n🏠 {room_label}：暂无消息\n"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_room_status(request_id):
    online = get_online_members()
    room_stats = {}
    for room_name in rooms.keys():
        room_label = "客厅" if room_name == "main" else room_name
        online_count = len([u for u in online if u["room"] == room_name])
        msg_count = len(messages.get(room_name, []))
        last_msg = messages.get(room_name, [])[-1] if messages.get(room_name) else None
        room_stats[room_label] = {
            "在线人数": online_count,
            "消息数": msg_count,
            "最后消息": f"{last_msg['sender']}: {last_msg['content']} ({last_msg['time']})" if last_msg else "无"
        }
    result_text = "📊 房间活跃状态：\n"
    for room, stats in room_stats.items():
        result_text += f"\n🏠 {room}：\n  👤 在线：{stats['在线人数']} 人\n  💬 消息数：{stats['消息数']} 条\n  📝 最后消息：{stats['最后消息']}\n"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_current_room(request_id):
    current = active_room.get("current", "main")
    room_label = "客厅" if current == "main" else current
    online = get_online_members()
    members_in_room = [u["name"] for u in online if u["room"] == current]
    result_text = f"📍 真人当前在：{room_label}\n👤 该房间在线成员：{', '.join(members_in_room) if members_in_room else '无'}\n\n💡 想跟随就用 group_send_message，想留客厅招待就用 group_send_to_living_room。"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_rooms(request_id):
    room_list = get_all_rooms()
    result_text = "📋 所有房间列表：\n"
    for room in room_list:
        name = room["name"]
        label = "客厅" if name == "main" else name
        locked = "🔒 有密码" if room["has_password"] else "🔓 公开"
        creator = room.get("creator", "system")
        result_text += f"\n🏠 {label}（{locked}，创建者：{creator}）"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_members(request_id):
    online = get_online_members()
    if not online:
        result_text = "🟢 当前没有在线成员"
    else:
        result_text = "🟢 在线成员：\n"
        for member in online:
            room_label = "客厅" if member["room"] == "main" else member["room"]
            result_text += f"\n👤 {member['name']}（在 {room_label}）"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


# ----- 家园 MCP 工具 -----
async def mcp_get_home_status(request_id):
    result_text = "🏡 家园概况：\n"
    for area in home_areas:
        area_houses = [h for h in houses.values() if h["area"] == area]
        if area_houses:
            result_text += f"\n📍 {area}：\n"
            for h in area_houses:
                rooms_list = house_rooms.get([k for k, v in houses.items() if v == h][0] if False else next((k for k, v in houses.items() if v["name"] == h["name"] and v["area"] == area), ''), [])
                result_text += f"  {h['emoji']} {h['name']}（房间：{', '.join(rooms_list) if rooms_list else '无'}）\n"
        else:
            result_text += f"\n📍 {area}：空置\n"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_write_note(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    content = args.get("content", "")
    sender = args.get("sender", "神秘人")
    if not room_exists(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在，无法写纸条"}]}})
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": "❌ 纸条内容不能为空"}]}})
    if room not in notes:
        notes[room] = []
    notes[room].append({"author": sender, "text": content, "time": get_current_time(room)})
    if len(notes[room]) > 100:
        notes[room] = notes[room][-100:]
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": f"💌 纸条已悄悄放进「{room}」！"}]}})


async def mcp_write_diary(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    content = args.get("content", "")
    sender = args.get("sender", "神秘人")
    if not room_exists(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在，无法写日记"}]}})
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                    "result": {"content": [{"type": "text", "text": "❌ 日记内容不能为空"}]}})
    if room not in diaries:
        diaries[room] = []
    diaries[room].append({"author": sender, "text": content, "time": get_current_time(room)})
    if len(diaries[room]) > 100:
        diaries[room] = diaries[room][-100:]
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": f"📖 日记已悄悄写进「{room}」的日记本！"}]}})


async def mcp_get_notes(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    items = notes.get(room, [])
    if not items:
        text = f"📭 「{room}」里没有纸条"
    else:
        text = f"💌 「{room}」的纸条（{len(items)} 张）：\n" + "\n".join(
            [f"· {n['author']}：{n['text']}（{n['time']}）" for n in items]
        )
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": text}]}})


async def mcp_get_diaries(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    items = diaries.get(room, [])
    if not items:
        text = f"📭 「{room}」的日记本是空白的"
    else:
        text = f"📖 「{room}」的日记（{len(items)} 篇）：\n" + "\n".join(
            [f"· {n['author']}：{n['text']}（{n['time']}）" for n in items]
        )
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": text}]}})


# ============================================================
#  启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
