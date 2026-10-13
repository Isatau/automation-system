"""Матрица ролей и разрешений. Проверка выполняется на сервере (deps.require_perm)."""
ALL = {
 "orders:view","orders:create","orders:edit","orders:cancel","orders:transfer","orders:discount",
 "orders:price_override","orders:split",
 "payments:view","payments:create","payments:refund",
 "shifts:open","shifts:close","shifts:reopen","shifts:cashops","shifts:view",
 "hall:view","hall:manage",
 "kitchen:view","kitchen:status",
 "menu:view","menu:manage","menu:price_change",
 "warehouse:view","warehouse:receipt","warehouse:writeoff","warehouse:inventory",
 "warehouse:transfer","warehouse:negative","suppliers:manage","purchase:approve",
 "finance:view","finance:operate",
 "staff:view","staff:manage","payroll:calc","payroll:approve","time:clock",
 "guests:view","guests:manage","loyalty:adjust",
 "reports:view","audit:view","documents:upload","documents:verify",
 "admin:users","admin:settings","admin:permissions","backups:run","backups:restore",
 "notifications:view","notifications:manage",
}

ROLE_PERMS: dict[str, set[str]] = {
 "owner": set(ALL),
 "manager": ALL - {"admin:settings","admin:permissions","backups:restore"},
 "shift_admin": {"orders:view","orders:create","orders:edit","orders:cancel","orders:transfer","orders:discount",
   "orders:split","payments:view","payments:create","payments:refund","shifts:open","shifts:close","shifts:cashops",
   "shifts:view","hall:view","hall:manage","kitchen:view","menu:view","warehouse:view","guests:view","guests:manage",
   "reports:view","documents:upload","staff:view","time:clock","warehouse:writeoff","notifications:view","kitchen:status"},
 "cashier": {"orders:view","orders:create","orders:edit","orders:cancel","orders:discount","orders:split",
   "payments:view","payments:create","shifts:open","shifts:close","shifts:cashops","shifts:view","hall:view",
   "menu:view","guests:view","guests:manage","documents:upload","time:clock","notifications:view"},
 "waiter": {"orders:view","orders:create","orders:edit","orders:transfer","orders:cancel","orders:split",
   "hall:view","menu:view","guests:view","kitchen:view","time:clock","notifications:view"},
 "bartender": {"orders:view","orders:create","orders:edit","kitchen:view","kitchen:status","hall:view",
   "menu:view","time:clock","notifications:view"},
 "cook": {"kitchen:view","kitchen:status","orders:view","menu:view","time:clock","notifications:view"},
 "storekeeper": {"warehouse:view","warehouse:receipt","warehouse:writeoff","warehouse:inventory","warehouse:transfer",
   "suppliers:manage","menu:view","reports:view","time:clock","notifications:view","documents:upload"},
 "accountant": {"finance:view","finance:operate","reports:view","audit:view","warehouse:view","menu:view",
   "menu:price_change","payroll:calc","staff:view","guests:view","guests:manage","loyalty:adjust",
   "documents:verify","payments:view","orders:view","time:clock","notifications:view"},
 "tech_admin": {"admin:users","admin:settings","admin:permissions","backups:run","backups:restore","audit:view",
   "notifications:manage","shifts:view","orders:view","payments:view","reports:view","finance:view","warehouse:view",
   "menu:view","hall:view","documents:upload","time:clock"},
}

def permissions_for(user, db) -> set[str]:
    base = set(ROLE_PERMS.get(user.role, set()))
    from .models import UserPermission
    for up in db.query(UserPermission).filter_by(user_id=user.id).all():
        if up.granted:
            base.add(up.permission)
        else:
            base.discard(up.permission)
    return base

def has_perm(user, db, perm: str) -> bool:
    return perm in permissions_for(user, db)
