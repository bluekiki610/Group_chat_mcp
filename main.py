from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import json
import time
from datetime import datetime, timedelta, timezone
import os

app = FastAPI()

if os.path.isdir("images"):
    app.mount("/images", StaticFiles(directory="images"), name="images")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  数据存储（内存 + 自动保存到文件 + 备份恢复）
# ============================================================
DATA_FILE = "data.json"

rooms: Dict[str, Dict] = {"main": {"name": "main", "has_password": False, "password": "", "creator": "system"}}
messages: Dict[str, List[Dict]] = {"main": []}
avatars: Dict[str, str] = {}
online_users: Dict[str, str] = {}
online_times: Dict[str, float] = {}
time_settings: Dict[str, Dict] = {}
active_room: Dict[str, str] = {"current": "main", "password": ""}

regions: Dict[str, Dict] = {}
buildings: Dict[str, Dict] = {}
npcs: Dict[str, List[Dict]] = {}
stories: Dict[str, List[Dict]] = {}
notes: Dict[str, List[Dict]] = {}
diaries: Dict[str, List[Dict]] = {}
room_bg: Dict[str, str] = {}
building_seq: int = 0
note_seq: int = 0

# 权限数据
room_access: Dict[str, List[str]] = {}  # 房间 -> 已授权名单
room_requests: Dict[str, List[Dict]] = {}  # 房间 -> 申请列表 [{applicant,time}]


def log(msg: str):
    print(f"[SAVE] {msg}", flush=True)


def collect_all_data() -> dict:
    return {
        "rooms": rooms,
        "messages": messages,
        "avatars": avatars,
        "time_settings": time_settings,
        "regions": regions,
        "buildings": buildings,
        "npcs": npcs,
        "stories": stories,
        "notes": notes,
        "diaries": diaries,
        "room_bg": room_bg,
        "building_seq": building_seq,
        "note_seq": note_seq,
        "room_access": room_access,
        "room_requests": room_requests,
    }


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(collect_all_data(), f, ensure_ascii=False)
    except Exception as e:
        print(f"[SAVE] 保存失败: {e}", flush=True)


def load_data():
    global building_seq, note_seq
    try:
        if not os.path.exists(DATA_FILE):
            return
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        rooms.update(data.get("rooms", {}))
        messages.update(data.get("messages", {}))
        avatars.update(data.get("avatars", {}))
        time_settings.update(data.get("time_settings", {}))
        regions.update(data.get("regions", {}))
        buildings.update(data.get("buildings", {}))
        npcs.update(data.get("npcs", {}))
        stories.update(data.get("stories", {}))
        notes.update(data.get("notes", {}))
        diaries.update(data.get("diaries", {}))
        room_bg.update(data.get("room_bg", {}))
        building_seq = data.get("building_seq", 0)
        note_seq = data.get("note_seq", 0)
        room_access.update(data.get("room_access", {}))
        room_requests.update(data.get("room_requests", {}))
        print(f"[SAVE] 已恢复数据（房间 {len(rooms)}，消息 {sum(len(v) for v in messages.values())} 条）", flush=True)
    except Exception as e:
        print(f"[SAVE] 加载失败: {e}", flush=True)


load_data()


# ============================================================
#  根路径返回 HTML 页面
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
#  辅助函数
# ============================================================
def get_current_time(room: str = "main") -> str:
    settings = time_settings.get(room, {"mode": "real"})
    now = datetime.now(timezone.utc) + timedelta(hours=8)
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


def find_building_of_room(room: str):
    """找到房间所属的建筑 id，没有则 None"""
    for bid, b in buildings.items():
        if room in b.get("rooms", []):
            return bid
    return None


def is_hall_room(room: str) -> bool:
    """会客厅（公共区域）判断"""
    info = rooms.get(room, {})
    if info.get("creator") == "hall":
        return True
    # 兼容：房间名以·会客厅结尾且属于建筑
    bid = find_building_of_room(room)
    if bid and room.endswith("·会客厅"):
        return True
    return False


def can_access_room(room: str, user: str) -> bool:
    """权限检查：能否进入房间（main/会客厅公开；主人/已授权可进私密）"""
    room = clean_room_name(room)
    if room in ("", "main"):
        return True
    if not room_exists(room):
        return False
    if is_hall_room(room):
        return True
    bid = find_building_of_room(room)
    if bid is None:
        return True  # 普通房间公开
    b = buildings[bid]
    if user and user == b.get("owner"):
        return True  # 主人
    if user and user in room_access.get(room, []):
        return True  # 已授权
    return False


