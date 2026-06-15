import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# 載入環境變數
load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("please set DATABASE_URL environment variable!")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,  # 每次從連接池拿取連線前先發送一個簡單的 query 確認連線活著
    pool_recycle=1800    # 每 30 分鐘強制重置連線，避免被伺服器單方面斷開
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    last_message_sent_at = Column(DateTime, nullable=True)
    requests = relationship("ExchangeRequest", back_populates="user")

class ExchangeRequest(Base):
    __tablename__ = "exchange_requests"
    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"))
    gender = Column(String)
    
    current_building = Column(String)
    current_floor = Column(Integer)   # 後端程式自動從 current_room 推導寫入
    current_room = Column(Integer)    # 三位數字
    current_bed = Column(String)      
    
    target_buildings = Column(String) 
    target_floor = Column(Integer, nullable=True)
    target_room = Column(Integer, nullable=True) 
    
    status = Column(String, default="PENDING")
    matched_with_id = Column(String, nullable=True)
    matched_email = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="requests")

def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()
    print("PostgreSQL (Neon) the database has been initialized。")