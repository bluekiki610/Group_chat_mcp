import os
import json
import time
import random
import threading
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from starlette.routing import Mount

try:
    from mcp.server.fastmcp import FastMCP
except Exception:
    from fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data.json"
SNAPSHOT_DIR = BASE_DIR / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)

# ========== 数据 ==========
data = {}
def default_data():
    return {
        "messages": {}, "rooms": {}, "avatars": {}, "online": {},
        "active_room": {"current": "main"}, "room_bg": {},
        "regions": {}, "buildings": {}, "npcs": {},
        "room_access": {}, "room_requests": {}, "user_ais": {},
        "time_settings": {}, "building_seq": 1, "note_seq": 1,
        "stories": {}, "notes": {}, "diaries": {},
        "edit_pwd": "", "edit_locked": False,
        "wallets": {}, "home_jobs": {}, "work_sessions": {},
        "work_history": [], "work_switch": {}, "visits": {},
        "sms": {}, "trails": {}, "messages_cache": {},
    }

def sanitize_data():
    """把旧版 data.json 的数据自动规整成新格式，防止接口崩溃。"""
    try:
        data["messages"] = {r: [m for m in ms if isinstance(m, dict)] for r, ms in data.get("messages", {}).items()}
        for name in list(data.get("rooms", {}).keys()):
            r = data["rooms"][name]
            if not isinstance(r, dict):
                data["rooms"][name] = {"creator": "?", "has_password": False, "password": "", "created": now_str(), "description": ""}
            else:
                r.setdefault("creator", "?"); r.setdefault("has_password", False); r.setdefault("password", ""); r.setdefault("created", now_str()); r.setdefault("description", "")
        data["work_history"] = [h for h in data.get("work_history", []) if isinstance(h, dict)]
        data["wallets"] = {k: (v if isinstance(v, (int, float)) else 0) for k, v in data.get("wallets", {}).items()}
        data["work_sessions"] = {k: v for k, v in data.get("work_sessions", {}).items() if isinstance(v, dict)}
        data["home_jobs"] = {k: v for k, v in data.get("home_jobs", {}).items() if isinstance(v, str)}
        data["work_switch"] = {k: bool(v) for k, v in data.get("work_switch", {}).items()}
        data["avatars"] = {k: (v if isinstance(v, str) else "") for k, v in data.get("avatars", {}).items()}
        data["user_ais"] = {k: (v if isinstance(v, list) else []) for k, v in data.get("user_ais", {}).items()}
        data["regions"] = {k: (v if isinstance(v, dict) else {}) for k, v in data.get("regions", {}).items()}
        data["buildings"] = {k: (v if isinstance(v, dict) else {}) for k, v in data.get("buildings", {}).items()}
        data["npcs"] = {k: (v if isinstance(v, list) else []) for k, v in data.get("npcs", {}).items()}
        for key in ["notes", "diaries", "stories", "sms", "trails", "visits", "room_access", "room_requests", "room_bg", "time_settings"]:
            v = data.get(key)
            if isinstance(v, dict):
                for k2 in list(v.keys()):
                    if not isinstance(v[k2], list):
                        v[k2] = []
        if not isinstance(data.get("active_room"), dict):
            data["active_room"] = {"current": "main"}
        data["active_room"].setdefault("current", "main")
        if not isinstance(data.get("rooms"), dict):
            data["rooms"] = {}
        data["rooms"].setdefault("main", {"creator": "system", "has_password": False, "password": "", "created": now_str(), "description": "城市的公共大厅，所有人都在这里聊天。"})
    except Exception:
        pass

def load_data():
    global data
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = default_data()
    else:
        data = default_data()
    for k, v in default_data().items():
        data.setdefault(k, v)
    sanitize_data()

def save_data():
    try:
        if DATA_FILE.exists():
            shutil.copyfile(DATA_FILE, str(DATA_FILE) + ".bak")
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass

def snapshot():
    try:
        name = "auto_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
        (SNAPSHOT_DIR / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        files = sorted(SNAPSHOT_DIR.glob("auto_*.json"))
        for f in files[:-20]:
            f.unlink(missing_ok=True)
    except Exception:
        pass

# ========== 工具函数 ==========
def clean_room_name(name: str) -> str:
    return name.strip()
def room_exists(name: str) -> bool:
    return name in data["rooms"]
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def room_time(room: str) -> str:
    try:
        s = data["time_settings"].get(room) or data["time_settings"].get("main") or {}
        if s.get("mode") == "fixed" and s.get("fixed_time"):
            return s["fixed_time"]
    except Exception:
        pass
    return now_str()

def is_ai_of(owner: str, name: str) -> bool:
    return name in data["user_ais"].get(owner, [])

def find_building_of_room(room: str):
    for bid, b in data["buildings"].items():
        if room in b.get("rooms", []):
            return bid
    return None

def can_access_room(room: str, user: str) -> bool:
    if room == "main":
        return True
    bid = find_building_of_room(room)
    if bid is None:
        return True
    b = data["buildings"][bid]
    if b.get("type") == "npc":
        return True
    if room.endswith("·会客厅"):
        return True
    if b.get("owner") == user:
        return True
    acc = data["room_access"].get(room, [])
    if user in acc:
        return True
    for granted in acc:
        if is_ai_of(granted, user):
            return True
    return False

def can_view_room(room: str, user: str) -> bool:
    if can_access_room(room, user):
        return True
    return False

# ========== 模型 ==========
class MessageIn(BaseModel): sender: str; content: str; role: str = "user"; room: str = "main"; password: str = ""
class RoomCreate(BaseModel): name: str; password: str = ""; creator: str = ""
class RoomJoin(BaseModel): name: str; password: str = ""
class RoomDelete(BaseModel): name: str; password: str = ""
class RemoveMember(BaseModel): name: str; room: str = "main"; password: str = ""
class DeleteMsg(BaseModel): room: str; sender: str; content: str; time: str; user: str
class RestoreIn(BaseModel): messages: list; room: str = "main"; password: str = ""
class NameIn(BaseModel): name: str; image: str = ""
class AvatarIn(BaseModel): name: str; image: str
class HeartIn(BaseModel): name: str; room: str = "main"
class RoomNameIn(BaseModel): room: str; password: str = ""
class RegionIn(BaseModel): label: str; x: float; y: float; image: str = ""
class RegionDel(BaseModel): label: str
class BuildingIn(BaseModel): name: str; emoji: str; type: str; region: str = ""; x: float; y: float; owner: str = ""; description: str = ""
class BuildingRename(BaseModel): building_id: str; name: str
class BuildingDesc(BaseModel): building_id: str; description: str
class BuildingFeatures(BaseModel): building_id: str; features: list = []; salary: float = 0
class BuildingNotice(BaseModel): building_id: str; notice: str = ""
class BuildingDel(BaseModel): building_id: str
class BuildingRoomIn(BaseModel): building_id: str; name: str
class RoomDescIn(BaseModel): room: str; description: str
class RoomBgIn(BaseModel): room: str; image: str
class NpcIn(BaseModel): building_id: str; name: str; emoji: str = "👤"; desc: str = ""
class NpcEdit(BaseModel): building_id: str; name: str; new_name: str; emoji: str; desc: str
class NpcDel(BaseModel): building_id: str; name: str
class NoteIn(BaseModel): room: str; author: str; text: str
class DiaryComment(BaseModel): room: str; index: int; author: str; text: str
class StoryIn(BaseModel): building_id: str; author: str; text: str
class RoomApply(BaseModel): room: str; applicant: str
class GrantIn(BaseModel): room: str; owner: str; user: str; allow: bool = True
class RevokeIn(BaseModel): room: str; owner: str; user: str
class UserAisIn(BaseModel): user: str; ais: list = []
class EditPwdIn(BaseModel): pwd: str
class TimeSet(BaseModel): mode: str = "real"; fixed_time: str = ""; room: str = "main"
class SummonIn(BaseModel): ai: str; room: str = "main"
class WorkStart(BaseModel): name: str; building_id: str; hours: int = 2
class WorkStop(BaseModel): name: str
class WorkAuto(BaseModel): name: str
class WorkSwitch(BaseModel): name: str; on: bool = True
class HomeJobIn(BaseModel): user: str = ""; ai: str = ""; building_id: str
class SmsIn(BaseModel): sender: str; to: str; text: str

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

# ========== 基础 ==========
@app.get("/")
async def root(): return FileResponse(BASE_DIR / "index.html")

@app.get("/api/health")
async def health(): return {"ok": True, "rooms": len(data["rooms"])}

@app.get("/api/messages")
async def get_messages(room: str = "main", password: str = "", user: str = ""):
    room = clean_room_name(room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][room]
    if r.get("has_password") and r.get("password") != password:
        raise HTTPException(403, "密码错误")
    if not can_view_room(room, user):
        raise HTTPException(403, "没有权限查看这个房间")
    msgs = data["messages"].get(room, [])
    return {"messages": msgs[-300:]}

@app.post("/api/messages")
async def send_message(m: MessageIn):
    room = clean_room_name(m.room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][room]
    if r.get("has_password") and r.get("password") != m.password:
        raise HTTPException(403, "密码错误")
    if not can_view_room(room, m.sender):
        raise HTTPException(403, "没有权限进入这个房间")
    content = m.content.strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    msg = {"sender": m.sender, "content": content[:1000], "role": m.role, "time": room_time(room)}
    data["messages"].setdefault(room, []).append(msg)
    data["active_room"]["current"] = room
    save_data()
    return {"ok": True, "time": msg["time"], "count": len(data["messages"][room])}

@app.post("/api/messages/delete")
async def delete_message(dm: DeleteMsg):
    room = clean_room_name(dm.room)
    if not room_exists(room):
        raise HTTPException(404, "房间不存在")
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "只有房主可以删除房间消息")
    if data["buildings"][bid].get("owner") != dm.user:
        raise HTTPException(403, "只有房主可以删除")
    before = len(data["messages"].get(room, []))
    data["messages"][room] = [m for m in data["messages"].get(room, [])
                               if not (m.get("sender") == dm.sender and m.get("content") == dm.content and m.get("time") == dm.time)]
    save_data()
    return {"ok": True, "deleted": before - len(data["messages"].get(room, []))}

@app.post("/api/restore")
async def restore_messages(r: RestoreIn):
    room = clean_room_name(r.room)
    if not room_exists(room):
        return {"ok": False, "reason": "no_room"}
    rr = data["rooms"][room]
    if rr.get("has_password") and rr.get("password") != r.password:
        return {"ok": False, "reason": "bad_pwd"}
    cur = data["messages"].get(room, [])
    got = [m for m in r.messages if isinstance(m, dict) and m.get("sender") != "system"]
    cur_senders = {(m.get("sender"), m.get("content"), m.get("time")) for m in cur}
    added = 0
    for m in got:
        key = (m.get("sender"), m.get("content"), m.get("time"))
        if key not in cur_senders:
            cur.append({"sender": m.get("sender", "?"), "content": m.get("content", ""), "role": m.get("role", "user"), "time": m.get("time", now_str())})
            cur_senders.add(key)
            added += 1
    data["messages"][room] = cur[-500:]
    save_data()
    return {"ok": True, "added": added}