def save_entry(sender: str, content: str, role: str, room: str = "main") -> dict:
    room = clean_room_name(room)
    entry = {"sender": sender, "content": content, "role": role, "time": get_current_time(room), "room": room}
    if room not in messages:
        messages[room] = []
    messages[room].append(entry)
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    active_room["current"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    save_data()
    return entry


def get_all_rooms() -> List[Dict]:
    result = []
    for name, info in rooms.items():
        result.append({"name": name, "has_password": info.get("has_password", False), "creator": info.get("creator", "system")})
    return result


def get_online_members() -> List[Dict]:
    now = time.time()
    to_remove = [n for n, t in online_times.items() if now - t > 30]
    for name in to_remove:
        online_users.pop(name, None)
        online_times.pop(name, None)
    return [{"name": n, "room": r} for n, r in online_users.items()]


def clean_room_name(room: str) -> str:
    return room.strip() if room else "main"


def next_bid() -> str:
    global building_seq
    building_seq += 1
    return f"b{building_seq}"


def add_note_to_room(room: str, author: str, text: str) -> dict:
    global note_seq
    note_seq += 1
    if room not in notes:
        notes[room] = []
    item = {"id": f"n{note_seq}", "author": author, "text": text, "time": get_current_time(room), "reply": None}
    notes[room].append(item)
    if len(notes[room]) > 200:
        notes[room] = notes[room][-200:]
    save_data()
    return item


def room_label(name: str) -> str:
    return "公共大厅" if name == "main" else name


def ensure_hall_room(bid: str):
    """为建筑自动创建会客厅"""
    b = buildings.get(bid)
    if not b:
        return
    hall = f"{b['name']}·会客厅"
    if hall not in rooms:
        rooms[hall] = {"name": hall, "has_password": False, "password": "", "creator": "hall"}
        messages[hall] = []
    if hall not in b["rooms"]:
        b["rooms"].insert(0, hall)


# ============================================================
#  Pydantic 模型
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


class RegionCreate(BaseModel):
    label: str
    x: float = 50
    y: float = 50
    image: str = ""


class RegionDelete(BaseModel):
    label: str


class BuildingCreate(BaseModel):
    name: str
    emoji: str = "🏠"
    type: str = "home"
    region: str = ""
    x: float = 50
    y: float = 50
    owner: str = ""


class BuildingMove(BaseModel):
    building_id: str
    x: float
    y: float


class BuildingDelete(BaseModel):
    building_id: str


class BuildingRename(BaseModel):
    building_id: str
    name: str
    emoji: str = ""


class BuildingRoomCreate(BaseModel):
    building_id: str
    name: str


class BuildingRoomDelete(BaseModel):
    building_id: str
    room: str


class RoomBg(BaseModel):
    room: str
    image: str


class NoteItem(BaseModel):
    room: str
    author: str
    text: str


class NoteReply(BaseModel):
    room: str
    note_id: str
    author: str
    text: str


class NpcCreate(BaseModel):
    building_id: str
    name: str
    emoji: str = "👤"
    desc: str = ""


class NpcDelete(BaseModel):
    building_id: str
    name: str


class StoryItem(BaseModel):
    building_id: str
    author: str
    text: str


class BackupData(BaseModel):
    rooms: Optional[Dict] = None
    messages: Optional[Dict] = None
    avatars: Optional[Dict] = None
    time_settings: Optional[Dict] = None
    regions: Optional[Dict] = None
    buildings: Optional[Dict] = None
    npcs: Optional[Dict] = None
    stories: Optional[Dict] = None
    notes: Optional[Dict] = None
    diaries: Optional[Dict] = None
    room_bg: Optional[Dict] = None
    building_seq: Optional[int] = 0
    note_seq: Optional[int] = 0
    room_access: Optional[Dict] = None
    room_requests: Optional[Dict] = None


class RoomApply(BaseModel):
    room: str
    applicant: str


class RoomGrant(BaseModel):
    room: str
    owner: str
    user: str
    allow: bool = True


class RoomRevoke(BaseModel):
    room: str
    owner: str
    user: str


# ============================================================
#  备份 / 恢复
# ============================================================
@app.get("/api/backup")
async def backup_data():
    return collect_all_data()


@app.post("/api/restore_backup")
async def restore_backup(data: BackupData):
    global building_seq, note_seq
    if data.rooms is not None: rooms.clear(); rooms.update(data.rooms)
    if data.messages is not None: messages.clear(); messages.update(data.messages)
    if data.avatars is not None: avatars.clear(); avatars.update(data.avatars)
    if data.time_settings is not None: time_settings.clear(); time_settings.update(data.time_settings)
    if data.regions is not None: regions.clear(); regions.update(data.regions)
    if data.buildings is not None: buildings.clear(); buildings.update(data.buildings)
    if data.npcs is not None: npcs.clear(); npcs.update(data.npcs)
    if data.stories is not None: stories.clear(); stories.update(data.stories)
    if data.notes is not None: notes.clear(); notes.update(data.notes)
    if data.diaries is not None: diaries.clear(); diaries.update(data.diaries)
    if data.room_bg is not None: room_bg.clear(); room_bg.update(data.room_bg)
    if data.room_access is not None: room_access.clear(); room_access.update(data.room_access)
    if data.room_requests is not None: room_requests.clear(); room_requests.update(data.room_requests)
    building_seq = data.building_seq or 0
    note_seq = data.note_seq or 0
    save_data()
    return {"ok": True, "msg": "数据已恢复！"}


# ============================================================
#  聊天 API（含权限校验）
# ============================================================
@app.post("/api/messages")
async def send_message(msg: Message):
    room = clean_room_name(msg.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, msg.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if not can_access_room(room, msg.sender):
        raise HTTPException(status_code=403, detail="🔒 这个房间需要主人同意才能进入，请先申请访问")
    if not msg.content.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    entry = save_entry(msg.sender, msg.content, msg.role, room)
    return {"ok": True, "time": entry["time"]}


@app.get("/api/messages")
async def get_messages(count: int = 200, room: str = "main", password: str = "", user: str = ""):
    room = clean_room_name(room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, password):
        raise HTTPException(status_code=403, detail="密码错误")
    if not can_access_room(room, user):
        raise HTTPException(status_code=403, detail="🔒 需要主人同意才能进入")
    msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))
    return {"messages": msgs[-count:], "room": room}


