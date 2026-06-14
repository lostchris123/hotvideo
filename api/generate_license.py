import json
from api.license import get_license_manager, LicenseManager

# ===== 在这里填写参数 =====
LICENSE_KEY = "PH-HOTVIDEO-20260613-001"
EXPIRE_DATE = "2027-06-13"   # 格式: YYYY-MM-DD
MAX_USERS = 10
FEATURES = []
CUSTOMER_NAME = "客户名称"
USE_CURRENT_MACHINE_CODE = True   # True=自动读取当前机器码；False=使用下面手填值
MANUAL_MACHINE_CODE = ""
# ======================


def main():
    machine_code = LicenseManager.get_machine_code() if USE_CURRENT_MACHINE_CODE else MANUAL_MACHINE_CODE.strip()
    if not machine_code:
        raise SystemExit("MANUAL_MACHINE_CODE 不能为空")

    license_data = {
        "license_key": LICENSE_KEY,
        "machine_code": machine_code,
        "expire_date": EXPIRE_DATE,
        "max_users": MAX_USERS,
        "features": FEATURES,
        "customer_name": CUSTOMER_NAME,
    }

    print("准备导入 License:")
    print(license_data)
    print("\n可复制的纯JSON如下：")
    print(json.dumps(license_data, ensure_ascii=False, indent=2))

    ok, msg = get_license_manager().import_license(license_data)
    print({"ok": ok, "msg": msg})

    if ok:
        info = get_license_manager().get_license_info()
        print("当前 License 信息:")
        print(info)
        print("\n当前 License 信息（纯JSON）:")
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