# ========== 房间 ==========
@app.get("/api/rooms")
async def get_rooms():
    out = []
    for name, r in data["rooms"].items():
        out.append({"name": name, "creator": r.get("creator", ""), "has_password": bool(r.get("has_password")), "description": r.get("description", ""), "created": r.get("created", "")})
    return {"rooms": out}

@app.post("/api/rooms/create")
async def create_room(rc: RoomCreate):
    name = clean_room_name(rc.name)
    if not name:
        raise HTTPException(400, "房间名字不能为空")
    if room_exists(name):
        raise HTTPException(400, "房间已存在")
    data["rooms"][name] = {"creator": rc.creator, "has_password": bool(rc.password), "password": rc.password, "created": now_str(), "description": ""}
    data["messages"].setdefault(name, [])
    save_data()
    return {"ok": True, "room": name}

@app.post("/api/rooms/join")
async def join_room(rj: RoomJoin):
    name = clean_room_name(rj.name)
    if not room_exists(name):
        raise HTTPException(404, "房间不存在")
    r = data["rooms"][name]
    if r.get("has_password") and r.get("password") != rj.password:
        raise HTTPException(403, "密码错误")
    return {"ok": True, "room": name}

@app.post("/api/rooms/delete")
async def delete_room(rd: RoomDelete):
    name = clean_room_name(rd.name)
    if not room_exists(name):
        return {"ok": False}
    r = data["rooms"][name]
    if r.get("creator") == "system":
        raise HTTPException(403, "不能删除公共大厅")
    if r.get("has_password") and r.get("password") != rd.password:
        raise HTTPException(403, "密码错误")
    data["messages"].pop(name, None)
    data["room_bg"].pop(name, None)
    data.pop(name, None)
    data["rooms"].pop(name, None)
    save_data()
    return {"ok": True}

@app.post("/api/remove_member")
async def remove_member(rm: RemoveMember):
    name = clean_room_name(rm.room)
    if not room_exists(name):
        return {"ok": False}
    r = data["rooms"][name]
    if r.get("has_password") and r.get("password") != rm.password:
        raise HTTPException(403, "密码错误")
    data["messages"][name] = [m for m in data["messages"].get(name, []) if m.get("sender") != rm.name]
    save_data()
    return {"ok": True}

@app.post("/api/current_room")
async def current_room(rn: RoomNameIn):
    data["active_room"]["current"] = clean_room_name(rn.room)
    save_data()
    return {"ok": True}

# ========== 在线 ==========
@app.post("/api/heartbeat")
async def heartbeat(h: HeartIn):
    name = h.name.strip()
    data["online"][name] = {"time": time.time(), "room": clean_room_name(h.room)}
    data["online"] = {k: v for k, v in data["online"].items() if time.time() - v.get("time", 0) < 45}
    return {"ok": True}

@app.get("/api/online")
async def get_online():
    data["online"] = {k: v for k, v in data["online"].items() if time.time() - v.get("time", 0) < 45}
    return {"online": [{"name": k, "room": v.get("room", "main")} for k, v in data["online"].items()]}

# ========== 头像 ==========
@app.get("/api/avatar")
async def get_avatar(): return {"avatars": data["avatars"]}

@app.post("/api/avatar")
async def set_avatar(a: AvatarIn):
    data["avatars"][a.name] = a.image
    save_data()
    return {"ok": True}

# ========== 地图 ==========
@app.get("/api/map")
async def get_map():
    return {
        "regions": data["regions"], "buildings": data["buildings"], "npcs": data["npcs"],
        "room_bg": data["room_bg"], "rooms": data["rooms"],
        "room_access": data["room_access"], "room_requests": data["room_requests"],
        "user_ais": data["user_ais"], "work_sessions": data["work_sessions"],
        "home_jobs": data["home_jobs"],
    }

@app.post("/api/map/region")
async def add_region(r: RegionIn):
    data["regions"][r.label] = {"x": r.x, "y": r.y, "image": r.image}
    save_data()
    return {"ok": True}

@app.post("/api/map/region/delete")
async def del_region(r: RegionDel):
    data["regions"].pop(r.label, None)
    save_data()
    return {"ok": True}

@app.post("/api/map/building")
async def add_building(b: BuildingIn):
    bid = "b" + str(data["building_seq"])
    data["building_seq"] += 1
    data["buildings"][bid] = {
        "name": b.name, "emoji": b.emoji, "type": b.type, "region": b.region,
        "x": b.x, "y": b.y, "owner": b.owner, "description": b.description,
        "rooms": [], "salary": 0, "features": [], "notice": "",
    }
    if b.type == "home":
        hall = b.name + "·会客厅"
        data["rooms"][hall] = {"creator": "hall", "has_password": False, "password": "", "created": now_str(), "description": ""}
        data["messages"].setdefault(hall, [])
        data["buildings"][bid]["rooms"].append(hall)
    save_data()
    return {"ok": True, "building_id": bid}