@app.post("/api/restore")
async def restore_messages(data: RestoreMessages):
    room = clean_room_name(data.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if not check_room_access(room, data.password):
        raise HTTPException(status_code=403, detail="密码错误")
    if not can_access_room(room, data.messages[-1].get("sender", "") if data.messages else ""):
        raise HTTPException(status_code=403, detail="🔒 需要主人同意才能进入")
    if room not in messages:
        messages[room] = []
    existing = {f"{m['sender']}|{m['content']}|{m['time']}" for m in messages[room]}
    for m in data.messages:
        key = f"{m['sender']}|{m['content']}|{m['time']}"
        if key not in existing:
            messages[room].append(m)
            existing.add(key)
    if len(messages[room]) > 500:
        messages[room] = messages[room][-500:]
    active_room["current"] = room
    active_room["password"] = get_room_password(room) if is_room_locked(room) else ""
    save_data()
    return {"ok": True}


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
    rooms[name] = {"name": name, "has_password": bool(data.password), "password": data.password, "creator": data.creator}
    messages[name] = []
    save_data()
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
        raise HTTPException(status_code=403, detail="不能删除公共大厅")
    if name not in rooms:
        raise HTTPException(status_code=404, detail="房间不存在")
    if is_room_locked(name) and data.password != get_room_password(name):
        raise HTTPException(status_code=403, detail="密码错误")
    del rooms[name]
    messages.pop(name, None)
    time_settings.pop(name, None)
    room_access.pop(name, None)
    room_requests.pop(name, None)
    for user, room in list(online_users.items()):
        if room == name:
            online_users[user] = "main"
    if active_room["current"] == name:
        active_room["current"] = "main"
        active_room["password"] = ""
    save_data()
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


@app.post("/api/avatar")
async def upload_avatar(data: AvatarUpload):
    if data.name:
        avatars[data.name] = data.image
        save_data()
        return {"ok": True}
    return {"ok": False}


@app.get("/api/avatar")
async def get_avatars():
    return {"avatars": avatars}


@app.get("/api/time_settings")
async def get_time_settings(room: str = "main"):
    room = clean_room_name(room)
    return {"settings": time_settings.get(room, {"mode": "real", "fixed_time": "19:00"})}


@app.post("/api/time_settings")
async def set_time_settings(data: TimeSettings):
    room = clean_room_name(data.room)
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if data.mode not in ("real", "fixed"):
        raise HTTPException(status_code=400, detail="mode 必须为 real 或 fixed")
    time_settings[room] = {"mode": data.mode, "fixed_time": data.fixed_time}
    save_data()
    return {"settings": time_settings[room]}


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
    save_data()
    return {"ok": True}


# ============================================================
#  地图 / 区域 / 建筑 API
# ============================================================
@app.get("/api/map")
async def get_map():
    return {
        "regions": regions,
        "buildings": buildings,
        "npcs": npcs,
        "room_bg": room_bg,
        "room_access": room_access,
        "room_requests": room_requests,
    }


@app.post("/api/map/region")
async def create_region(data: RegionCreate):
    label = data.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="区域名不能为空")
    regions[label] = {"label": label, "x": max(0, min(100, data.x)), "y": max(0, min(100, data.y)), "image": data.image}
    save_data()
    return {"ok": True}


