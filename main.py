import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from database import SessionLocal, User, ExchangeRequest
from sqlalchemy.orm import Session

load_dotenv()

app = FastAPI(title="NDHU Dorm Exchange API")

# --- SMTP (從 .env 讀取) ---
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Session Middleware
secret_key = os.getenv("SECRET_KEY", "dev-secret-key-please-change-in-prod")
app.add_middleware(SessionMiddleware, secret_key=secret_key)

# OAuth
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

templates = Jinja2Templates(directory="templates")

# 宿舍性別分流
DORM_GENDER_MAP = {
    "仰山莊": ["F"], 
    "涵星一莊": ["F"], 
    "沁月莊": ["F"], 
    "行雲二莊": ["F"],
    "擷雲二莊": ["F"],  
    
    "迎曦莊": ["M"], 
    "涵星二莊": ["M"], 
    "向晴莊": ["M"], 
    "行雲一莊": ["M"],
    "擷雲一莊": ["M"]   
}

# --- 寄信功能 ---
def send_match_email(to_email: str, partner_email: str):
    """寄送媒合成功通知信給指定同學"""
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD]):
        print(f"SMTP 設定未完成，跳過寄送 Email 給 {to_email}")
        return

    subject = "【東華宿舍智慧交換系統】媒合成功通知"
    body = f"""同學您好，

恭喜您！系統已幫您成功媒合到換宿夥伴。
對方的聯絡信箱為：{partner_email}

請主動透過此信箱與對方同學聯繫，協調後續換宿細節。
祝您換宿順利！

（此為系統自動發送之信件，請勿直接回覆）
宿舍交換系統 敬上
(請注意，該系統由東華大學學生開發，不能代表國立東華大學官方)
"""

    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"成功寄送媒合通知信給：{to_email}")
    except Exception as e:
        print(f"寄送信件至 {to_email} 失敗：{e}")

def send_manual_message_email(to_email: str, sender_email: str, building: str, room: str, message: str):
    """寄送手動聯絡的通知信（官方固定格式，用戶訊息被完全隔離在指定區塊內）"""
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD]):
        print(f"SMTP 設定未完成，跳過寄送 Email 給 {to_email}")
        return

    subject = "【東華宿舍智慧交換系統】有人對您的換宿需求感興趣！"
    
    # 官方描述皆為固定不可調用的字串，使用者輸入的 message 僅作純文字嵌入
    body = f"""同學您好，

有同學在宿舍交換大廳看到了您的換宿需求。
您的刊登資料如下：
住宿莊別：{building}
住宿房號：{room}

對方的聯絡信箱為：{sender_email}

以下為該同學留下的備註訊息：
--------------------------------------------------
{message}
--------------------------------------------------

如果您亦有意願與該同學交換，請直接透過上方提供的信箱（{sender_email}）與對方聯繫。

（此為系統自動發送之信件，請勿直接回覆）
宿舍交換系統 敬上
(請注意，該系統由東華大學學生開發，不能代表國立東華大學官方)
"""
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email
    msg['Subject'] = Header(subject, 'utf-8')
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"成功寄送手動聯絡信給：{to_email}")
    except Exception as e:
        print(f"寄送信件至 {to_email} 失敗：{e}")


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Dependency to get current user
def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user

def require_auth(user: User = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="未授權的存取，請先登入")
    return user

# --- Pydantic Models ---
class CreateRequest(BaseModel):
    gender: str
    current_building: str
    current_room: int
    current_bed: str
    target_buildings: List[str]
    target_floor: Optional[int] = None
    target_room: Optional[int] = None

class PublicExchangeRequest(BaseModel):
    id: str
    gender: str
    current_building: str
    target_buildings: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class SendMessageData(BaseModel):
    message: str

# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="index.html", context={"user": user})

