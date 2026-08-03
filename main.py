from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse  # ← 新增 HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
import asyncio
from datetime import datetime
import random
import os  # ← 新增

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
#  ⭐ 关键修改：根路径返回 HTML 页面（聊天界面）
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
#  以下所有 /api 路径保持不变（返回 JSON 数据）
# ============================================================

# ------------------- 数据存储（内存） -------------------
messages: Dict[str, List[Dict]] = {"main": []}
rooms: Dict[str, Dict] = {"main": {"name": "main", "has_password": False, "password": "", "creator": "system"}}
online_users: Dict[str, str] = {}
avatars: Dict[str, str] = {}
time_settings: Dict[str, Dict] = {}

# ------------------- 辅助函数 -------------------
def get_current_time(room: str = "main") -> str:
    settings = time_settings.get(room, {"mode": "real"})
    if settings.get("mode") == "fixed":
        return settings.get("fixed_time", "19:00")
    else:
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
    room = msg.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and msg.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    
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
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    
    # 触发 AI 自动回复（可替换成你的 MCP 工具）
    if msg.role != "assistant" and msg.sender != "助手":
        asyncio.create_task(auto_reply(msg.sender, msg.content, room))
    
    return {"ok": True, "time": msg_time}

async def auto_reply(sender: str, content: str, room: str):
    await asyncio.sleep(1)
    replies = ["我听到啦！", "嗯嗯，有道理～", "好有意思！", "继续说吧，我在听。", "我会记住的。"]
    reply_text = random.choice(replies)
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
    return {"ok": True}

@app.post("/api/current_room")
async def set_current_room(data: CurrentRoom):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and data.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    return {"ok": True}

@app.post("/api/heartbeat")
async def heartbeat(data: Heartbeat):
    if data.name:
        online_users[data.name] = data.room or "main"
        return {"ok": True}
    return {"ok": False}

@app.get("/api/online")
async def get_online():
    online_list = []
    for name, room in online_users.items():
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
    settings = time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})
    return {"settings": settings}

@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}
    return {"settings": time_settings[room]}

@app.post("/api/remove_member")
async def remove_member(data: RemoveMember):
    room = data.room or "main"
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(room) and data.password != get_room_password(room):
        raise HTTPException(status_code=403, detail="密码错误")
    if room in messages:
        messages[room] = [m for m in messages[room] if m["sender"] != data.name]
    if data.name in online_users:
        online_users[data.name] = "main"
    return {"ok": True}

# ------------------- 启动 -------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
