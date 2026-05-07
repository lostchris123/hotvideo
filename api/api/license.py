"""
License 管理模块
实现简易的软件授权控制
"""
import json
import hashlib
import platform
import uuid
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
from loguru import logger


class LicenseInfo(BaseModel):
    """License 信息"""
    license_key: str
    machine_code: str
    expire_date: str
    max_users: int = 10
    features: list[str] = []
    customer_name: str = ""
    is_active: bool = True
    created_at: str = ""
    activated_at: str = ""


class LicenseManager:
    """License 管理器"""
    
    def __init__(self):
        self._license_info: Optional[LicenseInfo] = None
        self._load_license()
    
    @staticmethod
    def get_machine_code() -> str:
        """获取机器码（基于IOPlatformUUID生成，最稳定）"""
        import subprocess
        try:
            hardware_id = ""
            system = platform.system()
            
            if system == "Darwin":  # macOS
                # 优先使用 IOPlatformUUID（最稳定的硬件标识，不会因电源状态变化）
                result = subprocess.run(
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True, text=True
                )
                for line in result.stdout.split('\n'):
                    if "IOPlatformUUID" in line:
                        hardware_id = line.split('=')[-1].strip().strip('"')
                        break
                
                # 如果没找到 IOPlatformUUID，尝试使用序列号
                if not hardware_id:
                    result = subprocess.run(
                        ["system_profiler", "SPHardwareDataType"],
                        capture_output=True, text=True
                    )
                    for line in result.stdout.split('\n'):
                        if "Serial Number (system)" in line or "Hardware UUID" in line:
                            hardware_id = line.split(':')[-1].strip()
                            break
                            
            elif system == "Windows":
                # Windows使用主板UUID（比硬盘序列号更稳定）
                result = subprocess.run(
                    ["wmic", "csproduct", "get", "UUID"],
                    capture_output=True, text=True
                )
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    hardware_id = lines[1].strip()
                    # 如果获取失败，回退到硬盘序列号
                    if not hardware_id or hardware_id == "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF":
                        result = subprocess.run(
                            ["wmic", "diskdrive", "get", "serialnumber"],
                            capture_output=True, text=True
                        )
                        lines = result.stdout.strip().split('\n')
                        if len(lines) > 1:
                            hardware_id = lines[1].strip()
                    
            elif system == "Linux":
                # Linux优先使用machine-id（系统级唯一标识）
                try:
                    with open('/etc/machine-id', 'r') as f:
                        hardware_id = f.read().strip()
                except:
                    # 备用方案：使用DMI UUID
                    try:
                        with open('/sys/class/dmi/id/product_uuid', 'r') as f:
                            hardware_id = f.read().strip()
                    except:
                        pass
            
            # 如果以上方法都失败，回退到MAC地址方案
            if not hardware_id:
                mac = uuid.getnode()
                mac_str = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
                hardware_id = mac_str
            
            # 生成机器码（不再使用hostname，因为hostname可能会变）
            combined = f"{hardware_id}-{system}"
            machine_code = hashlib.sha256(combined.encode()).hexdigest()[:16].upper()
            return machine_code
            
        except Exception as e:
            logger.warning(f"获取机器码失败: {e}")
            # 回退方案：使用MAC地址
            try:
                mac = uuid.getnode()
                mac_str = ':'.join(('%012X' % mac)[i:i+2] for i in range(0, 12, 2))
                combined = f"{mac_str}-{platform.system()}"
                return hashlib.sha256(combined.encode()).hexdigest()[:16].upper()
            except:
                return "UNKNOWN"
    
    def _load_license(self):
        """从数据库加载License（优先加载有效期最长的）"""
        from crawler.models import get_user_session, LicenseRecord
        
        try:
            session = get_user_session()
            
            # 获取所有活跃的License，按过期时间降序排列（最新的在前）
            records = session.query(LicenseRecord).filter_by(is_active=True).order_by(
                LicenseRecord.expire_date.desc()
            ).all()
            
            if records:
                # 优先选择未过期的License
                today = datetime.now().date()
                valid_record = None
                
                for record in records:
                    try:
                        expire_date = datetime.strptime(record.expire_date, "%Y-%m-%d").date()
                        if expire_date >= today:
                            valid_record = record
                            break
                    except:
                        continue
                
                # 如果没有未过期的，使用第一个（可能刚过期）
                if not valid_record:
                    valid_record = records[0]
                    logger.warning(f"所有License已过期，使用最近过期的: {valid_record.expire_date}")
                
                features = json.loads(valid_record.features) if valid_record.features else []
                self._license_info = LicenseInfo(
                    license_key=valid_record.license_key,
                    machine_code=valid_record.machine_code,
                    expire_date=valid_record.expire_date,
                    max_users=valid_record.max_users or 10,
                    features=features,
                    customer_name=valid_record.customer_name or "",
                    is_active=valid_record.is_active,
                    created_at=str(valid_record.created_at) if valid_record.created_at else "",
                    activated_at=str(valid_record.activated_at) if valid_record.activated_at else "",
                )
                logger.info(f"License加载成功: {self._license_info.customer_name}, 有效期至: {self._license_info.expire_date}")
            else:
                logger.info("数据库中无有效License")
                self._license_info = None
            
            session.close()
        except Exception as e:
            logger.error(f"License加载失败: {e}")
            self._license_info = None
    
    def verify_license(self) -> tuple[bool, str]:
        """
        验证License
        返回: (是否有效, 消息)
        """
        if not self._license_info:
            return False, "未授权：请上传License文件"
        
        if not self._license_info.is_active:
            return False, "License未激活"
        
        # 验证机器码
        current_machine = self.get_machine_code()
        if self._license_info.machine_code != current_machine:
            return False, f"机器码不匹配\n当前机器码: {current_machine}\n授权机器码: {self._license_info.machine_code}"
        
        # 验证过期时间
        try:
            expire_date = datetime.strptime(self._license_info.expire_date, "%Y-%m-%d")
            if datetime.now() > expire_date:
                days_expired = (datetime.now() - expire_date).days
                return False, f"License已过期 {days_expired} 天\n过期时间: {self._license_info.expire_date}"
        except Exception as e:
            return False, f"License日期格式错误: {e}"
        
        days_left = (expire_date - datetime.now()).days
        return True, f"授权有效，剩余 {days_left} 天"
    
    def is_valid(self) -> bool:
        """检查License是否有效"""
        valid, _ = self.verify_license()
        return valid
    
    def get_license_info(self) -> Optional[dict]:
        """获取License信息"""
        if not self._license_info:
            return None
        
        valid, message = self.verify_license()
        
        days_left = 0
        if valid:
            try:
                expire_date = datetime.strptime(self._license_info.expire_date, "%Y-%m-%d")
                days_left = (expire_date - datetime.now()).days
            except:
                pass
        
        return {
            "customer_name": self._license_info.customer_name,
            "machine_code": self._license_info.machine_code,
            "expire_date": self._license_info.expire_date,
            "days_left": days_left,
            "max_users": self._license_info.max_users,
            "features": self._license_info.features,
            "is_valid": valid,
            "message": message,
        }
    
    def check_feature(self, feature: str) -> bool:
        """检查是否有某功能权限"""
        if not self.is_valid():
            return False
        if not self._license_info:
            return False
        if not self._license_info.features:
            return True
        return feature in self._license_info.features
    
    def activate_license(self, license_key: str) -> tuple[bool, str]:
        """
        激活License（输入License Key）
        License Key 由销售方提供，包含加密的授权信息
        """
        from crawler.models import get_user_session, LicenseRecord
        
        try:
            session = get_user_session()
            
            # 查找未激活的License
            record = session.query(LicenseRecord).filter_by(
                license_key=license_key,
                is_active=False
            ).first()
            
            if not record:
                # 检查是否已激活
                existing = session.query(LicenseRecord).filter_by(
                    license_key=license_key,
                    is_active=True
                ).first()
                if existing:
                    session.close()
                    return False, "License已激活"
                session.close()
                return False, "无效的License Key"
            
            # 验证机器码
            current_machine = self.get_machine_code()
            if record.machine_code != current_machine:
                session.close()
                return False, f"机器码不匹配\n当前: {current_machine}\n授权: {record.machine_code}"
            
            # 激活
            record.is_active = True
            record.activated_at = datetime.now()
            session.commit()
            session.close()
            
            # 重新加载
            self._load_license()
            
            return True, "License激活成功"
            
        except Exception as e:
            logger.error(f"激活License失败: {e}")
            return False, f"激活失败: {e}"
    
    def import_license(self, license_data: dict) -> tuple[bool, str]:
        """
        导入License（管理员操作）
        license_data 包含完整的授权信息，由销售方生成
        
        导入新License时会自动废弃旧的License
        """
        from crawler.models import get_user_session, LicenseRecord
        import json
        
        try:
            required_fields = ['license_key', 'machine_code', 'expire_date']
            for field in required_fields:
                if field not in license_data:
                    return False, f"缺少必要字段: {field}"
            
            session = get_user_session()
            
            # 检查是否已存在相同的License Key
            existing = session.query(LicenseRecord).filter_by(
                license_key=license_data['license_key']
            ).first()
            
            if existing:
                # 如果已存在，更新它
                existing.machine_code = license_data['machine_code']
                existing.expire_date = license_data['expire_date']
                existing.max_users = license_data.get('max_users', 10)
                existing.features = json.dumps(license_data.get('features', []))
                existing.customer_name = license_data.get('customer_name', '')
                existing.is_active = True
                existing.activated_at = datetime.now()
                logger.info(f"更新已存在的License: {license_data['license_key'][:16]}...")
            else:
                # 将所有旧的License设为不活跃
                session.query(LicenseRecord).update({"is_active": False})
                logger.info("已废弃所有旧License")
                
                # 创建新记录
                record = LicenseRecord(
                    license_key=license_data['license_key'],
                    machine_code=license_data['machine_code'],
                    expire_date=license_data['expire_date'],
                    max_users=license_data.get('max_users', 10),
                    features=json.dumps(license_data.get('features', [])),
                    customer_name=license_data.get('customer_name', ''),
                    is_active=True,
                    created_at=datetime.now(),
                    activated_at=datetime.now(),
                )
                session.add(record)
                logger.info(f"创建新License: {license_data['license_key'][:16]}...")
            
            session.commit()
            session.close()
            
            # 重新加载License（立即生效）
            self._load_license()
            
            if self._license_info:
                return True, f"License导入成功，有效期至 {license_data['expire_date']}"
            else:
                return False, "License导入成功但加载失败，请检查机器码"
            
        except Exception as e:
            logger.error(f"导入License失败: {e}")
            return False, f"导入失败: {e}"
    
    def reload_license(self):
        """重新加载License（用于手动刷新）"""
        self._load_license()
        return self._license_info is not None


