"""
工单管理系统 - 充值申请
"""
from datetime import datetime
from typing import Optional, List
from fastapi import HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pathlib import Path
import shutil
import uuid
from crawler.models import User, Ticket, TicketStatus, get_user_session
from config import get_settings


class TicketManager:
    """工单管理器"""
    
    @staticmethod
    def create_ticket(user_id: int, amount: int, payment_method: str, 
                      payment_proof: Optional[UploadFile] = None) -> Ticket:
        """
        创建充值工单
        
        Args:
            user_id: 用户ID
            amount: 申请积分数量
            payment_method: 支付方式 (alipay/wechat)
            payment_proof: 支付凭证图片
            
        Returns:
            Ticket: 创建的工单
        """
        session = get_user_session()
        try:
            # 验证金额
            if amount <= 0:
                raise HTTPException(status_code=400, detail="积分数量必须大于0")
            
            # 验证支付方式
            if payment_method not in ["alipay", "wechat"]:
                raise HTTPException(status_code=400, detail="不支持的支付方式")
            
            # 处理支付凭证
            proof_path = None
            if payment_proof:
                # 检查文件大小（最大10MB）
                MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
                content = payment_proof.file.read()
                if len(content) > MAX_FILE_SIZE:
                    raise HTTPException(status_code=400, detail="文件大小超过10MB限制")
                payment_proof.file.seek(0)  # 重置指针
                
                settings = get_settings()
                upload_dir = settings.DATA_DIR / "uploads" / "payment_proofs"
                upload_dir.mkdir(parents=True, exist_ok=True)
                
                # 生成唯一文件名
                file_ext = Path(payment_proof.filename).suffix
                unique_filename = f"{uuid.uuid4()}{file_ext}"
                file_path = upload_dir / unique_filename
                
                # 保存文件
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(payment_proof.file, buffer)
                
                proof_path = str(file_path.relative_to(settings.DATA_DIR))
            
            # 创建工单
            ticket = Ticket(
                user_id=user_id,
                type="recharge",
                amount=amount,
                payment_method=payment_method,
                payment_proof=proof_path,
                status=TicketStatus.PENDING
            )
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return ticket
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    @staticmethod
    def get_user_tickets(user_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
        """获取用户的工单列表"""
        session = get_user_session()
        try:
            tickets = session.query(Ticket).filter(
                Ticket.user_id == user_id
            ).order_by(
                Ticket.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            result = []
            for t in tickets:
                # 处理支付凭证URL
                payment_proof_url = None
                if t.payment_proof:
                    # 返回完整的 /uploads/ 路径供前端访问
                    proof_path = t.payment_proof
                    if not proof_path.startswith('/uploads/'):
                        if proof_path.startswith('uploads/'):
                            payment_proof_url = '/' + proof_path
                        else:
                            payment_proof_url = '/uploads/' + proof_path
                    else:
                        payment_proof_url = proof_path
                
                result.append({
                    "id": t.id,
                    "amount": t.amount,
                    "payment_method": t.payment_method,
                    "payment_proof": payment_proof_url,
                    "status": t.status.value,
                    "admin_note": t.admin_note,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None
                })
            return result
        finally:
            session.close()
    
    @staticmethod
    def get_ticket_by_id(ticket_id: int) -> Optional[Ticket]:
        """根据ID获取工单"""
        session = get_user_session()
        try:
            return session.query(Ticket).filter(Ticket.id == ticket_id).first()
        finally:
            session.close()
    
    @staticmethod
    def get_all_tickets(status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[dict]:
        """获取所有工单（管理员用）"""
        session = get_user_session()
        try:
            query = session.query(Ticket)
            
            if status:
                query = query.filter(Ticket.status == TicketStatus(status))
            
            tickets = query.order_by(
                Ticket.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            result = []
            for t in tickets:
                user = session.query(User).filter(User.id == t.user_id).first()
                
                # 处理支付凭证URL
                payment_proof_url = None
                if t.payment_proof:
                    # 返回完整的 /uploads/ 路径供前端访问
                    proof_path = t.payment_proof
                    if not proof_path.startswith('/uploads/'):
                        if proof_path.startswith('uploads/'):
                            payment_proof_url = '/' + proof_path
                        else:
                            payment_proof_url = '/uploads/' + proof_path
                    else:
                        payment_proof_url = proof_path
                
                result.append({
                    "id": t.id,
                    "user_id": t.user_id,
                    "username": user.username if user else "Unknown",
                    "email": user.email if user else "Unknown",
                    "amount": t.amount,
                    "payment_method": t.payment_method,
                    "payment_proof": payment_proof_url,
                    "status": t.status.value,
                    "admin_note": t.admin_note,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None
                })
            return result
        finally:
            session.close()
    
    @staticmethod
    def process_ticket(ticket_id: int, admin_id: int, approved: bool, 
                       admin_note: Optional[str] = None) -> bool:
        """
        处理工单（审批）
        
        Args:
            ticket_id: 工单ID
            admin_id: 管理员ID
            approved: 是否通过
            admin_note: 管理员备注
            
        Returns:
            bool: 是否处理成功
        """
        from api.credits import CreditManager
        
        session = get_user_session()
        try:
            ticket = session.query(Ticket).filter(Ticket.id == ticket_id).first()
            if not ticket:
                raise HTTPException(status_code=404, detail="工单不存在")
            
            if ticket.status != TicketStatus.PENDING:
                raise HTTPException(status_code=400, detail="工单已处理")
            
            if approved:
                # 通过：增加用户积分
                ticket.status = TicketStatus.APPROVED
                
                # 增加积分
                CreditManager.add_credits(
                    ticket.user_id,
                    ticket.amount,
                    description=f"充值工单 #{ticket.id} 通过",
                    related_id=str(ticket.id)
                )
            else:
                # 拒绝
                ticket.status = TicketStatus.REJECTED
            
            ticket.processed_by = admin_id
            ticket.admin_note = admin_note
            ticket.updated_at = datetime.now()
            
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    @staticmethod
    def get_pending_count() -> int:
        """获取待处理工单数量"""
        session = get_user_session()
        try:
            return session.query(Ticket).filter(
                Ticket.status == TicketStatus.PENDING
            ).count()
        finally:
            session.close()