@app.post("/api/map/region/delete")
async def delete_region(data: RegionDelete):
    label = data.label.strip()
    if label not in regions:
        raise HTTPException(status_code=404, detail="区域不存在")
    del regions[label]
    for bid, b in list(buildings.items()):
        if b.get("region") == label:
            b["region"] = ""
    save_data()
    return {"ok": True}


@app.post("/api/map/building")
async def create_building(data: BuildingCreate):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="建筑名不能为空")
    if data.type not in ("home", "npc"):
        raise HTTPException(status_code=400, detail="type 必须为 home 或 npc")
    if data.region and data.region not in regions:
        raise HTTPException(status_code=404, detail="区域不存在")
    bid = next_bid()
    buildings[bid] = {
        "name": name, "emoji": data.emoji or "🏠", "type": data.type,
        "region": data.region, "x": max(0, min(100, data.x)), "y": max(0, min(100, data.y)),
        "owner": data.owner, "rooms": []
    }
    ensure_hall_room(bid)  # 自动创建会客厅
    save_data()
    return {"ok": True, "building_id": bid}


@app.post("/api/map/building/move")
async def move_building(data: BuildingMove):
    if data.building_id not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    buildings[data.building_id]["x"] = max(0, min(100, data.x))
    buildings[data.building_id]["y"] = max(0, min(100, data.y))
    save_data()
    return {"ok": True}


@app.post("/api/map/building/rename")
async def rename_building(data: BuildingRename):
    if data.building_id not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    if data.name.strip():
        buildings[data.building_id]["name"] = data.name.strip()
    if data.emoji:
        buildings[data.building_id]["emoji"] = data.emoji
    ensure_hall_room(data.building_id)
    save_data()
    return {"ok": True}


@app.post("/api/map/building/delete")
async def delete_building(data: BuildingDelete):
    bid = data.building_id
    if bid not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    for room in buildings[bid].get("rooms", []):
        rooms.pop(room, None)
        messages.pop(room, None)
        room_bg.pop(room, None)
        notes.pop(room, None)
        diaries.pop(room, None)
        room_access.pop(room, None)
        room_requests.pop(room, None)
    buildings.pop(bid, None)
    npcs.pop(bid, None)
    stories.pop(bid, None)
    save_data()
    return {"ok": True}


@app.post("/api/map/room")
async def create_building_room(data: BuildingRoomCreate):
    name = data.name.strip()
    if data.building_id not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    if not name:
        raise HTTPException(status_code=400, detail="房间名不能为空")
    if name in rooms:
        raise HTTPException(status_code=400, detail="房间已存在")
    rooms[name] = {"name": name, "has_password": False, "password": "", "creator": "home"}
    messages[name] = []
    buildings[data.building_id]["rooms"].append(name)
    save_data()
    return {"ok": True, "room": name}


@app.post("/api/map/room/delete")
async def delete_building_room(data: BuildingRoomDelete):
    bid, room = data.building_id, data.room
    if bid not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    if room in buildings[bid].get("rooms", []):
        buildings[bid]["rooms"].remove(room)
    rooms.pop(room, None)
    messages.pop(room, None)
    room_bg.pop(room, None)
    room_access.pop(room, None)
    room_requests.pop(room, None)
    save_data()
    return {"ok": True}


@app.post("/api/room/bg")
async def set_room_bg(data: RoomBg):
    room = clean_room_name(data.room)
    room_bg[room] = data.image
    save_data()
    return {"ok": True}


# ============================================================
#  房间访问权限 API
# ============================================================
@app.post("/api/room/apply")
async def apply_room(data: RoomApply):
    room = clean_room_name(data.room)
    applicant = (data.applicant or "").strip()
    if not room_exists(room):
        raise HTTPException(status_code=404, detail="房间不存在")
    if can_access_room(room, applicant):
        return {"ok": True, "msg": "你已经有权限了"}
    if room not in room_requests:
        room_requests[room] = []
    # 去重
    room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != applicant]
    room_requests[room].append({"applicant": applicant, "time": get_current_time(room)})
    save_data()
    return {"ok": True, "msg": "申请已提交，等待主人同意"}


@app.get("/api/room/requests")
async def get_room_requests(room: str = ""):
    room = clean_room_name(room)
    return {"requests": room_requests.get(room, [])}


@app.post("/api/room/grant")
async def grant_room(data: RoomGrant):
    room = clean_room_name(data.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(status_code=400, detail="该房间不属于任何房子")
    if data.owner != buildings[bid].get("owner"):
        raise HTTPException(status_code=403, detail="只有主人才能同意")
    if data.allow:
        if room not in room_access:
            room_access[room] = []
        if data.user not in room_access[room]:
            room_access[room].append(data.user)
    # 从申请列表移除
    if room in room_requests:
        room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != data.user]
    save_data()
    return {"ok": True, "msg": "已" + ("同意" if data.allow else "拒绝") + " " + data.user + " 的访问"}