# 全局License管理器
_license_manager: Optional[LicenseManager] = None


def get_license_manager() -> LicenseManager:
    """获取License管理器单例"""
    global _license_manager
    if _license_manager is None:
        _license_manager = LicenseManager()
    return _license_manager


def refresh_license() -> bool:
    """刷新License（重新从数据库加载）"""
    global _license_manager
    if _license_manager:
        _license_manager._load_license()
        return _license_manager.is_valid()
    return False


def require_license() -> None:
    """
    License验证依赖项
    用于需要授权的接口
    如果当前License无效，会尝试重新加载
    """
    from fastapi import HTTPException
    
    manager = get_license_manager()
    valid, message = manager.verify_license()
    
    if not valid:
        # 尝试重新加载License（可能导入了新License）
        logger.info("当前License无效，尝试重新加载...")
        manager._load_license()
        valid, message = manager.verify_license()
    
    if not valid:
        raise HTTPException(
            status_code=403,
            detail=f"授权验证失败: {message}"
        )


def get_license_limits() -> dict:
    """获取License限制信息"""
    manager = get_license_manager()
    
    if not manager.is_valid() or not manager._license_info:
        return {
            "max_users": 0,
            "features": [],
        }
    
    return {
        "max_users": manager._license_info.max_users,
        "features": manager._license_info.features,
        "expire_date": manager._license_info.expire_date,
    }