@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for('auth_callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as error:
        return HTMLResponse(f"<h1>登入失敗</h1><p>{error.error}</p><a href='/'>回首頁</a>", status_code=400)
    
    user_info = token.get('userinfo')
    if not user_info:
        raise HTTPException(status_code=400, detail="無法取得使用者資訊")

    email = user_info.get("email")
    name = user_info.get("name")
    
    # 建立或取得使用者
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, name=name)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 記錄登入狀態
    request.session["user_id"] = user.id
    return RedirectResponse(url="/")

@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/")


# --- API Routes ---

@app.get("/api/requests", response_model=List[PublicExchangeRequest])
def get_requests(gender: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    """取得隱私大廳清單，強制套用 PublicExchangeRequest 避免個資外洩，並依據性別過濾"""
    if not gender:
        return []
        
    requests = db.query(ExchangeRequest).filter(
        ExchangeRequest.status == "PENDING",
        ExchangeRequest.gender == gender
    ).order_by(ExchangeRequest.created_at.desc()).all()
    return requests

@app.post("/api/requests/{req_id}/message")
def send_manual_message(
    req_id: str,
    data: SendMessageData,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_auth)
):
    """手動發送訊息給感興趣的刊登者"""
    target_req = db.query(ExchangeRequest).filter(
        ExchangeRequest.id == req_id,
        ExchangeRequest.status == "PENDING"
    ).first()
    
    if not target_req:
        raise HTTPException(status_code=404, detail="找不到該請求或已被配對")
        
    if target_req.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能發送訊息給自己")
        
    # 提早把資料拿完，手動關閉資料庫連線
    target_email = target_req.user.email
    building = target_req.current_building
    room = target_req.current_room
    db.close() 

    # 加入背景任務發信（官方邏輯不可被用戶修改）
    background_tasks.add_task(
        send_manual_message_email,
        target_email,
        current_user.email,
        building,
        room,
        data.message
    )
    
    return {"message": "已成功發送訊息給對方"}

@app.post("/api/requests")
def create_exchange_request(
    req: CreateRequest, 
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(require_auth)
):
    if req.gender not in ["M", "F"]:
        raise HTTPException(status_code=400, detail="無效的性別")
        
    if req.current_building not in DORM_GENDER_MAP or req.gender not in DORM_GENDER_MAP[req.current_building]:
        raise HTTPException(status_code=400, detail="目前莊別與您的性別不符")
        
    for tb in req.target_buildings:
        if tb not in DORM_GENDER_MAP or req.gender not in DORM_GENDER_MAP[tb]:
            raise HTTPException(status_code=400, detail=f"目標莊別 {tb} 與您的性別不符")
            
    # 2. 房號驗證與樓層推導
    current_room_str = str(req.current_room)
    if not re.match(r"^\d{3}$", current_room_str):
        raise HTTPException(status_code=400, detail="房號必須為三位數字")
    if current_room_str.startswith("0"):
        raise HTTPException(status_code=400, detail="房號開頭不能為 0")
        
    current_floor = int(current_room_str[0])
    target_buildings_str = ",".join(req.target_buildings)
    
    if req.target_room is not None:
        target_room_str = str(req.target_room)
        if not re.match(r"^\d{3}$", target_room_str):
            raise HTTPException(status_code=400, detail="目標房號必須為三位數字")

    # 檢查是否已有處理中的訂單
    existing_req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id,
        ExchangeRequest.status.in_(["PENDING", "MATCHED"])
    ).first()
    
    if existing_req:
        raise HTTPException(status_code=400, detail="您已有進行中的請求，請先解除配對或刪除該請求")

    matched_candidate = None
    
    candidates = db.query(ExchangeRequest).filter(
        ExchangeRequest.status == "PENDING",
        ExchangeRequest.user_id != current_user.id,
        ExchangeRequest.gender == req.gender
    ).with_for_update(skip_locked=True).all()
    
    for candidate in candidates:
        if candidate.current_building not in req.target_buildings:
            continue
            
        if req.target_floor and candidate.current_floor != req.target_floor:
            continue
        if req.target_room and candidate.current_room != req.target_room:
            continue
            
        b_targets = candidate.target_buildings.split(",") if candidate.target_buildings else []
        if req.current_building not in b_targets:
            continue
            
        if candidate.target_floor and current_floor != candidate.target_floor:
            continue
        if candidate.target_room and req.current_room != candidate.target_room:
            continue
            
        matched_candidate = candidate
        break
        
    if matched_candidate:
        new_req = ExchangeRequest(
            user_id=current_user.id,
            gender=req.gender,
            current_building=req.current_building,
            current_floor=current_floor,
            current_room=req.current_room,
            current_bed=req.current_bed,
            target_buildings=target_buildings_str,
            target_floor=req.target_floor,
            target_room=req.target_room,
            status="MATCHED",
            matched_with_id=matched_candidate.user_id,
            matched_email=matched_candidate.user.email
        )
        db.add(new_req)
        
        # 更新 B 訂單 (狀態 MATCHED)
        matched_candidate.status = "MATCHED"
        matched_candidate.matched_with_id = current_user.id
        matched_candidate.matched_email = current_user.email
        
        db.commit()

        # 抓出需要的 Email 資料，手動關閉釋放 DB 連線
        partner_email = matched_candidate.user.email
        db.close()

        # 指派背景任務發送 Email
        background_tasks.add_task(send_match_email, current_user.email, partner_email)
        background_tasks.add_task(send_match_email, partner_email, current_user.email)

        return {"status": "MATCHED", "message": "match成功!", "matched_email": partner_email}
    else:
        # 無吻合對象，新增 A 訂單 (狀態 PENDING)
        new_req = ExchangeRequest(
            user_id=current_user.id,
            gender=req.gender,
            current_building=req.current_building,
            current_floor=current_floor,
            current_room=req.current_room,
            current_bed=req.current_bed,
            target_buildings=target_buildings_str,
            target_floor=req.target_floor,
            target_room=req.target_room,
            status="PENDING"
        )
        db.add(new_req)
        db.commit()
        
        db.close()
        return {"status": "PENDING", "message": "已刊登至大廳，等待有緣人"}