@app.post("/api/room/revoke")
async def revoke_room(data: RoomRevoke):
    room = clean_room_name(data.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(status_code=400, detail="该房间不属于任何房子")
    if data.owner != buildings[bid].get("owner"):
        raise HTTPException(status_code=403, detail="只有主人才能移除")
    if room in room_access:
        room_access[room] = [u for u in room_access[room] if u != data.user]
    save_data()
    return {"ok": True, "msg": "已移除 " + data.user + " 的访问权限"}


# ============================================================
#  便签墙（可回复一次）
# ============================================================
@app.get("/api/notes")
async def get_notes(room: str = "main"):
    room = clean_room_name(room)
    return {"notes": notes.get(room, [])}


@app.post("/api/notes")
async def add_note(data: NoteItem):
    room = clean_room_name(data.room)
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="内容不能为空")
    item = add_note_to_room(room, data.author or "匿名", data.text)
    return {"ok": True, "note": item}


@app.post("/api/notes/reply")
async def reply_note(data: NoteReply):
    room = clean_room_name(data.room)
    items = notes.get(room, [])
    for n in items:
        if n["id"] == data.note_id:
            if n.get("reply"):
                raise HTTPException(status_code=400, detail="这条便签已经回复过了")
            n["reply"] = {"author": data.author or "匿名", "text": data.text, "time": get_current_time(room)}
            save_data()
            return {"ok": True, "note": n}
    raise HTTPException(status_code=404, detail="便签不存在")


# ============================================================
#  NPC & 剧情
# ============================================================
@app.get("/api/npc")
async def get_npc(building_id: str):
    return {"npcs": npcs.get(building_id, [])}


@app.post("/api/npc")
async def add_npc(data: NpcCreate):
    if data.building_id not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    if not data.name.strip():
        raise HTTPException(status_code=400, detail="NPC 名字不能为空")
    if data.building_id not in npcs:
        npcs[data.building_id] = []
    npcs[data.building_id].append({"name": data.name.strip(), "emoji": data.emoji or "👤", "desc": data.desc})
    save_data()
    return {"ok": True}


@app.post("/api/npc/delete")
async def delete_npc(data: NpcDelete):
    if data.building_id in npcs:
        npcs[data.building_id] = [n for n in npcs[data.building_id] if n["name"] != data.name]
        save_data()
    return {"ok": True}


@app.get("/api/story")
async def get_story(building_id: str):
    return {"stories": stories.get(building_id, [])}


@app.post("/api/story")
async def add_story(data: StoryItem):
    if data.building_id not in buildings:
        raise HTTPException(status_code=404, detail="建筑不存在")
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="剧情内容不能为空")
    if data.building_id not in stories:
        stories[data.building_id] = []
    stories[data.building_id].append({"author": data.author or "神秘人", "text": data.text, "time": get_current_time()})
    if len(stories[data.building_id]) > 200:
        stories[data.building_id] = stories[data.building_id][-200:]
    save_data()
    return {"ok": True}


# ============================================================
#  MCP 接口
# ============================================================
def mcp_log(msg: str):
    print(f"[MCP] {msg}", flush=True)


