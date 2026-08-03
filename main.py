from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
import asyncio
from datetime import datetime
import random

app = FastAPI()

# 允许跨域（你的前端可能在不同端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------- 数据存储（内存） -------------------
# 消息存储：每个房间一个列表
messages: Dict[str, List[Dict]] = {
    "main": []   # 默认客厅
}

# 房间信息：存储密码等
rooms: Dict[str, Dict] = {
    "main": {"name": "main", "has_password": False, "password": "", "creator": "system"}
}

# 在线用户：{ username: room_name }
online_users: Dict[str, str] = {}

# 头像存储：{ username: base64_image }
avatars: Dict[str, str] = {}

# 时间设置：每个房间独立
time_settings: Dict[str, Dict] = {}  # { room: {"mode": "real"|"fixed", "fixed_time": "19:00"} }

# 为了持久化，你可以将这些存储到文件或数据库，这里用内存演示

# ------------------- 辅助函数 -------------------
def get_current_time(room: str = "main") -> str:
    """根据房间的时间设置返回当前显示时间"""
    settings = time_settings.get(room, {"mode": "real"})
    if settings.get("mode") == "fixed":
        return settings.get("fixed_time", "19:00")
    else:
        # 返回北京时间（时:分）
        now = datetime.now().astimezone()
        return now.strftime("%H:%M")

def get_room_password(room: str) -> str:
    return rooms.get(room, {}).get("password", "")

def room_exists(room: str) -> bool:
    return room in rooms

def is_room_locked(room: str) -> bool:
    return rooms.get(room, {}).get("has_password", False)

# ------------------- API 模型 -------------------
class Message(BaseModel):
    sender: str
    content: str
    role: str = "user"   # user / assistant
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
    mode: str   # "real" or "fixed"
    fixed_time: str = ""
    room: str = "main"

class AvatarUpload(BaseModel):
    name: str
    image: str   # base64

class RemoveMember(BaseModel):
    name: str
    room: str = "main"
    password: str = ""

class RestoreMessages(BaseModel):
    messages: List[Dict]
    room: str = "main"
    password: str = ""

# ------------------- API 端点 -------------------

@app.get("/")
async def root():
    return {"status": "ok", "message": "人机小窝后端运行中"}

# ----- 消息相关 -----
@app.post("/api/messages")
async def send_message(msg: Message):
    room = msg.room or "main"
    # 检查房间是否存在
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    # 如果房间有密码，验证
    if is_room_locked(room) and msg.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    
    # 生成时间
    msg_time = get_current_time(room)
    entry = {
        "sender": msg.sender,
        "content": msg.content,
        "role": msg.role,
        "time": msg_time,
        "room": room
    }
    if room not in messages:
        messages[room] = []
    messages[room].append(entry)
    # 限制消息数量
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    
    # ---- 这里可以触发 AI 助手回复（调用 MCP 工具） ----
    # 你可以在这里调用你的 MCP 工具，让 AI 自动回复
    # 例如：如果消息发送者是用户，可以触发 AI 回复
    # 我们这里简单演示：如果 sender 不是 "助手"，则触发一个异步任务
    if msg.role != "assistant" and msg.sender != "助手":
        asyncio.create_task(auto_reply(msg.sender, msg.content, room))
    
    return {"ok": True, "time": msg_time}

async def auto_reply(sender: str, content: str, room: str):
    """模拟 AI 自动回复（你可以替换为真实的 MCP 调用）"""
    # 这里演示随机回复，实际可以调用你的 MCP 工具
    await asyncio.sleep(1)  # 模拟思考
    # 简单的自动回复（你可以替换为调用 deepseek 等）
    replies = [
        "我听到啦！",
        "嗯嗯，有道理～",
        "好有意思！",
        "继续说吧，我在听。",
        "我会记住的。"
    ]
    reply_text = random.choice(replies)
    # 发送回复消息
    entry = {
        "sender": "助手",
        "content": reply_text,
        "role": "assistant",
        "time": get_current_time(room),
        "room": room
    }
    if room not in messages:
        messages[room] = []
    messages[room].append(entry)

@app.get("/api/messages")
async def get_messages(count: int = 200, room: str = "main", password: str = ""):
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    
    msgs = messages.get(room, [])
    # 按时间排序
    msgs_sorted = sorted(msgs, key=lambda x: x.get("time", ""))
    if len(msgs_sorted) > count:
        msgs_sorted = msgs_sorted[-count:]
    return {"messages": msgs_sorted, "room": room}

@app.post("/api/restore")
async def restore_messages(data: RestoreMessages):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and data.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    if room not in messages:
        messages[room] = []
    # 合并去重（按内容+发送者+时间简单去重）
    existing = set()
    for m in messages[room]:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        existing.add(key)
    for m in data.messages:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        if key not in existing:
            messages[room].append(m)
            existing.add(key)
    # 限制长度
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    return {"ok": True}

# ----- 房间管理 -----
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
    messages[name] = []   # 初始化消息列表
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
    # 踢出所有在该房间的用户
    for user, room in list(online_users.items()):
        if room == name:
            online_users[user] = "main"   # 踢回客厅
    return {"ok": True}

@app.post("/api/current_room")
async def set_current_room(data: CurrentRoom):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and data.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    return {"ok": True}

# ----- 在线状态 -----
@app.post("/api/heartbeat")
async def heartbeat(data: Heartbeat):
    if data.name:
        online_users[data.name] = data.room or "main"
        # 清理超时（30秒无心跳视为离线）
        # 实际可以用定时任务，这里简单处理，由前端定期调用
        return {"ok": True}
    return {"ok": False}

@app.get("/api/online")
async def get_online():
    # 清理超过30秒未更新的用户（这里不自动清理，交给心跳超时机制）
    # 我们可以返回当前在线列表
    online_list = []
    now = time.time()
    # 由于没有存储心跳时间，我们简单返回所有在线用户（实际应该存时间）
    # 但为了演示，我们返回当前所有用户
    for name, room in online_users.items():
        online_list.append({"name": name, "room": room})
    return {"online": online_list}

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
    settings = time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})
    return {"settings": settings}

@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}
    return {"settings": time_settings[room]}

# ----- 删除成员 -----
@app.post("/api/remove_member")
async def remove_member(data: RemoveMember):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and data.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    if room in messages:
        messages[room] = [m for m in messages[room] if m["sender"] != data.name]
    # 如果该用户在线，将其移至客厅
    if data.name in online_users:
        online_users[data.name] = "main"
    return {"ok": True}

# ------------------- 启动 -------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
