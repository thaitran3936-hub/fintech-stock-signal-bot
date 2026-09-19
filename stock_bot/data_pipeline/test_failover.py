from failover import FailoverManager


print("========================================")
print("        TEST FAILOVER MANAGER")
print("========================================")


manager = FailoverManager()


print("\n===== TRẠNG THÁI BAN ĐẦU =====")

print(manager.status())


print("\n===== VIETCAP HOẠT ĐỘNG =====")

manager.update_health("vietcap", True)

print("Nguồn đang dùng:", manager.get_active_source())


print("\n===== VIETCAP MẤT KẾT NỐI =====")

manager.update_health("vietcap", False)

print("Nguồn đang dùng:", manager.get_active_source())


print("\n===== DNSE HOẠT ĐỘNG =====")

manager.update_health("dnse", True)

print("Nguồn đang dùng:", manager.get_active_source())


print("\n===== VIETCAP HOẠT ĐỘNG LẠI =====")

manager.update_health("vietcap", True)

print("Nguồn đang dùng:", manager.get_active_source())


print("\n===== CẢ HAI NGUỒN MẤT =====")

manager.update_health("vietcap", False)
manager.update_health("dnse", False)

print("Nguồn đang dùng:", manager.get_active_source())


print("\n===== TRẠNG THÁI CUỐI =====")

print(manager.status())


print("\n========================================")
print("        TEST FAILOVER HOÀN TẤT")
print("========================================")