@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(request: Request):
    if request.method == "GET":
        return JSONResponse(content={"jsonrpc": "2.0", "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "GroupChat", "version": "28.0.0"}}})
    try:
        body = await request.json()
    except Exception:
        mcp_log("⚠️ 请求不是合法 JSON")
        return JSONResponse(status_code=400, content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}})
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")
    mcp_log(f"收到请求: method={method}, id={request_id}")

    if method == "initialize":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "GroupChat", "version": "28.0.0"}}})

    if isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "ping":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {}})

    if method == "tools/list":
        mcp_log("→ 处理 tools/list")
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"tools": [
            {"name": "group_send_to_living_room", "description": "发送消息到公共大厅（公共区域）。当用户要求你留在公共大厅招待客人时使用此工具。注意：此工具没有 room 参数，永远发到公共大厅。", "inputSchema": {"type": "object", "properties": {"sender": {"type": "string", "description": "发送者名字"}, "content": {"type": "string", "description": "要发送的消息内容"}, "role": {"type": "string", "description": "填 'assistant'"}}, "required": ["sender", "content"]}},
            {"name": "group_send_message", "description": "发送消息到真人当前所在的房间（自动跟随）。AI 默认使用此工具，会自动跟随真人切换房间。真人所在房间无需申请。如果要去别人的私密房间，需先调用 group_apply_room_access 申请。", "inputSchema": {"type": "object", "properties": {"sender": {"type": "string", "description": "发送者名字"}, "content": {"type": "string", "description": "要发送的消息内容"}, "role": {"type": "string", "description": "填 'assistant'"}, "room": {"type": "string", "description": "可选，指定房间名。不填自动发送到真人当前所在房间"}}, "required": ["sender", "content"]}},
            {"name": "group_get_messages", "description": "获取聊天记录。可以获取所有房间的总览，也可以获取指定房间的详细消息。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string", "description": "可选，指定房间名"}, "count": {"type": "integer", "description": "获取数量，默认 10"}}}},
            {"name": "group_get_room_status", "description": "获取所有房间的活跃状态，用于 AI 决定去哪个房间串门。", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "group_get_current_room", "description": "查询真人当前在哪个房间，用于 AI 判断是否要跟过去。", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "group_get_rooms", "description": "获取所有房间列表（含密码状态）。", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "group_get_members", "description": "获取在线成员列表及他们所在的房间。", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "group_get_map", "description": "获取临空市地图：有哪些区域、建筑（住宅/NPC建筑）、NPC配置、每个建筑的会客厅和房间。想逛地图前先用这个了解情况。", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "group_walk_map", "description": "逛地图并触发小剧情！选择一个建筑（会客厅可以随便逛），写下你在这个建筑里发生的剧情。剧情会存进该建筑的剧情簿。你可以主动去逛。", "inputSchema": {"type": "object", "properties": {"building_id": {"type": "string", "description": "建筑 ID（用 group_get_map 查看）"}, "scene": {"type": "string", "description": "剧情内容"}, "sender": {"type": "string", "description": "你的名字"}}, "required": ["building_id", "scene"]}},
            {"name": "group_apply_room_access", "description": "申请进入某个私密房间（真人不在那里时）。提交后等主人同意。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string", "description": "要申请的房间名"}, "sender": {"type": "string", "description": "你的名字"}}, "required": ["room", "sender"]}},
            {"name": "group_read_story", "description": "查看某个建筑的剧情簿。", "inputSchema": {"type": "object", "properties": {"building_id": {"type": "string", "description": "建筑 ID"}}, "required": ["building_id"]}},
            {"name": "group_read_npc", "description": "查看某个建筑里有哪些 NPC。", "inputSchema": {"type": "object", "properties": {"building_id": {"type": "string", "description": "建筑 ID"}}, "required": ["building_id"]}},
            {"name": "group_write_note", "description": "在某个房间的便签墙上贴一张便签（小惊喜/留言），真人和 AI 都能看到。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string", "description": "房间名"}, "content": {"type": "string", "description": "便签内容"}, "sender": {"type": "string", "description": "你的名字"}}, "required": ["room", "content"]}},
            {"name": "group_reply_note", "description": "回复某房间便签墙上的一条便签（每条便签只能回复一次）。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string", "description": "房间名"}, "note_id": {"type": "string", "description": "便签 ID（用 group_get_notes 查看）"}, "content": {"type": "string", "description": "回复内容"}, "sender": {"type": "string", "description": "你的名字"}}, "required": ["room", "note_id", "content"]}},
            {"name": "group_get_notes", "description": "查看某个房间便签墙上的所有便签（含回复）。", "inputSchema": {"type": "object", "properties": {"room": {"type": "string", "description": "房间名"}}, "required": ["room"]}}
        ]}})

    if method == "tools/call":
        tool_name = params.get("name") or ""
        arguments = params.get("arguments", {})
        KNOWN = ["group_send_to_living_room", "group_send_message", "group_get_messages", "group_get_room_status",
                 "group_get_current_room", "group_get_rooms", "group_get_members", "group_get_map", "group_walk_map",
                 "group_apply_room_access", "group_read_story", "group_read_npc", "group_write_note", "group_reply_note", "group_get_notes"]
        if tool_name not in KNOWN:
            for k in KNOWN:
                if tool_name.endswith(k):
                    mcp_log(f"→ 工具名兼容转换: {tool_name} -> {k}")
                    tool_name = k
                    break
        mcp_log(f"→ 处理 tools/call: {tool_name}")

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
        elif tool_name == "group_get_map":
            return await mcp_get_map(request_id)
        elif tool_name == "group_walk_map":
            return await mcp_walk_map(arguments, request_id)
        elif tool_name == "group_apply_room_access":
            return await mcp_apply_room_access(arguments, request_id)
        elif tool_name == "group_read_story":
            return await mcp_read_story(arguments, request_id)
        elif tool_name == "group_read_npc":
            return await mcp_read_npc(arguments, request_id)
        elif tool_name == "group_write_note":
            return await mcp_write_note(arguments, request_id)
        elif tool_name == "group_reply_note":
            return await mcp_reply_note(arguments, request_id)
        elif tool_name == "group_get_notes":
            return await mcp_get_notes(arguments, request_id)
        else:
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}})

    mcp_log(f"→ 未知方法: {method}")
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method '{method}' not found"}})


