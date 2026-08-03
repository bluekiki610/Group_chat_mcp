from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
from datetime import datetime
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

# ============================================================
#  辅助函数
# ============================================================
def get_current_time(room: str = "main") -> str:
    """根据房间的时间设置返回当前显示时间（北京时间，完整日期时间）"""
    settings = time_settings.get(room, {"mode": "real"})
    now = datetime.now().astimezone()
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
    # 发消息 = 活跃，更新 AI 跟随的房间
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
    """返回在线成员列表，每个元素包含 name 和 room"""
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
    """标准化房间名"""
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
    mode: str  # "real" or "fixed"
    fixed_time: str = ""
    room: str = "main"


class AvatarUpload(BaseModel):
    name: str
    image: str  # base64


class RemoveMember(BaseModel):
    name: str
    room: str = "main"
    password: str = ""


class RestoreMessages(BaseModel):
    messages: List[Dict]
    room: str = "main"
    password: str = ""


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
    """网页上报当前房间（更新 active_room，供 AI 跟随）"""
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
#  MCP 接口（JSON-RPC 2.0）- RikkaHub 连接用
# ============================================================
def log(msg: str):
    """打印日志（Render Logs 可见）"""
    print(f"[MCP] {msg}", flush=True)


@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    """MCP JSON-RPC 2.0 端点（GET 用于健康检查，POST 用于 RikkaHub）"""
    if request.method == "GET":
        return JSONResponse(content={
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "GroupChat", "version": "14.0.0"},
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
                "serverInfo": {"name": "GroupChat", "version": "14.0.0"},
            },
        })

    # 通知类请求：返回空响应 202（不返回 null，避免客户端解析报错）
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
                    }
                ]
            }
        })

    if method == "tools/call":
        tool_name = params.get("name") or ""
        arguments = params.get("arguments", {})

        # 兼容 RikkaHub 等客户端给工具名加的前缀（如 mcp_xxx_group_get_messages）
        KNOWN_TOOLS = ["group_send_to_living_room", "group_send_message", "group_get_messages",
                       "group_get_room_status", "group_get_current_room", "group_get_rooms", "group_get_members"]
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
    """发送到客厅（没有 room 参数，永远发到 main）"""
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
    """发送消息（自动跟随真人房间；真人所在房间自动授权密码）"""
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
    """获取聊天记录"""
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
    """获取所有房间的活跃状态"""
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
    """查询真人当前在哪个房间"""
    current = active_room.get("current", "main")
    room_label = "客厅" if current == "main" else current
    online = get_online_members()
    members_in_room = [u["name"] for u in online if u["room"] == current]
    result_text = f"📍 真人当前在：{room_label}\n👤 该房间在线成员：{', '.join(members_in_room) if members_in_room else '无'}\n\n💡 想跟随就用 group_send_message，想留客厅招待就用 group_send_to_living_room。"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id,
                                "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_rooms(request_id):
    """获取所有房间列表"""
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
    """获取在线成员列表"""
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


# ============================================================
#  启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
