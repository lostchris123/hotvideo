"""
积分管理系统
"""
from datetime import datetime
from typing import Optional, List
from fastapi import HTTPException
from sqlalchemy.orm import Session
from crawler.models import User, CreditTransaction, TransactionType, get_user_session


# 积分消耗配置
CREDIT_COSTS = {
    TransactionType.SEARCH: 5,      # 搜索视频消耗5积分
    TransactionType.TRANSCRIPT: 10, # 提取文案消耗10积分
    TransactionType.DOWNLOAD: 2,    # 下载文案消耗2积分
    TransactionType.ANALYZE: 20,    # 爆款视频分析消耗20积分
    TransactionType.AI_CREATE: 5,   # AI创作消耗5积分
}


class CreditManager:
    """积分管理器"""
    
    @staticmethod
    def get_user_credits(user_id: int) -> int:
        """获取用户积分余额"""
        session = get_user_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return 0
            return user.credits
        finally:
            session.close()
    
    @staticmethod
    def check_credits(user_id: int, cost: int) -> bool:
        """检查用户是否有足够积分"""
        credits = CreditManager.get_user_credits(user_id)
        return credits >= cost
    
    @staticmethod
    def deduct_credits(user_id: int, amount: int, transaction_type: TransactionType, 
                       description: str = None, related_id: str = None) -> bool:
        """
        扣除用户积分
        
        Args:
            user_id: 用户ID
            amount: 扣除数量（正数）
            transaction_type: 交易类型
            description: 描述
            related_id: 关联ID
            
        Returns:
            bool: 是否扣除成功
        """
        session = get_user_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return False
            
            # 检查积分是否足够
            if user.credits < amount:
                return False
            
            # 扣除积分
            user.credits -= amount
            
            # 创建交易记录
            transaction = CreditTransaction(
                user_id=user_id,
                amount=-amount,
                type=transaction_type,
                description=description or f"消耗积分: {transaction_type.value}",
                related_id=related_id
            )
            session.add(transaction)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    @staticmethod
    def add_credits(user_id: int, amount: int, transaction_type: TransactionType = TransactionType.RECHARGE,
                    description: str = None, related_id: str = None) -> bool:
        """
        增加用户积分
        
        Args:
            user_id: 用户ID
            amount: 增加数量（正数）
            transaction_type: 交易类型
            description: 描述
            related_id: 关联ID
            
        Returns:
            bool: 是否增加成功
        """
        session = get_user_session()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return False
            
            # 增加积分
            user.credits += amount
            
            # 创建交易记录
            transaction = CreditTransaction(
                user_id=user_id,
                amount=amount,
                type=transaction_type,
                description=description or f"获得积分: {transaction_type.value}",
                related_id=related_id
            )
            session.add(transaction)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    @staticmethod
    def get_transaction_history(user_id: int, limit: int = 50, offset: int = 0) -> List[dict]:
        """获取用户交易记录"""
        session = get_user_session()
        try:
            transactions = session.query(CreditTransaction).filter(
                CreditTransaction.user_id == user_id
            ).order_by(
                CreditTransaction.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            result = []
            for t in transactions:
                result.append({
                    "id": t.id,
                    "amount": t.amount,
                    "type": t.type.value,
                    "description": t.description,
                    "related_id": t.related_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None
                })
            return result
        finally:
            session.close()
    
    @staticmethod
    def get_cost_for_action(action: str) -> int:
        """获取操作的积分消耗"""
        action_map = {
            "search": TransactionType.SEARCH,
            "transcript": TransactionType.TRANSCRIPT,
            "download": TransactionType.DOWNLOAD,
        }
        transaction_type = action_map.get(action)
        if transaction_type:
            return CREDIT_COSTS.get(transaction_type, 0)
        return 0


class CreditRequired:
    """积分检查装饰器类"""
    
    def __init__(self, action: str):
        """
        初始化
        
        Args:
            action: 操作类型 (search/transcript/download)
        """
        self.action = action
        self.cost = CreditManager.get_cost_for_action(action)
    
    def __call__(self, func):
        """装饰器实现"""
        async def wrapper(*args, **kwargs):
            from api.auth import get_current_user
            
            # 获取当前用户（从参数中查找）
            current_user = None
            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break
            
            if not current_user:
                for key, value in kwargs.items():
                    if isinstance(value, User):
                        current_user = value
                        break
            
            if not current_user:
                raise HTTPException(status_code=401, detail="未登录")
            
            # 管理员不检查积分
            from crawler.models import UserRole
            if current_user.role == UserRole.ADMIN:
                return await func(*args, **kwargs)
            
            # 检查积分
            if not CreditManager.check_credits(current_user.id, self.cost):
                action_names = {
                    "search": "搜索视频",
                    "transcript": "提取文案",
                    "download": "下载文案"
                }
                action_name = action_names.get(self.action, "此操作")
                raise HTTPException(
                    status_code=403,
                    detail=f"积分不足，无法{action_name}。当前需要 {self.cost} 积分，请前往财务页面充值。"
                )
            
            # 扣除积分
            success = CreditManager.deduct_credits(
                current_user.id, 
                self.cost,
                TransactionType(self.action),
                description=f"{action_name}消耗"
            )
            
            if not success:
                raise HTTPException(status_code=500, detail="积分扣除失败")
            
            # 执行原函数
            return await func(*args, **kwargs)
        
        return wrapper


def require_credits(action: str):
    """
    积分检查装饰器
    
    使用方式:
    @require_credits("search")
    async def search_videos(current_user: User = Depends(get_current_user)):
        ...
    """
    return CreditRequired(action)