# ============================================================
#  MCP 工具实现
# ============================================================
async def mcp_send_to_living_room(args: dict, request_id):
    sender = args.get("sender", "助手"); content = args.get("content", ""); role = args.get("role", "assistant")
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 消息不能为空"}]}})
    save_entry(sender, content, role, "main")
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"✅ 已发送到公共大厅：{content}"}]}})


async def mcp_send_message(args: dict, request_id):
    sender = args.get("sender", "助手"); content = args.get("content", ""); role = args.get("role", "assistant")
    room = args.get("room"); password = ""
    if not room:
        room = active_room.get("current", "main"); password = active_room.get("password", "")
    else:
        room = clean_room_name(room)
        if active_room.get("current") == room:
            password = active_room.get("password", "")
    if not room_exists(room):
        room = "main"
    if is_room_locked(room) and password != get_room_password(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」有密码，未授权发送。"}]}})
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 消息不能为空"}]}})
    # 权限：真人所在房间（跟随）自动允许；会客厅允许；否则检查授权，没有则拒绝
    if room != "main" and not can_access_room(room, sender):
        if active_room.get("current") == room:
            pass  # 真人就在这个房间，AI 跟随允许
        else:
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🔒 房间「{room}」需要主人同意才能进入。请先调用 group_apply_room_access 申请，等待主人同意后再发消息。"}]}})
    save_entry(sender, content, role, room)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"✅ 已发送到「{room_label(room)}」：{content}"}]}})


async def mcp_get_messages(args: dict, request_id):
    room = args.get("room"); count = args.get("count", 10)
    if room:
        room = clean_room_name(room)
        if not room_exists(room):
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": f"房间 '{room}' 不存在"}})
        msgs = sorted(messages.get(room, []), key=lambda x: x.get("time", ""))[-count:]
        text = f"📋 房间「{room_label(room)}」最近 {len(msgs)} 条消息：\n" + "\n".join([f"{m['sender']}: {m['content']} ({m['time']})" for m in msgs]) if msgs else f"📭 房间「{room_label(room)}」暂无消息"
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})
    result_text = "📋 所有房间消息总览：\n"
    for room_name, msgs in messages.items():
        room_label_name = room_label(room_name)
        if msgs:
            last_msgs = sorted(msgs, key=lambda x: x.get("time", ""))[-count:]
            result_text += f"\n🏠 {room_label_name}（{len(last_msgs)} 条）：\n" + "\n".join([f"  {m['sender']}: {m['content']} ({m['time']})" for m in last_msgs])
        else:
            result_text += f"\n🏠 {room_label_name}：暂无消息\n"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_room_status(request_id):
    online = get_online_members()
    result_text = "📊 房间活跃状态：\n"
    for room_name in rooms.keys():
        room_label_name = room_label(room_name)
        online_count = len([u for u in online if u["room"] == room_name])
        msg_count = len(messages.get(room_name, []))
        last_msg = messages.get(room_name, [])[-1] if messages.get(room_name) else None
        result_text += f"\n🏠 {room_label_name}：👤 在线 {online_count} 人 / 💬 {msg_count} 条" + (f" / 📝 {last_msg['sender']}: {last_msg['content']}" if last_msg else "")
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_current_room(request_id):
    current = active_room.get("current", "main")
    room_label_name = room_label(current)
    online = get_online_members()
    members_in_room = [u["name"] for u in online if u["room"] == current]
    result_text = f"📍 真人当前在：{room_label_name}\n👤 该房间在线成员：{', '.join(members_in_room) if members_in_room else '无'}\n\n💡 想跟随就用 group_send_message，想留公共大厅招待就用 group_send_to_living_room。"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_rooms(request_id):
    result_text = "📋 所有房间列表：\n"
    for room in get_all_rooms():
        label = room_label(room["name"])
        locked = "🔒 有密码" if room["has_password"] else "🔓 公开"
        result_text += f"\n🏠 {label}（{locked}）"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_get_members(request_id):
    online = get_online_members()
    if not online:
        result_text = "🟢 当前没有在线成员"
    else:
        result_text = "🟢 在线成员：\n" + "\n".join([f"\n👤 {m['name']}（在 {room_label(m['room'])}）" for m in online])
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


# ----- 地图 MCP -----
async def mcp_get_map(request_id):
    result_text = "🗺️ 临空市地图：\n\n📍 区域：\n"
    if not regions:
        result_text += "  暂无区域\n"
    for name, r in regions.items():
        result_text += f"  - {name}（位置 {r['x']:.0f}%, {r['y']:.0f}%" + ("，有分区图" if r.get("image") else "") + "）\n"
    result_text += "\n🏗️ 建筑：\n"
    if not buildings:
        result_text += "  暂无建筑\n"
    for bid, b in buildings.items():
        ntype = "🏠住宅" if b["type"] == "home" else "🏥公共建筑"
        owner = b.get("owner") or "?"
        rooms_list = b.get("rooms", [])
        room_desc = "，".join(rooms_list) if rooms_list else "无"
        ncount = len(npcs.get(bid, []))
        result_text += f"  [{bid}] {b['emoji']} {b['name']}（{ntype}，主人：{owner}，房间：{room_desc}，NPC {ncount} 个）\n"
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": result_text}]}})