@app.post("/api/unmatch")
def unmatch_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    """解除配對並退回 PENDING 狀態 (防禦 IDOR 越權存取)"""
    user_req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id,
        ExchangeRequest.status == "MATCHED"
    ).first()
    
    if not user_req:
        raise HTTPException(status_code=400, detail="找不到符合的配對記錄，或您無權限修改")
        
    partner_id = user_req.matched_with_id
    
    partner_req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == partner_id,
        ExchangeRequest.status == "MATCHED",
        ExchangeRequest.matched_with_id == current_user.id
    ).first()
    
    user_req.status = "PENDING"
    user_req.matched_with_id = None
    user_req.matched_email = None
    
    if partner_req:
        partner_req.status = "PENDING"
        partner_req.matched_with_id = None
        partner_req.matched_email = None
        
    db.commit()
    return {"message": "已成功解除配對，重回大廳尋找"}

@app.get("/api/my_request")
def get_my_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    """取得當前使用者的請求狀態，供前端判斷顯示畫面"""
    req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id
    ).order_by(ExchangeRequest.created_at.desc()).first()
    
    if not req:
        return {"has_request": False}
        
    return {
        "has_request": True,
        "request": {
            "id": req.id,
            "status": req.status,
            "gender": req.gender,
            "matched_email": req.matched_email,
            "current_building": req.current_building,
            "current_room": req.current_room,
            "target_buildings": req.target_buildings,
            "created_at": req.created_at
        }
    }

@app.delete("/api/requests")
def delete_request(db: Session = Depends(get_db), current_user: User = Depends(require_auth)):
    """刪除當前的 PENDING 請求"""
    req = db.query(ExchangeRequest).filter(
        ExchangeRequest.user_id == current_user.id,
        ExchangeRequest.status == "PENDING"
    ).first()
    if not req:
        raise HTTPException(status_code=400, detail="找不到可刪除的請求，可能已媒合或不存在")
    db.delete(req)
    db.commit()
    return {"message": "刪除成功"}