@app.post("/api/map/building/rename")
async def rename_building(r: BuildingRename):
    b = data["buildings"].get(r.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["name"] = r.name
    save_data()
    return {"ok": True}

@app.post("/api/map/building/desc")
async def building_desc(d: BuildingDesc):
    b = data["buildings"].get(d.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["description"] = d.description
    save_data()
    return {"ok": True}

@app.post("/api/map/building/features")
async def building_features(f: BuildingFeatures):
    b = data["buildings"].get(f.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["features"] = f.features
    b["salary"] = f.salary
    save_data()
    return {"ok": True, "msg": "功能已设置"}

@app.post("/api/map/building/notice")
async def building_notice(n: BuildingNotice):
    b = data["buildings"].get(n.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    b["notice"] = n.notice
    save_data()
    return {"ok": True, "msg": "公告已更新"}

@app.post("/api/map/building/delete")
async def del_building(d: BuildingDel):
    b = data["buildings"].pop(d.building_id, None)
    if b:
        for r in b.get("rooms", []):
            data["rooms"].pop(r, None)
            data["messages"].pop(r, None)
            data["room_bg"].pop(r, None)
            data["room_access"].pop(r, None)
            data["room_requests"].pop(r, None)
        data["npcs"].pop(d.building_id, None)
        data["stories"].pop(d.building_id, None)
    save_data()
    return {"ok": True}

@app.post("/api/map/room")
async def add_room(r: BuildingRoomIn):
    b = data["buildings"].get(r.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    room = r.name.strip()
    if room in b.get("rooms", []):
        raise HTTPException(400, "房间已存在")
    data["rooms"][room] = {"creator": "home", "has_password": False, "password": "", "created": now_str(), "description": ""}
    data["messages"].setdefault(room, [])
    b.setdefault("rooms", []).append(room)
    save_data()
    return {"ok": True, "room": room}

@app.post("/api/room/desc")
async def room_desc(d: RoomDescIn):
    room = clean_room_name(d.room)
    if room in data["rooms"]:
        data["rooms"][room]["description"] = d.description
    save_data()
    return {"ok": True}

@app.post("/api/room/bg")
async def room_bg(b: RoomBgIn):
    data["room_bg"][b.room] = b.image
    save_data()
    return {"ok": True}

# ========== NPC ==========
@app.post("/api/npc")
async def add_npc(n: NpcIn):
    data["npcs"].setdefault(n.building_id, []).append({"name": n.name, "emoji": n.emoji, "desc": n.desc})
    save_data()
    return {"ok": True}

@app.post("/api/npc/edit")
async def edit_npc(n: NpcEdit):
    for npc in data["npcs"].get(n.building_id, []):
        if npc["name"] == n.name:
            npc["name"] = n.new_name
            npc["emoji"] = n.emoji
            npc["desc"] = n.desc
            break
    save_data()
    return {"ok": True}

@app.post("/api/npc/delete")
async def del_npc(n: NpcDel):
    data["npcs"][n.building_id] = [x for x in data["npcs"].get(n.building_id, []) if x["name"] != n.name]
    save_data()
    return {"ok": True}

# ========== 便签/日记/剧情 ==========
@app.get("/api/notes")
async def get_notes(room: str):
    return {"notes": data["notes"].get(room, [])}

@app.post("/api/notes")
async def add_note(n: NoteIn):
    data["notes"].setdefault(n.room, []).append({"author": n.author, "text": n.text[:500], "time": now_str()})
    save_data()
    return {"ok": True}

@app.get("/api/diaries")
async def get_diaries(room: str):
    return {"diaries": data["diaries"].get(room, [])}

@app.post("/api/diaries")
async def add_diary(n: NoteIn):
    data["diaries"].setdefault(n.room, []).append({"author": n.author, "text": n.text[:1000], "time": now_str()})
    save_data()
    return {"ok": True}

@app.post("/api/diaries/comment")
async def comment_diary(c: DiaryComment):
    items = data["diaries"].get(c.room, [])
    if 0 <= c.index < len(items):
        items[c.index]["comment"] = {"author": c.author, "text": c.text[:300]}
        save_data()
    return {"ok": True}

@app.get("/api/story")
async def get_story(building_id: str):
    return {"stories": data["stories"].get(building_id, [])}

@app.post("/api/story")
async def add_story(s: StoryIn):
    data["stories"].setdefault(s.building_id, []).append({"author": s.author, "text": s.text[:1500], "time": now_str()})
    save_data()
    return {"ok": True}

# ========== 房间权限 ==========
@app.post("/api/room/apply")
async def room_apply(a: RoomApply):
    room = clean_room_name(a.room)
    reqs = data["room_requests"].setdefault(room, [])
    if not any(q.get("applicant") == a.applicant for q in reqs):
        reqs.append({"applicant": a.applicant, "time": now_str()})
        save_data()
        return {"ok": True, "msg": "申请已提交，等主人同意吧～"}
    return {"ok": True, "msg": "你已经申请过了，等主人同意～"}

@app.post("/api/room/grant")
async def room_grant(g: GrantIn):
    room = clean_room_name(g.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "房间不存在于建筑中")
    if data["buildings"][bid].get("owner") != g.owner:
        raise HTTPException(403, "只有房主可以授权")
    acc = data["room_access"].setdefault(room, [])
    if g.allow:
        if g.user not in acc:
            acc.append(g.user)
        for ai in data["user_ais"].get(g.user, []):
            if ai not in acc:
                acc.append(ai)
    else:
        data["room_requests"].pop(room, None)
        data["room_access"][room] = [u for u in acc if u != g.user and not is_ai_of(g.user, u)]
    save_data()
    return {"ok": True}

@app.post("/api/room/revoke")
async def room_revoke(r: RevokeIn):
    room = clean_room_name(r.room)
    bid = find_building_of_room(room)
    if bid is None:
        raise HTTPException(403, "房间不存在于建筑中")
    if data["buildings"][bid].get("owner") != r.owner:
        raise HTTPException(403, "只有房主可以移除")
    acc = data["room_access"].get(room, [])
    data["room_access"][room] = [u for u in acc if u != r.user and not is_ai_of(r.user, u)]
    save_data()
    return {"ok": True}

# ========== 用户 AI / 编辑权限 ==========
@app.post("/api/user_ais")
async def user_ais(u: UserAisIn):
    data["user_ais"][u.user] = u.ais
    save_data()
    return {"ok": True}

@app.get("/api/edit_status")
async def edit_status():
    return {"locked": bool(data["edit_locked"])}

@app.post("/api/set_edit_pwd")
async def set_edit_pwd(p: EditPwdIn):
    data["edit_pwd"] = p.pwd
    data["edit_locked"] = True
    save_data()
    return {"ok": True, "locked": True}

@app.post("/api/check_edit_pwd")
async def check_edit_pwd(p: EditPwdIn):
    return {"ok": data["edit_pwd"] == p.pwd}

# ========== 时间 ==========
@app.get("/api/time_settings")
async def get_time(room: str = "main"):
    return {"settings": data["time_settings"].get(room, {})}

@app.post("/api/time_settings")
async def set_time(t: TimeSet):
    data["time_settings"][t.room] = {"mode": t.mode, "fixed_time": t.fixed_time}
    save_data()
    return {"ok": True}

# ========== 召唤/轨迹 ==========
@app.post("/api/summon")
async def summon(s: SummonIn):
    room = clean_room_name(s.room)
    msg = {"sender": "system", "content": f"📣 有人召唤 {s.ai}！快来 {room} 看看～", "role": "system", "time": room_time(room)}
    data["messages"].setdefault(room, []).append(msg)
    save_data()
    return {"ok": True, "msg": f"已召唤 {s.ai}！"}

@app.get("/api/trails")
async def get_trails(user: str):
    items = data["trails"].get(user, [])
    return {"trails": items[-30:]}

def add_trail(user: str, text: str, room: str = "", tab: str = ""):
    if not user or user == "system":
        return
    data["trails"].setdefault(user, []).append({"time": now_str(), "text": text[:200], "room": room, "tab": tab})
    data["trails"][user] = data["trails"][user][-40:]

# ========== 经济/工作 ==========
def pay_work(name: str, building_id: str, hours: int):
    b = data["buildings"].get(building_id)
    if not b:
        return
    earn = b.get("salary", 0) * hours
    data["wallets"][name] = data["wallets"].get(name, 0) + earn
    data["work_history"].append({"name": name, "building": b.get("name", "?"), "hours": hours, "earn": earn, "time": now_str()})
    data["work_history"] = data["work_history"][-200:]
    data["work_sessions"].pop(name, None)
    save_data()

def work_tick():
    while True:
        try:
            now = time.time()
            for name in list(data["work_sessions"].keys()):
                s = data["work_sessions"][name]
                if now >= s["start_ts"] + s["hours"] * 3600:
                    pay_work(name, s["building_id"], s["hours"])
            for name, on in list(data["work_switch"].items()):
                if not on:
                    continue
                if name in data["work_sessions"]:
                    continue
                auto_start_work(name)
        except Exception:
            pass
        time.sleep(30)

def auto_start_work(name: str):
    try:
        candidates = [bid for bid, b in data["buildings"].items()
                      if b.get("type") == "npc" and "work" in b.get("features", []) and b.get("salary", 0) > 0]
        if not candidates:
            return
        home = data["home_jobs"].get(name)
        pick = None
        if home:
            home_bids = [bid for bid in candidates if data["buildings"][bid].get("name") == home]
            if home_bids and random.random() < 0.8:
                pick = random.choice(home_bids)
        if not pick:
            pick = random.choice(candidates)
        data["work_sessions"][name] = {"building_id": pick, "start_ts": time.time(), "hours": 2, "started_at": now_str()}
        add_trail(name, f"去 {data['buildings'][pick].get('name')} 上班了")
        save_data()
    except Exception:
        pass

@app.get("/api/economy")
async def economy(user: str):
    try:
        w = data["work_sessions"].get(user)
        my_h = [h for h in data["work_history"] if h.get("name") == user]
        return {"wallet": data["wallets"].get(user, 0), "working": w,
                "home_jobs": {k: v for k, v in data["home_jobs"].items() if k == user or is_ai_of(user, k)},
                "my_history": my_h}
    except Exception:
        return {"wallet": 0, "working": None, "home_jobs": {}, "my_history": []}

@app.post("/api/work/start")
async def work_start(ws: WorkStart):
    b = data["buildings"].get(ws.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    if b.get("type") != "npc" or "work" not in b.get("features", []) or b.get("salary", 0) <= 0:
        raise HTTPException(403, "这个建筑不能工作")
    data["work_sessions"][ws.name] = {"building_id": ws.building_id, "start_ts": time.time(), "hours": max(1, min(8, ws.hours)), "started_at": now_str()}
    data["work_switch"][ws.name] = True
    add_trail(ws.name, f"开始在 {b.get('name')} 上班")
    save_data()
    return {"ok": True, "msg": f"{ws.name} 开始在 {b.get('name')} 上班（{ws.hours}小时）！"}

@app.post("/api/work/stop")
async def work_stop(ws: WorkStop):
    s = data["work_sessions"].pop(ws.name, None)
    data["work_switch"][ws.name] = False
    save_data()
    if s:
        elapsed = time.time() - s["start_ts"]
        hours = max(0.25, min(s["hours"], elapsed / 3600))
        b = data["buildings"].get(s["building_id"])
        earn = (b.get("salary", 0) if b else 0) * hours
        data["wallets"][ws.name] = data["wallets"].get(ws.name, 0) + earn
        data["work_history"].append({"name": ws.name, "building": b.get("name", "?") if b else "?", "hours": round(hours, 2), "earn": round(earn, 1), "time": now_str()})
        data["work_history"] = data["work_history"][-200:]
        save_data()
        return {"ok": True, "msg": f"{ws.name} 下班了，赚了 {round(earn,1)} 金币！"}
    return {"ok": True, "msg": f"{ws.name} 本来就没在上班"}

@app.post("/api/work/auto")
async def work_auto(wa: WorkAuto):
    if wa.name in data["work_sessions"]:
        return {"ok": True, "msg": f"{wa.name} 已经在上班了"}
    before = len(data["work_sessions"])
    auto_start_work(wa.name)
    if len(data["work_sessions"]) > before:
        s = data["work_sessions"][wa.name]
        b = data["buildings"].get(s["building_id"])
        return {"ok": True, "msg": f"{wa.name} 已自动去 {b.get('name') if b else '?'} 上班（常驻/随机选择）！"}
    return {"ok": False, "msg": "没有可工作的公共建筑"}

@app.post("/api/work/switch")
async def work_switch(ws: WorkSwitch):
    data["work_switch"][ws.name] = ws.on
    save_data()
    return {"ok": True, "msg": f"{ws.name} 自主工作已{'开启' if ws.on else '关闭'}"}

@app.post("/api/home_jobs")
async def home_jobs(hj: HomeJobIn):
    b = data["buildings"].get(hj.building_id)
    if not b:
        raise HTTPException(404, "建筑不存在")
    name = hj.ai or hj.user
    if not name:
        raise HTTPException(400, "缺少名字")
    data["home_jobs"][name] = b.get("name")
    save_data()
    return {"ok": True}

@app.get("/api/workers")
async def workers():
    out = []
    for name, s in data["work_sessions"].items():
        b = data["buildings"].get(s["building_id"])
        left = max(0, s["start_ts"] + s["hours"] * 3600 - time.time())
        out.append({"name": name, "building": b.get("name", "?") if b else "?", "left_min": int(left // 60)})
    return {"workers": out}

@app.get("/api/mywork")
async def mywork(user: str):
    mine = [h for h in data["work_history"] if h.get("name") == user]
    return {"history": mine[-30:][::-1]}

# ========== 短信 ==========
@app.get("/api/sms")
async def get_sms(user: str):
    return {"sms": data["sms"].get(user, [])}

@app.post("/api/sms")
async def send_sms(s: SmsIn):
    if not s.text.strip():
        raise HTTPException(400, "内容不能为空")
    data["sms"].setdefault(s.to, []).append({"from": s.sender, "text": s.text[:500], "time": now_str()})
    data["sms"][s.to] = data["sms"][s.to][-100:]
    save_data()
    return {"ok": True}

# ========== 铃铛 ==========
@app.get("/api/bell")
async def get_bell(owner: str):
    return {"visits": data["visits"].get(owner, [])}

def bell_visit(owner: str, text: str):
    if not owner or owner == "system":
        return
    data["visits"].setdefault(owner, []).append({"text": text[:200], "time": now_str()})
    data["visits"][owner] = data["visits"][owner][-50:]

# ========== 备份 ==========
@app.get("/api/backup")
async def backup():
    return data

@app.get("/api/backup/list")
async def backup_list():
    files = sorted(SNAPSHOT_DIR.glob("auto_*.json"), reverse=True)
    return {"backups": [{"name": f.name, "size": f.stat().st_size} for f in files[:20]]}

@app.post("/api/restore_backup")
async def restore_backup(d: dict):
    for k, v in d.items():
        if k != "ts":
            data[k] = v
    for k, v in default_data().items():
        data.setdefault(k, v)
    sanitize_data()
    save_data()
    return {"ok": True}

# ========== 后台线程 ==========
threading.Thread(target=work_tick, daemon=True).start()

def snapshot_loop():
    while True:
        time.sleep(1800)
        snapshot()
threading.Thread(target=snapshot_loop, daemon=True).start()

load_data()

# ========== MCP ==========
mcp = FastMCP("linkong-world")

@mcp.tool()
def group_send(sender: str, content: str, room: str = ""):
    """说话。room 不填则自动发送到真人当前所在的房间（跟随）；填 'main' 发到公共大厅；也可以填任意房间名/会客厅名。"""
    target = room.strip() if room and room.strip() else data["active_room"].get("current", "main")
    if not room_exists(target):
        return f"❌ 房间「{target}」不存在。先 group_query(type=map) 看看有哪些地方，或者 type=rooms 看所有房间。"
    if target != "main" and not can_access_room(target, sender):
        return f"🔒 房间「{target}」是私密的，你没有权限。请先调用 group_access 申请。"
    msg = {"sender": sender, "content": content[:1000], "role": "assistant", "time": room_time(target)}
    data["messages"].setdefault(target, []).append(msg)
    data["active_room"]["current"] = target
    add_trail(sender, f"在 {target} 说话：{content[:50]}", room=target)
    bid = find_building_of_room(target)
    if bid and target.endswith("·会客厅"):
        owner = data["buildings"][bid].get("owner")
        if owner and owner != sender:
            bell_visit(owner, f"📣 {sender} 来你家会客厅了：{content[:60]}")
    save_data()
    return f"✅ 已在「{target}」发言。"

@mcp.tool()
def group_query(type: str, sender: str, room: str = "", building_id: str = "", count: int = 10):
    """查看一切。type：map(地图) / building(建筑详情) / room(房间+消息) / npc(NPC) / story(剧情簿) / notes(便签) / diaries(日记) / messages(消息) / members(在线) / current_room(真人在哪) / rooms(所有房间) / sms(我的私信) / mywork(我的打工记录) / workers(全城工作状态)。"""
    try:
        if type == "map":
            regions = "\n".join(f"📍 {n}（分区图:{'有' if v.get('image') else '无'}）" for n, v in data["regions"].items()) or "（还没有区域）"
            buildings = "\n".join(f"{b.get('emoji','🏠')} {b.get('name')} [{'公共' if b.get('type')=='npc' else '住宅'}·{b.get('region') or '总览区'}·{b.get('description','')[:40]}·工作:{'有' if b.get('salary',0)>0 else '无'}]" for b in data["buildings"].values()) or "（还没有建筑）"
            return f"🗺️ 临空市地图\n\n📍 区域：\n{regions}\n\n🏗️ 建筑：\n{buildings}"
        if type == "rooms":
            lst = "\n".join(f"· {n}{' 🔒' if v.get('has_password') else ''}（{v.get('creator','')}）" for n, v in data["rooms"].items())
            return f"📋 所有房间：\n{lst}"
        if type == "current_room":
            cr = data["active_room"].get("current", "main")
            return f"📍 真人现在在：{cr}\n🔍 建议 group_query(type=room, room={cr}) 看看那里的消息，或者直接 group_send 过去。"
        if type == "members":
            names = [n for n, v in data["online"].items() if time.time() - v.get("time", 0) < 45]
            return "👥 在线成员：" + ("、".join(names) if names else "（当前无人在线）")
        if type == "room":
            r = clean_room_name(room)
            if not room_exists(r):
                return f"❌ 房间「{r}」不存在"
            if not can_view_room(r, sender):
                return f"🔒 房间「{r}」是私密的，你没有权限。请先 group_access 申请。"
            desc = data["rooms"].get(r, {}).get("description", "")
            msgs = data["messages"].get(r, [])
            if not msgs:
                return f"🏠 房间「{r}」\n{('📝 '+desc+'\n') if desc else ''}💬 还没有消息，说点什么吧。"
            lines = msgs[-count:]
            txt = "\n".join(f"{m.get('time','')} {m.get('sender','?')}: {m.get('content','')}" for m in lines)
            return f"🏠 房间「{r}」\n{('📝 '+desc+'\n') if desc else ''}💬 最近消息：\n{txt}"
        if type == "building":
            b = data["buildings"].get(building_id)
            if not b:
                return "❌ 建筑不存在（building_id 从 map 里看）"
            rooms = "\n".join(f"· {x}" for x in b.get("rooms", [])) or "（无房间）"
            npcs = "\n".join(f"· {n.get('emoji','👤')} {n.get('name')}：{n.get('desc','')}" for n in data["npcs"].get(building_id, [])) or "（无NPC）"
            feats = ",".join(b.get("features", [])) or "无"
            workers = "、".join(n for n, s in data["work_sessions"].items() if s.get("building_id") == building_id) or "无人"
            return f"🏗️ {b.get('emoji')} {b.get('name')}（{'公共' if b.get('type')=='npc' else '住宅'}）\n📝 {b.get('description','')}\n👑 主人：{b.get('owner','?')}\n📢 公告：{b.get('notice','') or '无'}\n⚙️ 功能：{feats} · 时薪：{b.get('salary',0)}\n🚪 房间：\n{rooms}\n👥 NPC：\n{npcs}\n👔 正在上班：{workers}"
        if type == "npc":
            npcs = data["npcs"].get(building_id, [])
            if not npcs:
                return "（这个建筑还没有NPC）"
            return "\n".join(f"{n.get('emoji','👤')} {n.get('name')}：{n.get('desc','')}" for n in npcs)
        if type == "story":
            sts = data["stories"].get(building_id, [])
            if not sts:
                return "🎬 剧情簿还是空的"
            return "\n".join(f"{s.get('time')} {s.get('author')}: {s.get('text')}" for s in sts[-count:])
        if type == "notes":
            items = data["notes"].get(room, [])
            if not items:
                return "💌 便签墙还是空的"
            return "\n".join(f"✍️ {n.get('author')}：{n.get('text')}（{n.get('time')}）" for n in items[-count:])
        if type == "diaries":
            items = data["diaries"].get(room, [])
            if not items:
                return "📖 日记本还是空的"
            return "\n".join(f"📖 {n.get('author')}：{n.get('text')}（{n.get('time')}）" for n in items[-count:])
        if type == "messages":
            r = clean_room_name(room) if room else data["active_room"].get("current", "main")
            if not can_view_room(r, sender):
                return f"🔒 房间「{r}」是私密的，你没有权限"
            msgs = data["messages"].get(r, [])
            if not msgs:
                return f"💬 房间「{r}」还没有消息"
            return "\n".join(f"{m.get('time','')} {m.get('sender','?')}: {m.get('content','')}" for m in msgs[-count:])
        if type == "sms":
            msgs = data["sms"].get(sender, [])
            if not msgs:
                return "📭 你没有未读的短信"
            return "📩 你的私信：\n" + "\n".join(f"{m.get('time')} {m.get('from')}: {m.get('text')}" for m in msgs[-count:])
        if type == "mywork":
            mine = [h for h in data["work_history"] if h.get("name") == sender][-count:][::-1]
            if not mine:
                return "📖 你还没有打工记录"
            return "💼 我的打工记录：\n" + "\n".join(f"· {h.get('time')} {h.get('building')} {h.get('hours')}小时 +{h.get('earn',0)}金币" for h in mine)
        if type == "workers":
            if not data["work_sessions"]:
                return "👔 现在全城没人上班"
            lines = []
            for n, s in data["work_sessions"].items():
                b = data["buildings"].get(s["building_id"])
                lines.append(f"· {n} 在 {b.get('name','?') if b else '?'} 上班")
            return "👔 正在上班的人：\n" + "\n".join(lines)
        return "❓ 未知的 type，试试 map/building/room/npc/story/notes/diaries/messages/members/current_room/rooms/sms/mywork/workers"
    except Exception as e:
        return f"⚠️ 查询出错：{e}"

@mcp.tool()
def group_write(type: str, content: str, sender: str, room: str = "", building_id: str = "", note_id: str = ""):
    """写内容。type：note(贴便签,需room) / diary(写日记,需room) / story(触发剧情,需building_id) / reply(回复便签,需room和note_id) / sms(发私信,room=收件人名字) / work(去上班,room=建筑名)。"""
    try:
        if type == "note":
            if not room:
                return "❌ 贴便签需要 room 参数"
            data["notes"].setdefault(room, []).append({"author": sender, "text": content[:500], "time": now_str()})
            add_trail(sender, f"在 {room} 贴了张便签", room=room, tab="note")
            save_data()
            return f"✅ 便签已贴在「{room}」"
        if type == "diary":
            if not room:
                return "❌ 写日记需要 room 参数"
            data["diaries"].setdefault(room, []).append({"author": sender, "text": content[:1000], "time": now_str()})
            add_trail(sender, f"在 {room} 写了日记", room=room, tab="diary")
            save_data()
            return f"✅ 日记已写在「{room}」"
        if type == "story":
            if not building_id:
                return "❌ 触发剧情需要 building_id 参数"
            data["stories"].setdefault(building_id, []).append({"author": sender, "text": content[:1500], "time": now_str()})
            b = data["buildings"].get(building_id)
            add_trail(sender, f"在 {b.get('name','?') if b else '?'} 触发剧情")
            save_data()
            return f"✅ 剧情已写进「{b.get('name','?') if b else '?'}」的剧情簿"
        if type == "reply":
            items = data["notes"].get(room, [])
            idx = -1
            for i, n in enumerate(items):
                if n.get("id") == note_id or (note_id and str(i) == note_id):
                    idx = i
                    break
            if idx < 0 and note_id.isdigit():
                idx = int(note_id)
            if 0 <= idx < len(items):
                items[idx]["reply"] = {"author": sender, "text": content[:300]}
                save_data()
                return f"✅ 已回复便签"
            return "❌ 找不到那张便签（note_id 从 group_query(type=notes) 看）"
        if type == "sms":
            to = room.strip()
            if not to:
                return "❌ 发私信需要 room=收件人名字"
            data["sms"].setdefault(to, []).append({"from": sender, "text": content[:500], "time": now_str()})
            data["sms"][to] = data["sms"][to][-100:]
            save_data()
            return f"✅ 已发私信给 {to}"
        if type == "work":
            name = room.strip()
            candidates = [bid for bid, b in data["buildings"].items()
                          if b.get("type") == "npc" and "work" in b.get("features", []) and b.get("salary", 0) > 0]
            if not candidates:
                return "❌ 还没有能工作的公共建筑"
            pick = None
            if name:
                for bid in candidates:
                    if data["buildings"][bid].get("name") == name:
                        pick = bid
                        break
            if not pick:
                home = data["home_jobs"].get(sender)
                if home:
                    for bid in candidates:
                        if data["buildings"][bid].get("name") == home:
                            pick = bid
                            break
            if not pick:
                pick = random.choice(candidates)
            data["work_sessions"][sender] = {"building_id": pick, "start_ts": time.time(), "hours": 2, "started_at": now_str()}
            data["work_switch"][sender] = True
            b = data["buildings"][pick]
            add_trail(sender, f"去 {b.get('name')} 上班了")
            save_data()
            return f"✅ 已开始上班：{b.get('name')}（2小时）！"
        return "❓ 未知的 type，试试 note/diary/story/reply/sms/work"
    except Exception as e:
        return f"⚠️ 写入出错：{e}"

@mcp.tool()
def group_access(room: str, sender: str):
    """申请进入某个私密房间（真人不在那里时）。"""
    room = clean_room_name(room)
    if not room_exists(room):
        return f"❌ 房间「{room}」不存在"
    reqs = data["room_requests"].setdefault(room, [])
    if not any(q.get("applicant") == sender for q in reqs):
        reqs.append({"applicant": sender, "time": now_str()})
        save_data()
    return f"📨 已申请进入「{room}」，等主人同意（主人会在房屋的访问管理里看到）"

# ========== 挂载 MCP 服务器到 FastAPI（无重定向版） ==========
class TrailingSlashMount(Mount):
    """把 /mcp 请求自动补上尾斜杠，避免 307 重定向。"""
    async def handle(self, scope, receive, send):
        if scope.get("type") == "http":
            p = scope.get("path", "")
            if p and not p.endswith("/"):
                scope["path"] = p + "/"
                try:
                    scope["raw_path"] = scope.get("raw_path", b"") + b"/"
                except Exception:
                    pass
        await super().handle(scope, receive, send)

def mount_mcp():
    mounted = False
    mcp_app = None
    if hasattr(mcp, "streamable_http_app"):
        try:
            mcp_app = mcp.streamable_http_app()
            print("[MCP] got streamable_http_app")
        except Exception as e:
            print("[MCP] streamable_http_app fail:", e)
    if mcp_app is None and hasattr(mcp, "sse_app"):
        try:
            mcp_app = mcp.sse_app()
            print("[MCP] got sse_app (fallback)")
        except Exception as e:
            print("[MCP] sse_app fail:", e)
    if mcp_app is not None:
        try:
            app.mount("/mcp", TrailingSlashMount("/mcp", app=mcp_app))
            mounted = True
            print("[MCP] mounted at /mcp (no-redirect)")
        except Exception as e:
            print("[MCP] mount fail:", e)
    if not mounted:
        try:
            mcp.mount("/mcp", app)
            print("[MCP] used mcp.mount")
            mounted = True
        except Exception as e:
            print("[MCP] mcp.mount fail:", e)
    print("[MCP] type=", type(mcp).__name__, "attrs=", [x for x in dir(mcp) if "app" in x.lower() or "mount" in x.lower()])
mount_mcp()

def run():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

if __name__ == "__main__":
    run()