async def mcp_walk_map(args: dict, request_id):
    bid = args.get("building_id", "")
    scene = args.get("scene", "")
    sender = args.get("sender", "神秘访客")
    if bid not in buildings:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 建筑不存在，先调用 group_get_map 查看建筑 ID"}]}})
    if not scene:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 剧情内容不能为空"}]}})
    b = buildings[bid]
    building_npcs = npcs.get(bid, [])
    if building_npcs:
        npc_names = "、".join([n["name"] for n in building_npcs])
        text = f"🏥 {b['name']}｜{sender} 来逛：\n{scene}\n（建筑内 NPC：{npc_names}）"
    else:
        text = f"🏠 {b['name']}｜{sender} 来逛：\n{scene}"
    if bid not in stories:
        stories[bid] = []
    stories[bid].append({"author": sender, "text": text, "time": get_current_time()})
    if len(stories[bid]) > 200:
        stories[bid] = stories[bid][-200:]
    save_data()
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"🎬 剧情已记录进「{b['name']}」的剧情簿！真人随时可以回看～"}]}})


async def mcp_apply_room_access(args: dict, request_id):
    room = clean_room_name(args.get("room", ""))
    sender = args.get("sender", "神秘人")
    if not room_exists(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在"}]}})
    if can_access_room(room, sender):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "✅ 你已经可以进入这个房间了"}]}})
    if room not in room_requests:
        room_requests[room] = []
    room_requests[room] = [r for r in room_requests[room] if r.get("applicant") != sender]
    room_requests[room].append({"applicant": sender, "time": get_current_time(room)})
    save_data()
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"📨 已向房间「{room}」的主人提交访问申请，等主人同意后你就能进去了！"}]}})


async def mcp_read_story(args: dict, request_id):
    bid = args.get("building_id", "")
    items = stories.get(bid, [])
    bname = buildings.get(bid, {}).get("name", bid)
    if not items:
        text = f"📭 「{bname}」的剧情簿还是空的"
    else:
        text = f"🎬 「{bname}」剧情簿（{len(items)} 条）：\n" + "\n".join([f"\n📅 {s['time']} {s['author']}：\n{s['text']}" for s in items])
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})


async def mcp_read_npc(args: dict, request_id):
    bid = args.get("building_id", "")
    items = npcs.get(bid, [])
    bname = buildings.get(bid, {}).get("name", bid)
    if not items:
        text = f"🏥 「{bname}」还没有配置 NPC"
    else:
        text = f"🏥 「{bname}」的 NPC：\n" + "\n".join([f"\n{n['emoji']} {n['name']}：{n['desc'] or '（无介绍）'}" for n in items])
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})


async def mcp_write_note(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    content = args.get("content", "")
    sender = args.get("sender", "神秘人")
    if not room_exists(room):
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ 房间「{room}」不存在，无法贴便签"}]}})
    if not content:
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 便签内容不能为空"}]}})
    item = add_note_to_room(room, sender, content)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"💌 便签已贴上「{room}」的便签墙（ID: {item['id']}）！"}]}})


async def mcp_reply_note(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    note_id = args.get("note_id", "")
    content = args.get("content", "")
    sender = args.get("sender", "神秘人")
    for n in notes.get(room, []):
        if n["id"] == note_id:
            if n.get("reply"):
                return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 这条便签已经回复过了"}]}})
            if not content:
                return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 回复内容不能为空"}]}})
            n["reply"] = {"author": sender, "text": content, "time": get_current_time(room)}
            save_data()
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "💬 回复成功！"}]}})
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "❌ 便签不存在"}]}})


async def mcp_get_notes(args: dict, request_id):
    room = clean_room_name(args.get("room", "main"))
    items = notes.get(room, [])
    if not items:
        text = f"📭 「{room}」的便签墙是空的"
    else:
        lines = []
        for n in items:
            line = f"· [{n['id']}] {n['author']}：{n['text']}（{n['time']}）"
            if n.get("reply"):
                line += f"\n    ↳ 💬 {n['reply']['author']}：{n['reply']['text']}（{n['reply']['time']}）"
            lines.append(line)
        text = f"📌 「{room}」便签墙（{len(items)} 条）：\n" + "\n".join(lines)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}]}})


# ============================================================
#  启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
